#!/usr/bin/env python3
"""
Eval runner. See EVALS-PLAN.md / PLATFORM-PLAN.md.

Tiers (cheapest first):
  t2  retrieval  — embeddings only, NO generation. hit@k, MRR, content coverage.
  t1  routing    — supervisor node only (one tiny LLM call per case). Accuracy +
                   confusion pairs against a hand-written golden set.
  t4  canaries   — a handful of full-pipeline questions with known-good content
                   (and known-bad phrases like "couldn't find").
  t5  cost/latency — derived from the t4 runs: p50/p95 wall time, tokens/query, $.
  nightly = t1,t2,t4 (t5 comes with t4).

Every result records the LLM model id, because the app auto-upgrades to the newest
model in its tier (src/llm.py) — a model change is exactly what these evals guard.

Usage (inside the backend container — it has Milvus, the API keys and src/):
  docker compose exec -T backend python3 /app/evals/run_evals.py --tier nightly
  ... --tier t2 --k 10       # sweep a different top_k
  ... --json                 # machine-readable

Outputs: results/<tier>.jsonl (append-only history) and results/latest.json (newest
summary per tier — what the ops email, dashboard and /admin page read).

Exits non-zero when any tier regresses against evals/baseline.json, so it can gate
CI or a timer. Evals call the graph IN-PROCESS in their own process, so they never
consume the public site's per-IP quota or GLOBAL_DAILY_CALL_CAP.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
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
LATEST = RESULTS / "latest.json"

# $/MTok (in, out) for the T5 cost estimate. Unknown models report tokens only.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0), "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0), "claude-opus-4-8": (5.0, 25.0),
}
# Phrases that mean a known-answerable canary failed — the fenced-JSON weeks read like this.
FAIL_PHRASES = ["couldn't find", "could not find", "wasn't able to find", "unable to find",
                "don't have information", "no information about"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load(name: str) -> list[dict]:
    return [json.loads(l) for l in (DATASETS / name).read_text().splitlines() if l.strip()]


def _model() -> dict:
    try:
        from src.llm import resolve
        st = resolve()
        return {"model": st["model"], "model_source": st["source"]}
    except Exception as exc:                          # never let bookkeeping fail a run
        return {"model": "unknown", "model_source": f"error: {exc}"[:80]}


# ── T2 retrieval ────────────────────────────────────────────────────────────────
def _match(actual: str, expected: str) -> bool:
    """Expected sources are matched as prefixes so an Ebert slug or a doc path both
    work, and an `amp/` mirror variant of a review still counts as the same source."""
    a = actual.replace("ebert/amp/", "ebert/")
    return a == expected or a.startswith(expected)


async def run_retrieval(k: int | None, verbose: bool) -> dict:
    from src.tools.milvus_retriever import retrieve

    cases = _load("retrieval.jsonl")
    rows, hits, rr_total = [], 0, 0.0

    for c in cases:
        t0 = time.time()
        try:
            res = await retrieve(c["q"], top_k=k)
            sources = [ch.get("source", "") for ch in res.get("chunks", [])]
            err = None
        except Exception as exc:                      # a broken retriever is a failed eval
            res, sources, err = {}, [], str(exc)[:150]

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
    return {
        "tier": "t2", "name": "retrieval", "at": _now(), "k": k or "default", "n": n,
        "hit_rate": round(hits / n, 4) if n else 0.0,
        "mrr": round(rr_total / n, 4) if n else 0.0,
        "misses": [r["id"] for r in rows if not r["hit"]],
        "content_coverage": round(sum(r["coverage"] for r in cov_rows) / len(cov_rows), 4) if cov_rows else None,
        "partial_coverage": [r["id"] for r in cov_rows if r["coverage"] < 1.0],
        "headline": f"hit@k {hits}/{n}",
        "cases": rows,
    }


# ── T1 routing ──────────────────────────────────────────────────────────────────
async def run_routing(verbose: bool) -> dict:
    from src.agents.supervisor import supervisor_route_node

    cases = _load("routing.jsonl")
    sem = asyncio.Semaphore(4)

    async def one(c: dict) -> dict:
        async with sem:
            t0 = time.time()
            try:
                got, err = (await supervisor_route_node({"question": c["q"], "history": []}))["routing"], None
            except Exception as exc:
                got, err = None, str(exc)[:150]
        ok = got == c["expect"] or got in c.get("accept", [])
        return {"id": c["id"], "q": c["q"], "expect": c["expect"], "got": got, "ok": ok,
                "strict": got == c["expect"], "holdout": bool(c.get("holdout")),
                "ms": int((time.time() - t0) * 1000), "error": err}

    rows = await asyncio.gather(*(one(c) for c in cases))
    if verbose:
        for r in rows:
            mark = "✓" if r["strict"] else ("~" if r["ok"] else "✗")
            extra = "" if r["strict"] else f"  got {r['got']}" + (f" ({r['error']})" if r["error"] else "")
            print(f"  {mark} {r['id']:20} expect {r['expect']:13}{extra}")

    n = len(rows)
    acc = lambda rs: round(sum(r["ok"] for r in rs) / len(rs), 4) if rs else None   # noqa: E731
    # Confusion as "expected→got" pairs for the failures only — the actionable part.
    confusion: dict[str, int] = {}
    for r in rows:
        if not r["ok"]:
            key = f"{r['expect']}→{r['got']}"
            confusion[key] = confusion.get(key, 0) + 1
    ok_n = sum(r["ok"] for r in rows)
    return {
        "tier": "t1", "name": "routing", "at": _now(), "n": n,
        "accuracy": acc(rows),
        "strict_accuracy": round(sum(r["strict"] for r in rows) / n, 4) if n else None,
        "holdout_accuracy": acc([r for r in rows if r["holdout"]]),
        "failures": sorted(r["id"] for r in rows if not r["ok"]),
        "confusion": dict(sorted(confusion.items(), key=lambda kv: -kv[1])),
        "headline": f"{ok_n}/{n} routed correctly",
        "cases": rows,
    }


# ── T4 canaries (+ T5 cost/latency) ────────────────────────────────────────────
def _parse_sse(ev: str) -> tuple[str, dict]:
    etype, data = "", {}
    for line in ev.splitlines():
        if line.startswith("event: "):
            etype = line[7:].strip()
        elif line.startswith("data: "):
            try:
                data = json.loads(line[6:])
            except ValueError:
                pass
    return etype, data


def check_canary(c: dict, answer: str) -> list[str]:
    """Return the list of problems with a canary answer ([] = pass). Pure — unit-tested."""
    low = answer.lower()
    problems = []
    if not answer.strip():
        return ["empty answer"]
    for p in FAIL_PHRASES:
        if p in low:
            problems.append(f'says "{p}"')
    for key, min_key in (("contain_any", "min_any"), ("contain_any_2", "min_any_2")):
        want = c.get(key) or []
        if want:
            found = [w for w in want if w.lower() in low]
            need = c.get(min_key, 1)
            if len(found) < need:
                problems.append(f"only {len(found)}/{need} of {want}")
    return problems


async def run_canaries(verbose: bool) -> tuple[dict, dict]:
    from src.main import _stream_pipeline

    cases = _load("canaries.jsonl")
    model = _model()["model"]
    rows = []
    for c in cases:                                   # sequential: honest latency numbers
        t0 = time.time()
        final, any_tok, route, err, done = [], [], None, None, {}
        try:
            async for ev in _stream_pipeline(c["q"], f"eval-{c['id']}-{int(t0)}", client_ip=None):
                et, d = _parse_sse(ev)
                if et == "token":
                    (final if d.get("is_final") else any_tok).append(d.get("content", ""))
                elif et == "routing_decision":
                    route = d.get("routing")
                elif et == "pipeline_error":
                    err = f"{d.get('code')}: {d.get('detail') or d.get('message')}"[:200]
                elif et == "done":
                    done = d
        except Exception as exc:
            err = str(exc)[:150]
        answer = "".join(final) or "".join(any_tok)
        problems = ([err] if err else []) + check_canary(c, answer)
        rows.append({"id": c["id"], "q": c["q"], "ok": not problems, "problems": problems,
                     "route": route, "ms": done.get("total_latency_ms") or int((time.time() - t0) * 1000),
                     "tokens_in": done.get("total_prompt_tokens", 0),
                     "tokens_out": done.get("total_completion_tokens", 0),
                     "answer_head": answer[:300]})
        if verbose:
            r = rows[-1]
            print(f"  {'✓' if r['ok'] else '✗'} {c['id']:16} {r['route'] or '?':12} "
                  f"{r['ms']/1000:5.1f}s {r['tokens_in']+r['tokens_out']:6} tok"
                  + ("" if r["ok"] else f"\n      {'; '.join(problems)}"))

    n = len(rows)
    ok_n = sum(r["ok"] for r in rows)
    t4 = {"tier": "t4", "name": "canaries", "at": _now(), "n": n, "pass_rate": round(ok_n / n, 4) if n else 0,
          "failures": sorted(r["id"] for r in rows if not r["ok"]),
          "headline": f"{ok_n}/{n} canaries pass", "cases": rows}

    lat = sorted(r["ms"] for r in rows)
    tin = [r["tokens_in"] for r in rows]
    tout = [r["tokens_out"] for r in rows]
    price = next((v for k, v in PRICES.items() if model.startswith(k)), None)
    cost = (statistics.mean(tin) * price[0] + statistics.mean(tout) * price[1]) / 1e6 if price and rows else None
    t5 = {"tier": "t5", "name": "cost & latency", "at": _now(), "n": n,
          "p50_ms": int(statistics.median(lat)) if lat else None,
          "p95_ms": lat[min(n - 1, int(round(0.95 * (n - 1))))] if lat else None,
          "tokens_per_query": int(statistics.mean([a + b for a, b in zip(tin, tout)])) if rows else None,
          "usd_per_query": round(cost, 5) if cost is not None else None,
          "headline": (f"p50 {statistics.median(lat)/1000:.1f}s · "
                       f"{int(statistics.mean([a + b for a, b in zip(tin, tout)]))} tok/q"
                       + (f" · ${cost:.4f}/q" if cost is not None else "")) if rows else "no runs"}
    return t4, t5


# ── Baseline gate ───────────────────────────────────────────────────────────────
def gate(res: dict, base_all: dict) -> tuple[str, list[str]]:
    """Compare one tier's result to its baseline → ("pass"|"fail"|"new", messages).
    Improvements are reported but never auto-written: bumping the baseline is a
    deliberate, reviewable commit."""
    tier = res["tier"]
    base = base_all.get(tier)
    if base is None:
        return "new", ["no baseline for this tier yet — add one to evals/baseline.json"]
    msgs, failed = [], False
    if tier == "t2":
        new = sorted(set(res["misses"]) - set(base.get("misses", [])))
        fixed = sorted(set(base.get("misses", [])) - set(res["misses"]))
        if fixed:
            msgs.append(f"newly passing: {', '.join(fixed)}")
        if new:
            failed = True
            msgs.append(f"REGRESSION — no longer retrieved: {', '.join(new)}")
        if res["mrr"] + 1e-9 < base.get("mrr", 0) - 0.05:
            failed = True
            msgs.append(f"REGRESSION — MRR {base['mrr']:.3f} → {res['mrr']:.3f}")
    elif tier == "t1":
        # Newer models can't run at temperature 0, so routing has some run-to-run
        # noise; tolerate ~3 cases of 48 before calling it a regression.
        tol = base.get("tolerance", 0.06)
        if res["accuracy"] + 1e-9 < base["accuracy"] - tol:
            failed = True
            msgs.append(f"REGRESSION — routing accuracy {base['accuracy']:.0%} → {res['accuracy']:.0%}")
        new = sorted(set(res["failures"]) - set(base.get("failures", [])))
        if new:
            msgs.append(f"newly misrouted: {', '.join(new)}")
    elif tier == "t4":
        new = sorted(set(res["failures"]) - set(base.get("failures", [])))
        if new:
            failed = True
            msgs.append(f"REGRESSION — canaries failing: {', '.join(new)}")
    elif tier == "t5":
        if base.get("p95_ms") and res.get("p95_ms") and res["p95_ms"] > base["p95_ms"] * 2:
            failed = True
            msgs.append(f"REGRESSION — p95 latency {base['p95_ms']}ms → {res['p95_ms']}ms (>2×)")
        if base.get("tokens_per_query") and res.get("tokens_per_query") and \
                res["tokens_per_query"] > base["tokens_per_query"] * 2:
            failed = True
            msgs.append(f"REGRESSION — tokens/query {base['tokens_per_query']} → {res['tokens_per_query']} (>2×)")
    return ("fail" if failed else "pass"), msgs


def record(results: list[dict], model: dict) -> None:
    """Append history per tier and merge the newest summary of each into latest.json."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    try:
        latest = json.loads(LATEST.read_text())
    except (OSError, ValueError):
        latest = {"tiers": {}}
    for r in results:
        summary = {k: v for k, v in r.items() if k != "cases"} | model
        with (RESULTS / f"{r['tier']}_{r['name'].split()[0]}.jsonl").open("a") as f:
            f.write(json.dumps(summary) + "\n")
        latest["tiers"][r["tier"]] = summary
    latest["updated"] = _now()
    latest |= model
    LATEST.write_text(json.dumps(latest, indent=2) + "\n")
    # Full per-case detail of the newest run, for the admin page's failure drill-down.
    for r in results:
        (RESULTS / f"{r['tier']}_last_cases.json").write_text(json.dumps(r.get("cases", []), indent=1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="t2", help="comma list of t1,t2,t4 (t5 rides on t4) or 'nightly'")
    ap.add_argument("--k", type=int, default=None, help="override top_k (t2 sweep)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-gate", action="store_true", help="report only, never exit non-zero")
    a = ap.parse_args()

    tiers = {"t1", "t2", "t4"} if a.tier == "nightly" else set(a.tier.split(","))
    unknown = tiers - {"t1", "t2", "t4", "t5"}
    if unknown:
        print(f"unknown/unimplemented tier(s): {', '.join(sorted(unknown))} — see EVALS-PLAN.md")
        return 2
    verbose = not a.json
    model = _model()
    base_all = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    # ONE event loop for every tier: the LLM client caches an async HTTP pool bound
    # to the loop that first used it, so a second asyncio.run() fails its first
    # call with "Connection error" (the app itself only ever has one loop).
    async def run_all() -> list[dict]:
        out: list[dict] = []
        if "t2" in tiers:
            if verbose:
                print(f"\nT2 retrieval (no LLM generation — embeddings only)"
                      f"{f', top_k={a.k}' if a.k else ''}\n")
            out.append(await run_retrieval(a.k, verbose))
        if "t1" in tiers:
            if verbose:
                print(f"\nT1 routing — supervisor only, model {model['model']}\n")
            out.append(await run_routing(verbose))
        if "t4" in tiers or "t5" in tiers:
            if verbose:
                print(f"\nT4 canaries — full pipeline, model {model['model']}\n")
            out.extend(await run_canaries(verbose))
        return out

    results = asyncio.run(run_all())

    worst = 0
    for r in results:
        r["status"], r["gate"] = gate(r, base_all)
        worst = max(worst, 1 if r["status"] == "fail" else 0)
        if verbose:
            icon = {"pass": "✓", "fail": "🔴", "new": "•"}[r["status"]]
            print(f"\n  {icon} {r['tier']} {r['name']}: {r['headline']}")
            for m in r["gate"]:
                print(f"      {m}")
    record(results, model)

    if a.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "cases"} for r in results], indent=2))
    return 0 if a.no_gate else worst


if __name__ == "__main__":
    sys.exit(main())
