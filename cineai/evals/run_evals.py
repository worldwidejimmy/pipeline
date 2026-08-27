#!/usr/bin/env python3
"""
Eval runner. See EVALS-PLAN.md.

T2 (retrieval) scores the retriever WITHOUT generating a single token: embed the
query, inspect what comes back. That makes it ~free (embeddings only), fully
deterministic, and the fastest way to catch the failure mode most likely to recur
as the corpus grows — a relevant document not making the top-k window.

Metrics per case:
  hit@k   — did an expected source appear in the top k at all?
  rank    — 1-based position of the first expected source (lower is better)
  MRR     — mean reciprocal rank across cases

Usage (run inside the backend container, which has Milvus + the embedding key):
  docker compose exec -T backend python3 /app/evals/run_evals.py --tier t2
  ... --tier t2 --k 10          # sweep a different top_k
  ... --tier t2 --json          # machine-readable, for history/CI

Exits non-zero when a case regresses against evals/baseline.json, so this can gate
CI. Deliberately calls retrieve() directly rather than /api/query, so it never
consumes the app's GLOBAL_DAILY_CALL_CAP.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

HERE = Path(__file__).resolve().parent
DATASETS = HERE / "datasets"
RESULTS = HERE / "results"
BASELINE = HERE / "baseline.json"


def _match(actual: str, expected: str) -> bool:
    """Expected sources are matched as prefixes so an Ebert slug or a doc path both
    work, and an `amp/` mirror variant of a review still counts as the same source."""
    a = actual.replace("ebert/amp/", "ebert/")
    return a == expected or a.startswith(expected)


async def run_retrieval(k: int | None, verbose: bool) -> dict:
    from src.tools.milvus_retriever import retrieve

    cases = [json.loads(l) for l in (DATASETS / "retrieval.jsonl").read_text().splitlines() if l.strip()]
    rows, hits, rr_total = [], 0, 0.0

    for c in cases:
        t0 = time.time()
        try:
            res = await retrieve(c["q"], top_k=k)
            sources = [ch.get("source", "") for ch in res.get("chunks", [])]
            err = None
        except Exception as exc:                      # a broken retriever is a failed eval
            sources, err = [], str(exc)[:150]

        rank = next((i + 1 for i, s in enumerate(sources)
                     for e in c["expect_sources"] if _match(s, e)), None)
        hit = rank is not None
        hits += hit
        rr_total += (1 / rank) if rank else 0.0

        # CONTENT COVERAGE — the metric that actually catches the Police-class bug.
        # hit@k only proves the right DOCUMENT surfaced; the real failure was the
        # document surfacing while the chunks holding the answer did not. Check the
        # required facts against the concatenated retrieved text.
        want = c.get("expect_content") or []
        blob = " ".join(ch.get("text", "") for ch in res.get("chunks", [])).lower() if not err else ""
        missing = [w for w in want if w.lower() not in blob]
        covered = (len(want) - len(missing)) / len(want) if want else None

        rows.append({"id": c["id"], "q": c["q"], "hit": hit, "rank": rank,
                     "expect": c["expect_sources"], "got": sources[:5],
                     "coverage": covered, "missing_content": missing,
                     "ms": int((time.time() - t0) * 1000), "error": err})
        if verbose:
            mark = "✓" if hit and not missing else ("~" if hit else "✗")
            pos = f"rank {rank}" if rank else "MISS"
            cov = f" cov {covered:.0%}" if covered is not None else ""
            print(f"  {mark} {c['id']:22} {pos:8}{cov:9} {c['expect_sources'][0][:38]}")
            if missing:
                print(f"      retrieved the doc but MISSING: {', '.join(missing)}")
            if not hit:
                # Show what DID come back — that's the actionable part of a miss.
                print(f"      got: {', '.join(s[:40] for s in sources[:3]) or '(nothing)'}")
                if err:
                    print(f"      error: {err}")

    n = len(cases)
    cov_rows = [r for r in rows if r["coverage"] is not None]
    partial = [r["id"] for r in cov_rows if r["coverage"] is not None and r["coverage"] < 1.0]
    return {
        "tier": "t2_retrieval",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "k": k or "default",
        "n": n,
        "hit_rate": round(hits / n, 4) if n else 0.0,
        "mrr": round(rr_total / n, 4) if n else 0.0,
        "misses": [r["id"] for r in rows if not r["hit"]],
        "content_coverage": round(sum(r["coverage"] for r in cov_rows) / len(cov_rows), 4) if cov_rows else None,
        "partial_coverage": partial,
        "cases": rows,
    }


def compare_baseline(result: dict) -> int:
    """Regression gate. A NEW miss fails; an improvement is reported and the baseline
    is left alone (bumping it is a deliberate, reviewable commit)."""
    if not BASELINE.exists():
        print("\n  no baseline yet — writing one. Review and commit evals/baseline.json.")
        BASELINE.write_text(json.dumps(
            {"hit_rate": result["hit_rate"], "mrr": result["mrr"],
             "misses": sorted(result["misses"])}, indent=2) + "\n")
        return 0

    base = json.loads(BASELINE.read_text())
    new_misses = sorted(set(result["misses"]) - set(base.get("misses", [])))
    fixed = sorted(set(base.get("misses", [])) - set(result["misses"]))

    if fixed:
        print(f"  ✅ newly passing: {', '.join(fixed)}")
    if new_misses:
        print(f"  🔴 REGRESSION — these used to retrieve and no longer do: {', '.join(new_misses)}")
        return 1
    if result["mrr"] + 1e-9 < base.get("mrr", 0) - 0.05:
        print(f"  🔴 REGRESSION — MRR dropped {base['mrr']:.3f} → {result['mrr']:.3f}")
        return 1
    print(f"  ✓ no regression (baseline hit_rate {base.get('hit_rate')}, mrr {base.get('mrr')})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="t2")
    ap.add_argument("--k", type=int, default=None, help="override top_k (sweep)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-gate", action="store_true", help="report only, never exit non-zero")
    a = ap.parse_args()

    if "t2" not in a.tier:
        print(f"tier {a.tier} not implemented yet — see EVALS-PLAN.md")
        return 0

    verbose = not a.json
    if verbose:
        print(f"\nT2 retrieval evals (no LLM generation — embeddings only)"
              f"{f', top_k={a.k}' if a.k else ''}\n")
    res = asyncio.run(run_retrieval(a.k, verbose))

    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"\n  hit@k {res['hit_rate']:.0%}  ({res['n'] - len(res['misses'])}/{res['n']})"
              f"   MRR {res['mrr']:.3f}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / "t2_retrieval.jsonl").open("a") as f:
        f.write(json.dumps({k: v for k, v in res.items() if k != "cases"}) + "\n")

    rc = compare_baseline(res)
    return 0 if a.no_gate else rc


if __name__ == "__main__":
    sys.exit(main())
