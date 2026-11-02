"""Experiment (2026-11-02): can a cheap score tell "the knowledge base has useful material
for this question" from "it doesn't"? Verdict and numbers: README.md.

Why it's needed: the hybrid search's RRF score depends only on rank (1st place scores
~0.033 whether it's a perfect match or the least-bad of a bad lot), so it can't gate
relevance. Candidate signals, all free (no LLM call):
  dense  max inner-product similarity of the query embedding to its top-6 passages
         (text-embedding-3-small vectors are unit length, so this is cosine)
  bm25   top BM25 score (keyword overlap; scale depends on the query)

Labelled set: every T2 retrieval case (answerable by construction) plus
datasets/kb_gate.jsonl (natural answerable questions and unanswerable ones: off-topic,
live logistics, celebrity trivia, unreleased films). A threshold is chosen on the
tuning cases only and reported on the held-out ones.

Run (read-only; embeddings only, about $0.0001):
  docker compose exec -T backend python3 /app/evals/experiments/kb_gate_experiment.py
GRADE=1 also runs the LLM grader (src/agents/kb_lookup.py) on every case in the unsure
band and scores it against the labels (~$0.005).
"""
import asyncio, json, os, sys
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/evals")
import src.tracing  # noqa: F401  (keeps env auto-tracing off)
from run_evals import _load
from src.config import get_config
from src.tools.milvus_retriever import _get_client, _get_embeddings

K = 6


def cases() -> list[dict]:
    out = [{"id": c["id"], "q": c["q"], "label": "relevant", "holdout": bool(c.get("holdout"))}
           for c in _load("retrieval.jsonl")]
    out += [{"id": c["id"], "q": c["q"], "label": c["label"], "holdout": bool(c.get("holdout"))}
            for c in _load("kb_gate.jsonl")]
    return out


async def signals(q: str) -> dict:
    cfg, client = get_config(), _get_client()
    vec = await _get_embeddings().aembed_query(q)
    dense = client.search(cfg.milvus_collection, data=[vec], anns_field="dense_vector",
                          search_params={"metric_type": "IP"}, limit=K, output_fields=["source"])[0]
    sparse = client.search(cfg.milvus_collection, data=[q], anns_field="sparse_vector",
                           search_params={"metric_type": "BM25"}, limit=1, output_fields=["source"])[0]
    return {"dense": max((h["distance"] for h in dense), default=0.0),
            "bm25": sparse[0]["distance"] if sparse else 0.0,
            "top": dense[0]["entity"]["source"] if dense else ""}


def sweep(rows: list[dict], key: str) -> list[tuple]:
    """(threshold, precision, recall, f0.5) for every candidate cut. F0.5 weights precision
    2x: a junk passage cited as "relevant" is worse on this site than a missed one."""
    out = []
    for t in sorted({round(r[key], 3) for r in rows}):
        tp = sum(r[key] >= t and r["label"] == "relevant" for r in rows)
        fp = sum(r[key] >= t and r["label"] == "none" for r in rows)
        fn = sum(r[key] < t and r["label"] == "relevant" for r in rows)
        p = tp / (tp + fp) if tp + fp else 1.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        f = 1.25 * p * rc / (0.25 * p + rc) if p + rc else 0.0
        out.append((t, p, rc, f))
    return out


def report(rows: list[dict], key: str, t: float, name: str) -> None:
    tp = sum(r[key] >= t and r["label"] == "relevant" for r in rows)
    fp = [r["id"] for r in rows if r[key] >= t and r["label"] == "none"]
    fn = [r["id"] for r in rows if r[key] < t and r["label"] == "relevant"]
    pos = sum(r["label"] == "relevant" for r in rows)
    print(f"  {name:8} relevant found {tp}/{pos}   false 'relevant': {len(fp)} {fp}   missed: {len(fn)} {fn}")


async def main():
    rows = []
    for c in cases():
        rows.append({**c, **await signals(c["q"])})
    for key in ("dense", "bm25"):
        pos = sorted(r[key] for r in rows if r["label"] == "relevant")
        neg = sorted(r[key] for r in rows if r["label"] == "none")
        print(f"\n== {key}: relevant {pos[0]:.3f}..{pos[-1]:.3f} (median {pos[len(pos)//2]:.3f})   "
              f"none {neg[0]:.3f}..{neg[-1]:.3f} (median {neg[len(neg)//2]:.3f})")
        tune = [r for r in rows if not r["holdout"]]
        hold = [r for r in rows if r["holdout"]]
        t, p, rc, f = max(sweep(tune, key), key=lambda x: (x[3], x[0]))
        print(f"  best on tuning set: threshold {t:.3f}  precision {p:.2f}  recall {rc:.2f}  F0.5 {f:.2f}")
        report(tune, key, t, "tuning")
        report(hold, key, t, "held-out")
    if os.environ.get("GRADE"):
        from src.agents.kb_lookup import _grade
        from src.kb_gate import decide
        from src.tools.milvus_retriever import retrieve
        band = [r for r in rows if decide(r["dense"]) == "grade"]
        right = 0
        print(f"\n== grader on the unsure band ({len(band)} cases)")
        for r in band:
            chunks = (await retrieve(r["q"]))["chunks"]
            keep = await _grade(r["q"], chunks)
            got = "relevant" if keep else "none"
            right += got == r["label"]
            print(f"  {'ok ' if got == r['label'] else 'BAD'} {r['label']:8} → {got:8} {r['id']:26} kept {[chunks[i]['source'] for i in keep]}")
        print(f"  grader accuracy {right}/{len(band)}")
    if os.environ.get("DUMP"):
        for r in sorted(rows, key=lambda r: r["dense"]):
            print(f"{r['dense']:.3f} {r['bm25']:7.2f} {r['label']:8} {'H' if r['holdout'] else ' '} {r['id']:28} {r['top']}")


asyncio.run(main())
