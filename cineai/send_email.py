#!/usr/bin/env python3
"""
Send an admin notification. Thin shim over the shared box-wide notifier.

Kept as a stable entry point so the existing callers (nightly_update.sh,
devops_check.py, seo_check.py) don't need to change, but it no longer holds or
reads SMTP credentials of its own. Everything is delegated to:

    server-management/scripts/notify-admin.sh

which reads creds from ~/.config/admin-notify.env (chmod 600, outside every repo)
and adds a fixed recipient, rate limiting, dedupe and a triage log.

Why: this app previously read SMTP_* from cineai/backend/.env, which docker
compose feeds to the backend container via env_file — so a container that never
sends mail was carrying working, spam-capable credentials. Those keys are gone
from backend/.env now; nothing in this repo has them.

Usage (unchanged):
    send_email.py "Subject line"            # body on stdin
    send_email.py "Subject line" "body"

Exits 0 when the notifier is missing or unconfigured, so cron never fails just
because notification is off.
"""
import os
import subprocess
import sys
from pathlib import Path

NOTIFIER = Path(
    os.environ.get(
        "ADMIN_NOTIFIER",
        Path.home() / "Code" / "server-management" / "scripts" / "notify-admin.sh",
    )
)


def main() -> int:
    subject = sys.argv[1] if len(sys.argv) > 1 else "SmartMovieSearch notification"
    body = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()

    if not NOTIFIER.exists():
        print(f"admin notifier not found at {NOTIFIER} — skipping email", file=sys.stderr)
        return 0

    # Tag the source app + severity so the shared log and subject line are useful.
    env = dict(os.environ, APP=os.environ.get("APP", "smartmoviesearch"))
    env.setdefault("SEVERITY", "error" if subject.startswith("🔴") else "info")

    r = subprocess.run([str(NOTIFIER), subject], input=body, text=True, env=env)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
