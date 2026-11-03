"""Desktop window for the researcher — open it, press Run, get the workbook.

A second frontend over :mod:`runner`, alongside the CLI in :mod:`main`. It adds
no pipeline logic of its own: it collects two overrides, calls
``runner.execute`` on a worker thread, mirrors the log into a pane, and offers
to open whatever file the run actually wrote.

Threading, which is the only genuinely tricky part here:

    Tkinter widgets may be touched from the MAIN THREAD ONLY. Calling a widget
    method from a worker corrupts the UI or crashes the interpreter, usually
    intermittently and never where you're looking. A run takes ~30s (one
    request per search term plus the politeness delay), which is far too long
    to block the event loop.

    So the worker never touches a widget. It emits log records into a
    ``queue.Queue`` (thread-safe by design) and stores its result on the
    instance. The main thread polls that queue on a ``root.after`` timer and is
    the only thing that ever writes to a widget.

Run it with pythonw.exe (see "Run Researcher.bat") so no console window opens
alongside.
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import yaml
from dotenv import load_dotenv

import runner
from runner import RunOptions

ROOT = Path(__file__).resolve().parent
POLL_MS = 100  # how often the main thread drains the log queue

# Sentinel pushed by the worker to say "no more records are coming".
_DONE = object()


class QueueLogHandler(logging.Handler):
    """Ships log records to the UI thread instead of writing them anywhere.

    The pipeline already reports itself thoroughly through ``logging``; this
    handler is what lets the window show that reporting live without the
    pipeline knowing a GUI exists.
    """

    def __init__(self, sink: queue.Queue):
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put((record.levelno, self.format(record)))
        except Exception:  # noqa: BLE001 - logging must never break the run
            pass


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def format_summary(result: runner.RunResult) -> str:
    """One-line verdict for the status bar."""
    if result.washout:
        return "No jobs found — every source returned nothing"
    return f"{result.kept} jobs  ·  {result.new_count} new  ·  {result.fetched} fetched"


class ResearcherWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.log_queue: queue.Queue = queue.Queue()
        self.result: runner.RunResult | None = None
        self.error: BaseException | None = None
        self.worker: threading.Thread | None = None

        root.title("Freelance Job Researcher")
        root.geometry("880x620")
        root.minsize(700, 500)

        # Defaults come from config.yaml every launch, so that file stays the
        # single source of truth and the window never drifts out of sync.
        try:
            config = load_config()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Config error", f"Could not read config.yaml:\n{exc}")
            raise SystemExit(1) from exc

        freelancer = (config.get("platforms") or {}).get("freelancer") or {}
        terms = freelancer.get("queries") or [freelancer.get("query", "Flutter")]

        self.lookback_var = tk.StringVar(value=str(config.get("lookback_hours", 72)))
        self.terms_var = tk.StringVar(value=", ".join(str(t) for t in terms))
        self.status_var = tk.StringVar(value="Ready")

        self._build(root)

    # --- layout ----------------------------------------------------------

    def _build(self, root: tk.Tk) -> None:
        pad = {"padx": 12, "pady": 6}

        header = ttk.Label(
            root, text="Freelance Job Researcher", font=("Segoe UI", 16, "bold")
        )
        header.pack(anchor="w", padx=12, pady=(12, 0))
        ttk.Label(
            root,
            text="Searches the configured platforms and writes a scored Excel workbook.",
            foreground="#555555",
        ).pack(anchor="w", padx=12, pady=(0, 6))

        form = ttk.LabelFrame(root, text="Settings for this run")
        form.pack(fill="x", **pad)

        ttk.Label(form, text="Lookback (hours):").grid(
            row=0, column=0, sticky="w", padx=8, pady=8
        )
        ttk.Entry(form, textvariable=self.lookback_var, width=10).grid(
            row=0, column=1, sticky="w", pady=8
        )
        ttk.Label(
            form, text="How far back a job may have been posted", foreground="#777777"
        ).grid(row=0, column=2, sticky="w", padx=8)

        ttk.Label(form, text="Search terms:").grid(
            row=1, column=0, sticky="w", padx=8, pady=8
        )
        ttk.Entry(form, textvariable=self.terms_var).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=8
        )
        form.columnconfigure(2, weight=1)
        ttk.Label(
            form,
            text="Comma-separated. These are sent to the platform, so a term "
            "that isn't searched can never appear in the sheet.",
            foreground="#777777",
            wraplength=680,
            justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 8))

        controls = ttk.Frame(root)
        controls.pack(fill="x", **pad)
        self.run_button = ttk.Button(
            controls, text="▶  RUN SEARCH", command=self.start_run, width=20
        )
        self.run_button.pack(side="left")
        self.progress = ttk.Progressbar(controls, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=12)

        ttk.Label(root, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=12
        )

        log_frame = ttk.LabelFrame(root, text="Progress")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(
            log_frame, wrap="word", height=14, state="disabled",
            background="#1e1e1e", foreground="#dcdcdc", insertbackground="#dcdcdc",
            font=("Consolas", 9), borderwidth=0,
        )
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        scroll.pack(side="right", fill="y", pady=6)
        self.log.tag_configure("WARNING", foreground="#e8b339")
        self.log.tag_configure("ERROR", foreground="#ff6b6b")
        self.log.tag_configure("OK", foreground="#7ec87e")

        actions = ttk.Frame(root)
        actions.pack(fill="x", padx=12, pady=(0, 12))
        self.open_excel_button = ttk.Button(
            actions, text="Open Excel", command=self.open_excel, state="disabled"
        )
        self.open_excel_button.pack(side="left")
        self.open_folder_button = ttk.Button(
            actions, text="Open Folder", command=self.open_folder, state="disabled"
        )
        self.open_folder_button.pack(side="left", padx=8)

    # --- running ---------------------------------------------------------

    def start_run(self) -> None:
        """Kick off a run, unless one is already in flight."""
        if self.worker and self.worker.is_alive():
            return
        try:
            lookback = int(self.lookback_var.get().strip())
            if lookback <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "Check the lookback",
                "Lookback (hours) must be a whole number greater than zero.",
            )
            return

        terms = runner.parse_terms(self.terms_var.get())
        if not terms:
            messagebox.showwarning(
                "Check the search terms",
                "Enter at least one search term, separated by commas.",
            )
            return

        self._clear_log()
        self.result = None
        self.error = None
        self.run_button.configure(state="disabled")
        self.open_excel_button.configure(state="disabled")
        self.open_folder_button.configure(state="disabled")
        self.status_var.set("Running…")
        self.progress.start(12)

        self.worker = threading.Thread(
            target=self._work, args=(lookback, terms), daemon=True
        )
        self.worker.start()
        self.root.after(POLL_MS, self._drain)

    def _work(self, lookback: int, terms: list[str]) -> None:
        """Runs OFF the main thread. Must not touch a single widget."""
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
        try:
            load_dotenv(ROOT / ".env")
            self.result = runner.execute(
                load_config(),
                RunOptions(lookback_hours=lookback, queries=terms),
                root=ROOT,
            )
        except BaseException as exc:  # noqa: BLE001 - surface it, never die silently
            self.error = exc
            logging.getLogger("researcher").error("run failed: %s", exc)
        finally:
            root_logger.removeHandler(handler)
            self.log_queue.put(_DONE)

    def _drain(self) -> None:
        """Main thread: move queued log lines into the pane, then finish up."""
        finished = False
        while True:
            try:
                item = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if item is _DONE:
                finished = True
                break
            level, message = item
            tag = (
                "ERROR" if level >= logging.ERROR
                else "WARNING" if level >= logging.WARNING
                else ""
            )
            self._append(message, tag)

        if finished:
            self._finish()
        else:
            self.root.after(POLL_MS, self._drain)

    def _finish(self) -> None:
        self.progress.stop()
        self.run_button.configure(state="normal")

        if self.error is not None:
            self.status_var.set("Run failed")
            self._append(f"\n{type(self.error).__name__}: {self.error}", "ERROR")
            messagebox.showerror("Run failed", str(self.error))
            return

        result = self.result
        if result is None:
            self.status_var.set("Run produced no result")
            return

        self.status_var.set(format_summary(result))
        if result.washout:
            self._append("\nNo jobs found. See the warnings above.", "ERROR")
            return

        self._append(f"\n✓ {format_summary(result)}", "OK")
        if result.excel_path:
            self._append(f"Saved: {result.excel_path}", "OK")
            self.open_excel_button.configure(state="normal")
            self.open_folder_button.configure(state="normal")
        for job in result.jobs[:5]:
            self._append(f"   [{job.final_score:>3}] {job.job_title[:70]}")

    # --- helpers ---------------------------------------------------------

    def _append(self, text: str, tag: str = "") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _open(self, target: Path) -> None:
        try:
            os.startfile(str(target))  # noqa: S606 - Windows shell open
        except OSError as exc:
            messagebox.showerror("Could not open", f"{target}\n\n{exc}")

    def open_excel(self) -> None:
        if self.result and self.result.excel_path:
            self._open(self.result.excel_path)

    def open_folder(self) -> None:
        if self.result and self.result.excel_path:
            self._open(self.result.excel_path.parent)


def main() -> int:
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.3)  # sane text size on high-DPI displays
    except tk.TclError:
        pass
    ResearcherWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
