"""Outgoing email.

MAIL_BACKEND=log   (default) writes messages to the application log. Use it for development.
MAIL_BACKEND=smtp  sends through SMTP_HOST / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD.
MAIL_BACKEND=memory keeps messages in `outbox` (used by the tests).
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from flask import current_app

outbox: list[EmailMessage] = []


def send(to: str, subject: str, body: str) -> None:
    cfg = current_app.config
    msg = EmailMessage()
    msg["From"] = cfg.get("MAIL_FROM") or "PORTIA SkillBridge <no-reply@localhost>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    backend = cfg.get("MAIL_BACKEND", "log")
    if backend == "memory":
        outbox.append(msg)
    elif backend == "smtp":
        with smtplib.SMTP(cfg["SMTP_HOST"], int(cfg.get("SMTP_PORT", 587)), timeout=15) as smtp:
            if cfg.get("SMTP_USE_TLS", True):
                smtp.starttls()
            if cfg.get("SMTP_USERNAME"):
                smtp.login(cfg["SMTP_USERNAME"], cfg["SMTP_PASSWORD"])
            smtp.send_message(msg)
    else:
        current_app.logger.info("EMAIL (not sent, MAIL_BACKEND=log) to=%s subject=%s\n%s", to, subject, body)
