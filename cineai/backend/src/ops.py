"""Public "Evals & ops" data: the scheduled jobs and the ingest/embedding history.

Built for the site's Evals & ops panel, so it is an explicit allow-list: schedules,
last-run times, ok/issue counts, review titles (public data) and embedding counts. Never
log text, paths, hostnames, backup locations or issue details (those stay in the
emailed reports and on /admin).

Sources, all written by the jobs themselves under backend/data (gitignored):
  nightly-logs/nightly-*.log      the nightly review scrape + embed (nightly_update.sh)
  ingest-history.jsonl            exact embedding tokens/cost per run (scripts/ingest_ebert.py,
                                  from 2026-11-02; older runs are estimated from their passage count)
  ops-logs/devops-*.log, devops-history.jsonl, seo-history.jsonl
  evals/results/latest.json       the eval runner
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
EVALS = Path("/app/evals/results")

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 1536
EMBED_USD_PER_MTOK = 0.02          # OpenAI list price, per 1M input tokens
EST_TOKENS_PER_CHUNK = 135         # measured mean (cl100k) over 2,268 review passages; for pre-2026-11-02 runs

# Mirrors the server crontab (UTC). The crontab is the source of truth; keep this in step.
JOBS = [
    {"id": "evals", "time": "04:20", "days": "daily", "name": "Evals",
     "what": "Routing, retrieval, canary, cost/latency, MCP and red-team tiers; emails on a regression. "
             "Then the model gate: a newer Claude model in the tier goes live only if it passes "
             "the same evals and costs no more per question.",
     "llm": "yes: Claude answers the eval questions (the retrieval and MCP tiers use none)"},
    {"id": "judge", "time": "04:20", "days": "Sundays", "name": "Answer-quality judge",
     "what": "A stronger Claude model grades a fixed set of answers for grounding and completeness.",
     "llm": "yes: an LLM judge"},
    {"id": "ingest", "time": "08:00", "days": "daily", "name": "Review ingest",
     "what": "Finds Roger Ebert reviews published since the last run, chunks and embeds only the new ones.",
     "llm": "no: embeddings only"},
    {"id": "backup", "time": "09:00", "days": "daily", "name": "Backup",
     "what": "Encrypted backup of the app data and vector index, copied off-site.", "llm": "no"},
    {"id": "devops", "time": "09:15", "days": "daily", "name": "DevOps check",
     "what": "Containers, health, disk, backups, certificate, ingest and eval freshness; alerts on issues.",
     "llm": "no"},
    {"id": "seo", "time": "09:20", "days": "daily", "name": "SEO check",
     "what": "Sitemap, robots, titles and structured data on the live site; alerts on issues.", "llm": "no"},
]


def _iso(stamp: str) -> str | None:
    """'20261102T080001Z' → ISO 8601 UTC."""
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def _jsonl(path: Path) -> list[dict]:
    try:
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


_TITLE = re.compile(r"✓ '(.+?)' \((\d{4}|None)\)")
_CHUNKS = re.compile(r"(\d+) chunks to embed")
_TOTAL = re.compile(r"(\d+) total in Milvus")


def parse_ingest_log(text: str) -> dict:
    """One nightly log → the public facts of that run. Pure (unit-tested)."""
    titles = [f"{t} ({y})" if y != "None" else t for t, y in _TITLE.findall(text)]
    chunks = int(m.group(1)) if (m := _CHUNKS.search(text)) else 0
    total = int(m.group(1)) if (m := _TOTAL.search(text)) else None
    ok = "═ done @" in text and "Traceback" not in text
    return {"reviews": titles, "chunks": chunks, "total_rows": total, "ok": ok}


def ingest_history(limit: int = 30) -> list[dict]:
    exact = {r["ts"][:8]: r for r in _jsonl(DATA / "ingest-history.jsonl")}
    runs = []
    for f in sorted((DATA / "nightly-logs").glob("nightly-*.log"))[-limit:]:
        stamp = f.stem.removeprefix("nightly-")
        try:
            run = parse_ingest_log(f.read_text(errors="replace"))
        except OSError:
            continue
        x = exact.get(stamp[:8])
        if x and x.get("chunks") == run["chunks"]:
            tokens, estimated = x["embed_tokens"], False
        else:
            tokens, estimated = run["chunks"] * EST_TOKENS_PER_CHUNK, True
        runs.append({"at": _iso(stamp), "ok": run["ok"], "reviews": run["reviews"][:40],
                     "review_count": len(run["reviews"]), "chunks": run["chunks"],
                     "embed_tokens": tokens, "tokens_estimated": estimated and run["chunks"] > 0,
                     "cost_usd": round(tokens * EMBED_USD_PER_MTOK / 1e6, 6),
                     "total_rows": run["total_rows"]})
    return list(reversed(runs))


def _last_ingest() -> dict:
    logs = sorted((DATA / "nightly-logs").glob("nightly-*.log"))
    if not logs:
        return {}
    run = parse_ingest_log(logs[-1].read_text(errors="replace"))
    return {"at": _iso(logs[-1].stem.removeprefix("nightly-")), "ok": run["ok"],
            "note": f"{run['chunks']} new passages embedded" if run["chunks"] else "nothing new"}


def _last_from_history(name: str) -> dict:
    rows = _jsonl(DATA / "ops-logs" / name)
    if not rows:
        return {}
    r = rows[-1]
    n = len(r.get("issues", []))
    return {"at": _iso(r.get("ts", "")), "ok": n == 0, "note": "all green" if n == 0 else f"{n} issue(s)"}


_BACKUP_AGE = re.compile(r"Latest backup: \S+ \((\d+)h ago")


def _last_backup() -> dict:
    """From the latest DevOps report: when the newest backup was made (age only, no names)."""
    logs = sorted((DATA / "ops-logs").glob("devops-*.log"))
    if not logs:
        return {}
    text = logs[-1].read_text(errors="replace")
    m = _BACKUP_AGE.search(text)
    stamp = logs[-1].stem.removeprefix("devops-")
    checked = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc) if _iso(stamp) else None
    if not (m and checked):
        return {"ok": False, "note": "no recent backup found"}
    made = checked.timestamp() - int(m.group(1)) * 3600
    return {"at": datetime.fromtimestamp(made, timezone.utc).isoformat(), "ok": int(m.group(1)) <= 26,
            "note": "encrypted, copied off-site"}


def _last_evals() -> tuple[dict, dict]:
    try:
        latest = json.loads((EVALS / "latest.json").read_text())
    except (OSError, ValueError):
        return {}, {}
    tiers = latest.get("tiers") or {}
    nightly = [t for k, t in tiers.items() if k != "t3"]
    passing = sum(t.get("status") == "pass" for t in nightly)
    ev = {"at": latest.get("updated"), "ok": passing == len(nightly),
          "note": f"{passing}/{len(nightly)} tiers passing"}
    t3 = tiers.get("t3") or {}
    judge = {"at": t3.get("at"), "ok": t3.get("status") == "pass", "note": t3.get("headline", "")} if t3 else {}
    return ev, judge


def overview() -> dict:
    ev, judge = _last_evals()
    last = {"evals": ev, "judge": judge, "ingest": _last_ingest(), "backup": _last_backup(),
            "devops": _last_from_history("devops-history.jsonl"), "seo": _last_from_history("seo-history.jsonl")}
    return {
        "jobs": [{**j, "last": last.get(j["id"]) or {}} for j in JOBS],
        "ingest": {
            "wiring": {
                "source": "rogerebert.com reviews (new ones found nightly via the Internet Archive index), "
                          "plus curated Markdown guides (directors, genres, decades, music)",
                "chunking": {
                    "reviews": "LangChain RecursiveCharacterTextSplitter: 900 characters, 120 overlap, "
                               "splitting on paragraphs, then lines, then sentences",
                    "guides": "Header-aware: one passage per ## section with its ### subsections (up to "
                              "3,200 characters, so a list such as a discography stays together); bigger "
                              "sections split per ###, sub-split above 1,600 characters (800/100 overlap); "
                              "tiny sections merged; every passage keeps its heading path",
                },
                "embedding": {"model": EMBED_MODEL, "provider": "OpenAI", "dims": EMBED_DIMS,
                              "usd_per_million_tokens": EMBED_USD_PER_MTOK,
                              "query_cost_usd": round(15 * EMBED_USD_PER_MTOK / 1e6, 9)},
                "index": "Milvus 2.5: dense vectors (inner product = cosine) plus a BM25 sparse index Milvus "
                         "builds from the same text; queries run both and fuse the rankings (RRF)",
                "query_time": "Each question is embedded with the same model (one API call, about 15 "
                              "tokens) and searched in Milvus on this server. No Claude call, no per-search fee.",
            },
            "runs": ingest_history(),
        },
    }
