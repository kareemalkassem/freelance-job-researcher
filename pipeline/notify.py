"""Optional alerts when a genuinely good job shows up.

The spreadsheet is the deliverable, but a 95-scoring Flutter job posted at 09:00
is worth knowing about before you next open the file. This sends a short digest
of the top matches over Telegram, email, or both.

Off by default and threshold-gated: it only fires for jobs scoring at or above
``notify.min_score`` that are *new* this run, so it can never turn into a daily
notification of the same jobs. Failures are logged and swallowed — a dead SMTP
server must not cost you the spreadsheet.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import httpx

from models.job_posting import JobPosting

log = logging.getLogger("researcher.notify")

_TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 20.0


def select(jobs: list[JobPosting], config: dict) -> list[JobPosting]:
    """The jobs worth interrupting you for: new, high-scoring, capped in count."""
    conf = config.get("notify") or {}
    min_score = int(conf.get("min_score", 80))
    limit = int(conf.get("max_items", 5))
    new_only = bool(conf.get("new_only", True))

    picked = [
        j for j in jobs
        if j.final_score >= min_score and (j.is_new or not new_only)
    ]
    return picked[:limit]


def render(jobs: list[JobPosting]) -> str:
    """A compact plain-text digest that reads fine in Telegram and email."""
    lines = [f"{len(jobs)} new freelance match{'es' if len(jobs) != 1 else ''}:", ""]
    for job in jobs:
        lines.append(f"[{job.final_score}] {job.job_title}")
        detail = " · ".join(
            p for p in (job.platform, job.budget_amount, job.posted_time_ago) if p
        )
        if detail:
            lines.append(f"    {detail}")
        if job.fit_reason:
            lines.append(f"    {job.fit_reason}")
        if job.red_flags:
            lines.append(f"    ⚠ {job.red_flags}")
        lines.append(f"    {job.job_url}")
        lines.append("")
    return "\n".join(lines).strip()


def send_telegram(text: str, config: dict) -> bool:
    """Post the digest to a Telegram chat via a bot token."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "") or str(config.get("chat_id", ""))
    if not token or not chat_id:
        log.warning("notify: telegram enabled but TELEGRAM_BOT_TOKEN/CHAT_ID not set")
        return False
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(
            _TELEGRAM_URL.format(token=token),
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
        )
        resp.raise_for_status()
    return True


def send_email(text: str, config: dict) -> bool:
    """Send the digest over SMTP using stdlib only."""
    host = config.get("smtp_host", "")
    to_addr = config.get("to", "")
    from_addr = config.get("from", "") or to_addr
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    if not host or not to_addr:
        log.warning("notify: email enabled but smtp_host/to missing in config")
        return False

    msg = EmailMessage()
    msg["Subject"] = config.get("subject", "New freelance matches")
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(text)

    port = int(config.get("smtp_port", 587))
    if bool(config.get("use_ssl", False)):
        server = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT)
    else:
        server = smtplib.SMTP(host, port, timeout=_TIMEOUT)
        if bool(config.get("use_tls", True)):
            server.starttls()
    with server:
        if user and password:
            server.login(user, password)
        server.send_message(msg)
    return True


def dispatch(jobs: list[JobPosting], config: dict, senders: dict | None = None) -> list[str]:
    """Send the digest over every enabled channel. Returns channels that fired.

    ``senders`` is injectable so tests verify selection and rendering without
    touching the network.
    """
    conf = config.get("notify") or {}
    if not conf.get("enabled", False):
        return []

    picked = select(jobs, config)
    if not picked:
        return []

    text = render(picked)
    available = senders or {"telegram": send_telegram, "email": send_email}
    fired: list[str] = []

    for channel in conf.get("channels", []) or []:
        sender = available.get(str(channel).lower())
        if sender is None:
            log.warning("notify: unknown channel %r", channel)
            continue
        try:
            if sender(text, conf.get(str(channel).lower(), {}) or {}):
                fired.append(str(channel))
        except Exception as exc:  # a broken channel must not break the run
            log.warning("notify: %s failed — %s", channel, exc)
    return fired
