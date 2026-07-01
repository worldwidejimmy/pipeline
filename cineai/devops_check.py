#!/usr/bin/env python3
"""
Nightly DevOps health check for smartmoviesearch — emails a report to the admin.

Checks: container health/restarts, app HTTP health, disk, memory, reclaimable Docker
space, backup freshness, origin-cert expiry, and the last nightly-ingest result.
Emails via send_email.py (which no-ops if SMTP isn't configured). Run: ./devops_check.py
"""
import glob
import json
import os
import sys
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent          # cineai/
os.chdir(HERE)  # cron runs from $HOME; cd so `docker compose` finds the project
issues = []
R = []


def line(s=""):
    R.append(s)


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


line(f"SmartMovieSearch — nightly DevOps check  ({datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC})")
line("=" * 60)

# ── Containers ────────────────────────────────────────────────────────────────
line("\nContainers:")
ids = sh(["docker", "compose", "ps", "-q"]).split()
if not ids:
    issues.append("no containers running")
    line("  (none running!)")
for cid in ids:
    fmt = "{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}|{{.RestartCount}}"
    name, state, health, restarts = (sh(["docker", "inspect", "--format", fmt, cid]).split("|") + ["", "", "", ""])[:4]
    flag = "ok"
    if state != "running":
        flag = "DOWN"; issues.append(f"{name} {state}")
    elif health == "unhealthy":
        flag = "UNHEALTHY"; issues.append(f"{name} unhealthy")
    line(f"  {name.lstrip('/'):22} {state}/{health}  restarts={restarts}  [{flag}]")

# ── App health ────────────────────────────────────────────────────────────────
try:
    with urllib.request.urlopen("http://localhost:8001/api/health", timeout=8) as r:
        ok = r.status == 200
    line(f"\nApp health (/api/health): {'200 OK' if ok else r.status}")
    if not ok:
        issues.append("app health != 200")
except Exception as e:
    line(f"\nApp health: UNREACHABLE ({str(e)[:60]})"); issues.append("app unreachable")

# ── Disk / memory ─────────────────────────────────────────────────────────────
du = shutil.disk_usage("/")
pct = du.used * 100 // du.total
line(f"\nDisk /: {du.used//2**30}G used / {du.total//2**30}G ({pct}%), {du.free//2**30}G free")
if pct >= 85:
    issues.append(f"disk {pct}%")
mem = {l.split(":")[0]: l.split()[1] for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l}
avail_gb = int(mem.get("MemAvailable", "0")) / 2**20
line(f"Memory available: {avail_gb:.1f}G")
if avail_gb < 0.4:
    issues.append(f"low memory {avail_gb:.1f}G")

# ── Reclaimable docker space (informational) ──────────────────────────────────
line("\nDocker space:")
for l in sh(["docker", "system", "df", "--format", "{{.Type}}: {{.Size}} ({{.Reclaimable}} reclaimable)"]).splitlines():
    line(f"  {l}")

# ── Backup freshness ──────────────────────────────────────────────────────────
bks = sorted(glob.glob(os.path.expanduser("~/backups/smartmoviesearch/sms-*.tgz")), key=os.path.getmtime)
if bks:
    age_h = (time.time() - os.path.getmtime(bks[-1])) / 3600
    line(f"\nLatest backup: {Path(bks[-1]).name} ({age_h:.0f}h ago, {len(bks)} kept)")
    if age_h > 36:
        issues.append(f"backup stale ({age_h:.0f}h)")
else:
    line("\nBackups: NONE FOUND"); issues.append("no backups")

# ── Origin cert expiry ────────────────────────────────────────────────────────
end = sh(["openssl", "x509", "-in", "/etc/ssl/sms-origin-cert.pem", "-noout", "-enddate"])
if end.startswith("notAfter="):
    try:
        exp = datetime.strptime(end.split("=", 1)[1].strip(), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = (exp - datetime.now(timezone.utc)).days
        line(f"\nOrigin cert expires in {days} days ({exp:%Y-%m-%d})")
        if days < 30:
            issues.append(f"cert expires {days}d")
    except Exception:
        pass

# ── Last nightly ingest ───────────────────────────────────────────────────────
logs = sorted(glob.glob(str(HERE / "backend" / "data" / "nightly-logs" / "nightly-*.log")), key=os.path.getmtime)
if logs:
    txt = Path(logs[-1]).read_text(errors="ignore").replace("\r", "\n")
    added = [l for l in txt.splitlines() if "chunks added" in l or "Nothing to insert" in l]
    age_h = (time.time() - os.path.getmtime(logs[-1])) / 3600
    line(f"\nLast nightly ingest: {Path(logs[-1]).name} ({age_h:.0f}h ago)")
    line(f"  {added[-1].strip() if added else '(no clear result line)'}")
    if age_h > 30:
        issues.append(f"ingest hasn't run in {age_h:.0f}h")
else:
    line("\nNightly ingest: no logs yet")

# ── Verdict + email ───────────────────────────────────────────────────────────
line("\n" + "=" * 60)
if issues:
    line(f"⚠️  {len(issues)} issue(s): " + "; ".join(issues))
    subject = f"🔴 SmartMovieSearch devops: {len(issues)} issue(s)"
else:
    line("✅ all green")
    subject = "🟢 SmartMovieSearch devops: all green"

report = "\n".join(R)
print(report)

# Persist for triage: full report per run + append-only JSON history.
OPS = HERE / "backend" / "data" / "ops-logs"
OPS.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
(OPS / f"devops-{stamp}.log").write_text(report + "\n")
with (OPS / "devops-history.jsonl").open("a") as f:
    f.write(json.dumps({"ts": stamp, "ok": not issues, "issues": issues}) + "\n")
for old_log in sorted(OPS.glob("devops-*.log"))[:-60]:   # keep last 60 reports
    old_log.unlink(missing_ok=True)

if "--no-email" not in sys.argv:
    subprocess.run([str(HERE / "send_email.py"), subject], input=report, text=True)
