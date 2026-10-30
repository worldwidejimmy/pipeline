# Experiments

Measured ideas, kept with the code that measured them, **including the ones we didn't ship**.

## Cross-encoder reranker: not shipped (2026-10-30)

**Question:** does reranking the hybrid retriever's candidates improve RAG?
`rerank_experiment.py`; FlashRank ONNX cross-encoders (MS MARCO-trained), CPU, on this box.

Replacing the hybrid order (top-20 pool, top-6 kept, 20 tuning cases):

| Reranker | hit@6 | MRR | p50 latency |
|---|---|---|---|
| none (hybrid BM25 + dense, RRF) | 20/20 | 0.838 | — |
| TinyBERT-L-2 | 19/20 | 0.860 | 38 ms |
| MiniLM-L-12 | 20/20 | 0.854 | 900 ms |
| MultiBERT-L-12 | 13/20 | 0.406 | 2.3 s |

RRF-*fusing* the reranker's order with the hybrid order (rather than replacing it), then
tuning the pool and passage length on the same 20 cases, gave MiniLM-L-12 with
pool 8 / 800 chars: **MRR 0.942**, 20/20, ~190 ms.

**Then on 14 held-out cases** (written before this config was run on them, never used
for tuning):

| | hit@3 | hit@4 | hit@6 | MRR |
|---|---|---|---|---|
| hybrid | 12/14 | 13/14 | 13/14 | 0.720 |
| + fused MiniLM rerank | 12/14 | 13/14 | 13/14 | 0.768 |

**Verdict.** The tuning-set gain (+0.10 MRR) shrank to +0.05 on held-out data, and the
reranker found **no additional documents** at any k. It only reorders inside the window
the model sees anyway. That isn't worth +190 ms of CPU per RAG question on a shared VPS,
plus an ONNX runtime and model in the image.

**What it surfaced instead:**
- The held-out cases are now part of T2 (`holdout: true`, reported as `holdout_mrr`). One
  of them is a real retrieval miss, `wes-anderson-look`: a nameless description ("symmetrical
  pastel production design…") that Ebert reviews outrank. That's a query-understanding
  problem (rewriting/HyDE), not a ranking one.
- The hybrid retriever finds the same documents at top-4 as at top-6, on both sets, with
  full content coverage (T2 `--k 4`). Cutting `TOP_K` to 4 would trim RAG context ~33%. It
  needs a T3 (answer completeness) check before switching, so it isn't switched.
