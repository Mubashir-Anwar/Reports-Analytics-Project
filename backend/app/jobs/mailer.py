"""E-mail for scheduled reports (a report server: Notifications). Configured only by environment variables:

SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_SECURITY = starttls | ssl | none."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

MEDIA = {"pdf": ("application", "pdf"), "csv": ("text", "csv"),
         "xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
MAX_ATTACHMENT = 15 * 1024 * 1024


def configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("SMTP_FROM", "").strip())


def send(recipients: list[str], subject: str, text: str, attachment: tuple[str, bytes] | None = None) -> None:
    """Send one message to all recipients (Bcc, so they do not see each other). Raises on failure."""
    if not configured():
        raise RuntimeError("e-mail is not configured on the server (SMTP_HOST, SMTP_FROM)")
    message = EmailMessage()
    message["From"] = os.getenv("SMTP_FROM", "").strip()
    message["To"] = message["From"]
    message["Bcc"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(text)
    if attachment is not None:
        name, body = attachment
        if len(body) <= MAX_ATTACHMENT:
            main, sub = MEDIA.get(name.rsplit(".", 1)[-1], ("application", "octet-stream"))
            message.add_attachment(body, maintype=main, subtype=sub, filename=name)
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    security = os.getenv("SMTP_SECURITY", "starttls").strip().lower()
    user, password = os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", "")
    context = ssl.create_default_context()
    if security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    with server:
        if security == "starttls":
            server.starttls(context=context)
        if user:
            server.login(user, password)
        server.send_message(message)
