# Evals plan — SmartMovieSearch agents

**Why now:** this system has already shipped silent regressions that tests didn't catch.
The design principle below is: *build the evals that would have caught the bugs we
actually had*, not a generic eval suite.

## The regressions we're designing against (all real)

| What broke | How long it went unnoticed | Which eval catches it |
|---|---|---|
| Claude wrapped "JSON only" intent extraction in ```json fences → `json.loads` threw → silent fallback searched TMDB for the whole question → **"I couldn't find Inception"** | **weeks** | **T0 parse** + **T1 routing** |
| RAG returned 1 of 5 Police albums (chunks 2-4 never retrieved; 2 of 6 slots went to unrelated docs) | until manually noticed | **T2 retrieval (recall@k)** |
| Answer rendered twice in the UI | until browser-tested | e2e (already covered) |
| A queried artist simply absent from the corpus (The Cars) | until asked | **T2 coverage** |

Note what these have in common: **the system kept returning confident, well-formed
answers.** Nothing crashed. Only an eval that checks *content* would have flagged them.

---

## Tiers, cheapest first

Deliberately ordered by **value ÷ cost**, because there's a 30/day global call cap and
token spend is real.

### T0 — Parse/contract evals · **zero LLM cost** · run on every commit
Pure unit tests on the layers between the model and the app.
- `parse_llm_json` against real Claude outputs: fenced, bare, preamble, truncated, mixed.
- Supervisor route-string normalisation (quoted, fenced, chatty prefix).
- **Fixture-driven**: capture *real* model outputs once into `evals/fixtures/`, then replay
  them free, forever. This is how the fenced-JSON bug becomes a permanent regression test.
- Runs in the existing pytest suite (`make test-unit`).

### T1 — Routing evals · **~40 cheap Haiku calls** · run pre-merge
The supervisor is a **classifier**, so it's gradeable with exact ground truth — and we
already have the rubric: `SUPERVISOR_LLM_RULE_BULLETS` (18 rules) *is* the spec.
- Golden set: ~40-60 questions → expected route (`tmdb`, `rag`, `music`, `tmdb+rag`, …).
- Metrics: **accuracy**, plus a **confusion matrix** (which routes get mistaken for which).
- Only invokes the supervisor — no downstream agents, no synthesis. Cheap and fast.
- Gate: fail CI if accuracy drops below a committed baseline (start at whatever we measure,
  then ratchet).

### T2 — Retrieval evals · **~$0.00002, NO LLM** · run on every ingest/chunking change
**Highest value per cent in the whole plan.** Retrieval is scored without generating a
single token — embed the query, look at what comes back.
- Golden set: query → the doc(s) that *should* surface (e.g. "Police albums" →
  `docs/music/artists/the-police.md`).
- Metrics: **recall@k**, **MRR**, and *position of first relevant chunk*.
- Catches exactly the Police failure, plus any regression from changing `TOP_K`,
  chunk size, the embedding model, or `HYBRID_RRF_K`.
- Also a **coverage** report: which asked-about entities have no doc at all (The Cars).

### T3 — Answer-quality evals (LLM-as-judge) · **expensive** · nightly / pre-release
Reuse the **blind judge already built** for compare mode (`compare.py:_JUDGE_SYSTEM`) —
it already randomises A/B order, which is the standard mitigation for position bias.
- Rubric per case: **groundedness** (every claim supported by retrieved context),
  **relevance**, **completeness**, **no fabrication**.
- Include **negative cases**: questions the KB genuinely can't answer — the correct
  behaviour is admitting it, and we should *reward* that, not punish it. (Otherwise you
  train yourself toward confident hallucination.)
- Report a score per dimension, not one blended number.

### T4 — Assertion evals (regression canaries) · **cheap** · nightly
Deterministic assertions on full-pipeline output for a handful of known-good queries:
- "Tell me about Inception" → must contain `DiCaprio`, must **not** contain
  `couldn't find` / `wasn't able to`.
- These are blunt but they're exactly what would have screamed during the fenced-JSON
  weeks. Already partly present in `tests/e2e.mjs`.

### T5 — Cost & latency · free (metadata we already emit)
Per-query tokens and wall time are already in the SSE `done` event. Track p50/p95 and
tokens/query over time; alert on regressions. A prompt change that doubles tokens is a
real defect.

---

## Structure

```
cineai/evals/
  datasets/
    routing.jsonl        # {"q": "...", "expect": "tmdb+rag", "why": "..."}
    retrieval.jsonl      # {"q": "...", "expect_sources": ["docs/..."], "min_recall": 1}
    quality.jsonl        # {"q": "...", "rubric": {...}, "answerable": true|false}
  fixtures/              # captured REAL model outputs, replayed free
  run_evals.py           # --tier t0,t1,t2 --json   (exit non-zero on regression)
  results/               # append-only JSONL history per run
```

**Golden datasets are hand-written and version-controlled**, never model-generated —
a model-generated eval set mostly measures agreement with the generating model.

**Hold-out discipline:** don't tune prompts against the whole set. Keep ~20% unseen, or
you're optimising the metric rather than the system.

---

## Judge pitfalls to design around (these are the classic failure modes)

- **Position bias** — judges favour the first answer. *Already mitigated*: compare-mode
  randomises order. Keep that in the eval judge.
- **Self-preference** — a model rates its own output higher. Consider judging with a
  different tier (e.g. generate with Haiku, judge with Sonnet).
- **Verbosity bias** — longer answers score higher regardless of quality. Counter with an
  explicit rubric and a length-controlled comparison.
- **Judges are not free or deterministic** — pin the model id, set temperature 0, and
  record the judge version alongside every score, or your history isn't comparable.
- **Always keep a human-labelled slice** to check the judge itself against. An unvalidated
  judge is just a confident opinion.

---

## Cost control

| Tier | Cost per full run | Cadence |
|---|---|---|
| T0 | $0 | every commit |
| T1 | ~40 Haiku calls (cents) | pre-merge |
| T2 | ~$0.00002 (embeddings only) | on ingest/retrieval changes |
| T3 | 2 LLM calls × N cases | nightly or pre-release |
| T4 | ~5 full pipeline runs | nightly |
| T5 | $0 | continuous |

**Evals must bypass the app's rate limits** (`GLOBAL_DAILY_CALL_CAP`) or they'll eat your
30/day budget — run them against the pipeline directly, not through `/api/query`.

---

## Build order

1. **T2 retrieval** — free, deterministic, and it targets the failure mode most likely to
   recur as the corpus grows.
2. **T0 parse fixtures** — turns the worst historical bug into a permanent test.
3. **T1 routing** — cheap, gives a single trackable accuracy number.
4. **T4 canaries** — extend `tests/e2e.mjs`.
5. **T3 judge** — last, because it's the most expensive and the least trustworthy without
   the others in place.

## Open questions
- Where should eval results live so trends are visible — a JSONL history plus a card on
  the ops dashboard? (The dashboard already renders any `level`/`label`/`detail` rows.)
- Do we gate merges on T1 accuracy, or just report? (Suggest: report first, gate once the
  baseline is stable.)
- Is stockmarket's LLM trading agent in scope later? Its verdicts are already logged as
  `agent_events` — that's an eval set waiting to happen, and it has *ground truth*
  (subsequent price action), which is rarer and more valuable than any judge.
