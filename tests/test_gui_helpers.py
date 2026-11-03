"""Tests for the GUI's non-widget logic.

Tk widgets aren't unit-tested here — driving a real event loop in CI buys
little and breaks often, and the window is verified by launching it. What IS
tested is the part that would fail silently and dangerously: the thread-safe
handoff of log records from the worker to the UI, which is the only place this
program can corrupt itself.
"""
from __future__ import annotations

import logging
import queue

import gui
import runner


def test_log_handler_hands_records_to_the_queue():
    """The worker thread never touches a widget — it posts here instead."""
    sink: queue.Queue = queue.Queue()
    handler = gui.QueueLogHandler(sink)
    handler.setFormatter(logging.Formatter("%(message)s"))

    log = logging.getLogger("test.gui.handoff")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        log.info("fetched 294 jobs")
        log.warning("mostaql: blocked")
    finally:
        log.removeHandler(handler)

    level_a, text_a = sink.get_nowait()
    level_b, text_b = sink.get_nowait()
    assert (level_a, text_a) == (logging.INFO, "fetched 294 jobs")
    assert (level_b, text_b) == (logging.WARNING, "mostaql: blocked")
    assert sink.empty()


def test_log_handler_never_raises_into_the_pipeline():
    """Logging must not be able to take a run down."""
    class Exploding(queue.Queue):
        def put(self, *a, **kw):
            raise RuntimeError("queue is gone")

    handler = gui.QueueLogHandler(Exploding())
    handler.setFormatter(logging.Formatter("%(message)s"))
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)

    handler.emit(record)  # must not raise


def test_summary_line_reports_counts():
    result = runner.RunResult(jobs=[], fetched=294)
    result.jobs = [object()] * 178  # type: ignore[list-item]
    result.new_count = 169
    assert gui.format_summary(result) == "178 jobs  ·  169 new  ·  294 fetched"


def test_summary_line_calls_out_a_washout():
    """The window must not show a reassuring number when nothing was found."""
    result = runner.RunResult(jobs=[], fetched=0)
    assert "No jobs found" in gui.format_summary(result)


def test_full_run_cycle_updates_ui_from_the_worker(monkeypatch, tmp_path):
    """End-to-end of the threading path, without a visible window.

    Exercises start_run -> worker thread -> queue -> main-thread drain ->
    _finish. This is the one place the program can corrupt itself (a widget
    touched off the main thread), so it gets a real Tk instance and a real
    thread rather than mocks.
    """
    import logging
    import tkinter as tk

    fake_xlsx = tmp_path / "freelance_jobs_test.xlsx"
    fake_xlsx.write_text("not really a workbook")

    def fake_execute(config, opts, root=None):
        # Prove the worker's overrides came from the widgets...
        assert opts.lookback_hours == 99
        assert opts.queries == ["Flutter", "Rust"]
        # ...and that pipeline logging reaches the pane.
        logging.getLogger("researcher").info("fetched 7 jobs")
        result = runner.RunResult(fetched=7, new_count=2)
        result.jobs = [
            runner.JobPosting(platform="freelancer", job_title="A job", job_url="u")
        ]
        result.excel_path = fake_xlsx
        return result

    monkeypatch.setattr(runner, "execute", fake_execute)

    root = tk.Tk()
    root.withdraw()
    win = gui.ResearcherWindow(root)
    win.lookback_var.set("99")
    win.terms_var.set("Flutter, Rust")

    seen = {}

    def poll():
        # Wait for the worker to finish, then capture the settled UI state.
        if win.worker and win.worker.is_alive():
            root.after(50, poll)
            return
        if win.result is None and win.error is None:
            root.after(50, poll)
            return
        root.after(300, capture)

    def capture():
        seen["status"] = win.status_var.get()
        seen["run_state"] = str(win.run_button["state"])
        seen["excel_state"] = str(win.open_excel_button["state"])
        seen["log"] = win.log.get("1.0", "end")
        root.destroy()

    win.start_run()
    root.after(50, poll)
    root.mainloop()

    assert win.error is None, f"worker raised: {win.error}"
    assert seen["status"] == "1 jobs  ·  2 new  ·  7 fetched"
    assert seen["run_state"] == "normal", "Run must be re-enabled after finishing"
    assert seen["excel_state"] == "normal", "Open Excel must unlock once a file exists"
    assert "fetched 7 jobs" in seen["log"], "pipeline logging must reach the pane"
    assert "A job" in seen["log"], "top matches should be listed"
