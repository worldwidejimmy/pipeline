# Experiments

Measured ideas, kept with the code that measured them, **including the ones we didn't ship**.

## Knowledge-base relevance gate: shipped (2026-11-02)

**Question:** retrieval always returns its nearest passages. Can a cheap signal tell "the
knowledge base has something useful for this question" from "it doesn't", so the chat can
say so honestly, bring in the RAG agent when it helps and skip its LLM call when it can't?
`kb_gate_experiment.py`; labelled set = the 34 T2 cases (answerable by construction) +
`datasets/kb_gate.jsonl` (10 natural answerable questions, including two 2026 reviews newer
than the model, and 24 unanswerable ones: off-topic, live logistics, celebrity trivia,
unreleased films). Tuned on the non-holdout split, reported on the held-out split.

**Why not the existing score:** the hybrid search's RRF score depends only on rank (1st place
≈ 0.033 for a perfect match and for the least-bad of a bad lot).

| Signal (free, no LLM) | answerable range | unanswerable range | best threshold (tuning) | held-out |
|---|---|---|---|---|
| dense cosine, best of top-6 | 0.454–0.823 (median 0.671) | 0.221–0.599 (median 0.436) | 0.60: precision 1.00, recall 0.81 | 0 false "relevant", recall 0.78 |
| BM25 top score | 12.1–41.7 | 11.4–28.5 | 14.6: precision 0.76 | 4 false "relevant" |

The dense score separates the extremes cleanly but overlaps between 0.45 and 0.60 (18 of 68
questions: 9 answerable, 9 not, e.g. "was the Barbie movie any good" vs "Taylor Swift tour
dates"). So the gate is three-way (`src/kb_gate.py`): ≥ 0.60 relevant, < 0.45 nothing useful,
both with no LLM call; the band between goes to a Haiku grader (`src/agents/kb_lookup.py`)
that also picks which passages to keep.

**Grader on the band** (`GRADE=1`): 17/18 after tightening the prompt to "the passage must be
about the specific title/person asked about". The miss: "who was just cast as the next James
Bond" kept a *Layer Cake* review (Daniel Craig's pre-Bond film). One apparent false positive
in live testing was right: for "who composed the score for Inception?" it kept a *Purge:
Election Year* review whose text compares that film's score to Zimmer's *Inception*.

**Caveats:** 68 labelled questions is small; the thresholds belong to `text-embedding-3-small`
and this corpus, so re-run after changing either. Effect on cost: the RAG agent now joins
whenever the knowledge base is relevant, so T5 moved from $0.0008 to about $0.0010–0.0011 per
question (more of the answer grounded in the curated corpus and Ebert reviews).

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
