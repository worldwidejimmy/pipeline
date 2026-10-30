"""Experiment (2026-10-30): would a cross-encoder reranker improve retrieval? Verdict: no; see README.md.

Reranks the hybrid (BM25 + dense, RRF) top-POOL with a FlashRank ONNX cross-encoder,
RRF-fuses that order with the hybrid order, and scores hit@K / MRR against the T2
cases. Read-only: it never changes the index or the app.

Run inside a THROWAWAY install (flashrank isn't an app dependency):
  docker compose exec -T backend pip install -q flashrank
  docker compose exec -T -e POOL=8 -e CHARS=800 -e K=6 backend python3 /app/evals/experiments/rerank_experiment.py
  DATASET=… picks the cases file; filter to holdout cases with HOLDOUT=1.
(The next `docker compose up --build backend` removes flashrank again.)
"""
import asyncio, json, os, sys, time
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/evals")
import src.tracing
from run_evals import _load, _match
from src.tools.milvus_retriever import retrieve
from flashrank import Ranker, RerankRequest

cases = [c for c in _load(os.environ.get("DATASET", "retrieval.jsonl")) if not os.environ.get("HOLDOUT") or c.get("holdout")]
MODELS = ["ms-marco-MiniLM-L-12-v2"]
import os
POOL, CHARS = int(os.environ.get("POOL", 20)), int(os.environ.get("CHARS", 1500))

def rank_of(sources, c):
    return next((i + 1 for i, s in enumerate(sources) for e in c["expect_sources"] if _match(s, e)), None)

K = int(os.environ.get("K", 6))
def score(ranks, k=None):
    k = k or K
    hits = sum(1 for r in ranks if r and r <= k); mrr = sum(1 / r for r in ranks if r and r <= k) / len(ranks)
    return f"{hits}/{len(ranks)}", round(mrr, 3)

async def main():
    pools = {}
    for c in cases:
        pools[c["id"]] = (await retrieve(c["q"], top_k=20))["chunks"]
    base = [rank_of([ch["source"] for ch in pools[c["id"]][:6]], c) for c in cases]
    pool_hit = sum(1 for c in cases if rank_of([ch["source"] for ch in pools[c["id"]]], c))
    print(f"baseline hybrid top-6: hit@{K} {score(base)[0]}  MRR {score(base)[1]}   (expected doc anywhere in top-20: {pool_hit}/{len(cases)})")
    for name in MODELS:
        try:
            t0 = time.time(); ranker = Ranker(model_name=name, cache_dir="/tmp/flashrank"); load = time.time() - t0
        except Exception as e:
            print(name, "load failed", e); continue
        ranks, ms = [], []
        for c in cases:
            pool = pools[c["id"]][:POOL]
            t0 = time.time()
            out = ranker.rerank(RerankRequest(query=c["q"], passages=[{"id": i, "text": ch["text"][:CHARS], "meta": {"source": ch["source"]}} for i, ch in enumerate(pool)]))
            ms.append((time.time() - t0) * 1000)
            # RRF-fuse the reranker's order with the hybrid order (k=60, like the retriever)
            rr = {o["id"]: i for i, o in enumerate(out)}
            fused = sorted(range(len(pool)), key=lambda i: -(1 / (60 + i) + 1 / (60 + rr[i])))
            ranks.append(rank_of([pool[i]["source"] for i in fused[:6]], c))
        h, mrr = score(ranks)
        ms.sort()
        print(f"{name:28} hit@{K} {h}  MRR {mrr}  p50 {ms[len(ms)//2]:.0f}ms p95 {ms[int(len(ms)*.95)-1]:.0f}ms  (load {load:.1f}s)  ranks {ranks}")
    print("baseline ranks", base)
asyncio.run(main())
