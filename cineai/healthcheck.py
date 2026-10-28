#!/usr/bin/env python3
"""
Ping healthchecks.io for a scheduled job, so all-green runs don't need an email.

    from healthcheck import ping
    if not ping("smartmovie_search_devops", ok=not issues, body=report):
        send the email                     # red, OR the ping didn't get through

Pings https://hc-ping.com/<HC_PING_KEY>/<slug> (success) or …/<slug>/fail, with the
report as the POST body so it shows in the check's event log. A job that never
runs (cron broken, box down) sends no ping, and healthchecks.io alerts on that.

The project ping key lives in ~/.config/healthchecks.env (chmod 600, outside every
repo; this repo is public) as HC_PING_KEY=…, or in the environment. Returns False when
it's missing or the ping fails, so callers fall back to emailing: a green
run is never silently dropped.

CLI (for shell jobs):  healthcheck.py <slug> ok|fail  < body
"""
import os
import sys
import time
import urllib.request
from pathlib import Path

CONF = Path.home() / ".config" / "healthchecks.env"
BASE = "https://hc-ping.com"
MAX_BODY = 100_000            # healthchecks.io keeps up to 100 kB of the body


def _ping_key() -> str:
    key = os.environ.get("HC_PING_KEY", "")
    if not key and CONF.is_file():
        for ln in CONF.read_text().splitlines():
            k, _, v = ln.strip().partition("=")
            if k == "HC_PING_KEY":
                key = v.strip().strip("'\"")
    return key


def ping(slug: str, ok: bool, body: str = "") -> bool:
    key = _ping_key()
    if not key:
        print(f"[healthcheck] no HC_PING_KEY ({CONF}) — not pinged", file=sys.stderr)
        return False
    url = f"{BASE}/{key}/{slug}" + ("" if ok else "/fail")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body.encode()[:MAX_BODY], method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200:
                    return True
        except Exception as e:
            print(f"[healthcheck] {slug} ping attempt {attempt + 1} failed: {str(e)[:80]}", file=sys.stderr)
        time.sleep(2 * (attempt + 1))
    return False


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in ("ok", "fail"):
        sys.exit("usage: healthcheck.py <slug> ok|fail  < body")
    sys.exit(0 if ping(sys.argv[1], sys.argv[2] == "ok", sys.stdin.read() if not sys.stdin.isatty() else "") else 1)
