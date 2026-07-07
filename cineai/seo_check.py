#!/usr/bin/env python3
"""
Daily technical-SEO audit for smartmoviesearch — emails a report to the admin.

Fetches the live site (through Cloudflare) and checks the on-page/indexability
signals crawlers care about: HTTP status, title/description (+ lengths), Open Graph
+ Twitter cards, canonical, JSON-LD, and whether robots.txt / sitemap.xml are REAL
(not the SPA fallback). Emails via send_email.py; persists to backend/data/ops-logs.
Run: ./seo_check.py [--no-email]
"""
import glob
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SITE = os.environ.get("SEO_SITE", "https://smartmoviesearch.com")
HERE = Path(__file__).resolve().parent
issues, warns, R = [], [], []


def line(s=""):
    R.append(s)


def fetch(path):
    req = urllib.request.Request(SITE + path, headers={"User-Agent": "SmartMovieSearch-SEO-Check/1.0"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode("utf-8", "ignore"), (time.time() - t0) * 1000


line(f"SmartMovieSearch — daily SEO check  ({datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC})")
line("=" * 60)

# ── Homepage ──────────────────────────────────────────────────────────────────
try:
    status, html, ms = fetch("/")
except Exception as e:
    line(f"\nHomepage UNREACHABLE: {str(e)[:80]}")
    issues.append("homepage unreachable")
    html, status, ms = "", 0, 0

if html:
    line(f"\nHomepage: HTTP {status} in {ms:.0f}ms")
    if status != 200:
        issues.append(f"homepage {status}")
    if ms > 2000:
        warns.append(f"slow homepage {ms:.0f}ms")

    def find(pat):
        m = re.search(pat, html, re.I)
        return m.group(1).strip() if m else None

    title = find(r"<title>([^<]*)</title>")
    desc = find(r'<meta\s+name="description"\s+content="([^"]*)"')

    def report(label, val, lo=None, hi=None, required=True):
        if not val:
            (issues if required else warns).append(f"missing {label}")
            line(f"  ✗ {label}: MISSING")
        else:
            note = f" ({len(val)} chars)" if lo else ""
            flag = ""
            if lo and not (lo <= len(val) <= hi):
                warns.append(f"{label} length {len(val)}")
                flag = f"  ⚠ ideal {lo}-{hi}"
            line(f"  ✓ {label}{note}{flag}")

    line("\nMeta tags:")
    report("title", title, 30, 60)
    report("meta description", desc, 120, 160)
    for prop in ("og:title", "og:description", "og:url"):
        report(prop, find(rf'<meta\s+property="{prop}"\s+content="([^"]*)"'))
    report("og:image", find(r'<meta\s+property="og:image"'), required=False)
    report("twitter:card", find(r'<meta\s+name="twitter:card"'), required=False)
    report("canonical", find(r'<link\s+rel="canonical"'), required=False)
    report("JSON-LD schema", find(r'application/ld\+json'), required=False)
    report('<html lang>', find(r'<html[^>]*\slang="([^"]*)"'), required=False)

# ── robots.txt (real, not SPA fallback) ───────────────────────────────────────
line("\nrobots.txt:")
try:
    st, body, _ = fetch("/robots.txt")
    is_real = st == 200 and "<html" not in body.lower() and re.search(r"user-agent", body, re.I)
    if is_real:
        line("  ✓ present" + ("  (declares Sitemap)" if "sitemap" in body.lower() else "  ⚠ no Sitemap: line"))
        if "sitemap" not in body.lower():
            warns.append("robots.txt has no Sitemap:")
    else:
        line("  ✗ MISSING (URL falls through to the SPA — served app HTML)")
        issues.append("no real robots.txt")
except Exception:
    line("  ✗ error fetching"); issues.append("robots.txt error")

# ── sitemap.xml (real XML with URLs) ──────────────────────────────────────────
line("\nsitemap.xml:")
try:
    st, body, _ = fetch("/sitemap.xml")
    locs = len(re.findall(r"<loc>", body))
    if st == 200 and "<urlset" in body and locs:
        line(f"  ✓ present ({locs} URLs)")
    else:
        line("  ✗ MISSING (no valid <urlset>/<loc> — SPA fallback)")
        issues.append("no real sitemap.xml")
except Exception:
    line("  ✗ error fetching"); issues.append("sitemap error")

# ── Verdict + persist + email ─────────────────────────────────────────────────
line("\n" + "=" * 60)
if issues:
    subject = f"🟡 SmartMovieSearch SEO: {len(issues)} issue(s), {len(warns)} warning(s)"
    line(f"Issues:   {'; '.join(issues)}")
    if warns:
        line(f"Warnings: {'; '.join(warns)}")
elif warns:
    subject = f"🟢 SmartMovieSearch SEO: ok ({len(warns)} warning(s))"
    line(f"Warnings: {'; '.join(warns)}")
else:
    subject = "🟢 SmartMovieSearch SEO: all good"
    line("✅ no issues")

report_txt = "\n".join(R)
print(report_txt)

OPS = HERE / "backend" / "data" / "ops-logs"
OPS.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
(OPS / f"seo-{stamp}.log").write_text(report_txt + "\n")
with (OPS / "seo-history.jsonl").open("a") as f:
    f.write(json.dumps({"ts": stamp, "issues": issues, "warnings": warns}) + "\n")
for old in sorted(OPS.glob("seo-*.log"))[:-60]:
    old.unlink(missing_ok=True)

if "--no-email" not in sys.argv:
    import subprocess
    subprocess.run([str(HERE / "send_email.py"), subject], input=report_txt, text=True)
