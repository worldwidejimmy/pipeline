#!/usr/bin/env python3
"""
Send an admin notification email using SMTP creds from cineai/backend/.env.

Usage:
    send_email.py "Subject line"            # body on stdin
    send_email.py "Subject line" "body"

Reads SMTP_HOST/PORT/USER/PASS/FROM and ADMIN_EMAIL (falls back to SMTP_TO) from
cineai/backend/.env (or the process environment). If SMTP isn't configured it
prints a notice and exits 0 — so cron jobs never fail just because email is off.
"""
import os
import ssl
import sys
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path


def _load_env(p: Path) -> dict:
    env = {}
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = _load_env(Path(__file__).resolve().parent / "backend" / ".env")


def g(key, default=None):
    return os.environ.get(key) or ENV.get(key, default)


def main() -> int:
    host = g("SMTP_HOST")
    if not host:
        print("SMTP not configured (no SMTP_HOST) — skipping email")
        return 0
    port = int(g("SMTP_PORT", "587"))
    user, pw = g("SMTP_USER"), g("SMTP_PASS")
    frm = g("SMTP_FROM", user or "noreply@smartmoviesearch.com")
    to = g("ADMIN_EMAIL") or g("SMTP_TO")
    if not to:
        print("no ADMIN_EMAIL / SMTP_TO — skipping email")
        return 0

    subject = sys.argv[1] if len(sys.argv) > 1 else "SmartMovieSearch notification"
    body = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = frm
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)

    try:
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            smtp = smtplib.SMTP(host, port, timeout=30)
            smtp.starttls(context=ssl.create_default_context())
        with smtp:
            if user and pw:
                smtp.login(user, pw)
            smtp.sendmail(frm, [to], msg.as_string())
        print(f"emailed '{subject}' -> {to}")
        return 0
    except Exception as exc:
        print(f"email failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
