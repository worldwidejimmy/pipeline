# Platform plan — automated evals, admin page, model auto-upgrade, compare fixes

Status: **shipped** 2026-10-18 (all six build steps). Builds on `EVALS-PLAN.md`.
Operational detail now lives in `HANDOFF.md` → Evals / Model selection; this file is the design record.
Goal: practise AI-platform-engineering properly. The agents run unattended, so they need
continuous, automated evidence that they still work. A human should look only when that
evidence says something changed.

This is a public repo and a job-hunting demo, so every section ends with what is public
and what is admin-only.

---

## 1. Model auto-upgrade: "newest model of the configured cost tier"

**Today:** `src/llm.py` pins `MODELS = {haiku: claude-haiku-4-5, …}`. The default tier is
`haiku`, so the app runs Haiku 4.5. Haiku 5.5 exists and costs **10× less**
($0.10/$0.50 vs $1/$5 per MTok).

**Design**
- `resolve_model(tier)` calls the Models API (`GET /v1/models`) at startup and every 24h. It
  filters on the family prefix (`claude-haiku-`), picks the newest by `created_at`, and
  caches the result. On any API error it falls back to the pinned table, so startup never
  depends on the Models API.
- `MODEL_PIN=<id>` in `.env` overrides auto-resolution. This is the escape hatch when a new
  model regresses the evals.
- **Capability-aware params.** The newer generation changes the request surface, and this
  is the real work:
  - Haiku 5.5 / Sonnet 5.5 **reject non-default `temperature`** with a 400. Every
    `get_chat(temperature=…)` call would break today.
  - Haiku 5.5 has **thinking on by default** (effort `medium`). The supervisor's
    `max_tokens=16` route call and the `/api/status` ping (`max_tokens=5`) would spend
    their whole budget thinking. The fix: a per-call `effort`/thinking profile
    (`route` → thinking off/low, `synthesise` → low/medium).
  - Params are decided from the Models API `capabilities` field, not from string matching
    on the model id. That replaces today's `startswith("claude-opus")` hack.
- Check that `langchain-anthropic` 1.4.7 passes `output_config`/`thinking` through. If it
  doesn't, bump it, or use `model_kwargs`.
- **Every eval result records the model id.** When the resolved model changes, the next
  nightly run is flagged `model changed: X → Y` in the email and on the dashboard. A
  silent upgrade that regresses quality is exactly what the evals exist to catch.

**Public/admin:** the model id is already visible on the public status modal. That's fine.

---

## 2. Evals: from "make target" to unattended, scheduled evidence

Remaining tiers from `EVALS-PLAN.md`, in build order:

| Tier | What | Cost/run (Haiku 5.5) | Cadence |
|---|---|---|---|
| T0 parse fixtures | ✅ shipped | $0 | every commit (`make test-unit`) |
| T2 retrieval | ✅ shipped | ~$0 | nightly + on corpus change |
| **T1 routing** | ~50 hand-written q → expected route; accuracy + confusion matrix; supervisor node only | <$0.01 | nightly |
| **T4 canaries** | ~6 full-pipeline queries with `must_contain` / `must_not_contain` (`couldn't find`) | ~$0.02 | nightly |
| **T5 cost/latency** | p50/p95 latency + tokens/query from T4 runs and live usage | $0 | nightly |
| **T3 LLM judge** | ~20 cases, groundedness/relevance/completeness/no-fabrication, incl. *unanswerable* cases; judge = a **different tier** (Sonnet 5.5) to avoid self-preference; temp/effort pinned; judge id recorded | ~$0.20 | weekly |

**Automation**
- `run_evals.py --tier t0,t1,t2,t4,t5` (T3 behind `--tier t3`). Evals call the graph
  in-process, so they never touch `GLOBAL_DAILY_CALL_CAP` or the public quota.
- **Results persistence (bug today):** `results/*.jsonl` is written *inside* the container
  and lost on recreate. Bind-mount `cineai/evals/results` (gitignored) so history survives.
- `evals/latest.json` is a single summary the email, dashboard and admin page all read.
  It holds per tier: pass/fail vs baseline, the headline metric, the model id, cost, and a
  timestamp.
- Schedule: a host **systemd timer** (`cineai-evals.timer`, nightly 04:30; T3 Sundays)
  runs `make eval-nightly`. A user timer is used rather than cron so it shows in
  `systemctl list-timers` and its logs go to journald.
- **Regression → immediate admin email** via the existing Brevo `notify-admin.sh`. The
  daily digest carries the green/red summary either way.
- Hold-out: 20% of T1/T3 cases are tagged `holdout` and never shown while tuning prompts.

---

## 3. Admin page (`/admin`)

The 🛡️ modal is promoted to a full page. Like everything admin-only, it is gated by the
existing access token (`_require_admin` on every `/api/admin/*` endpoint, server-side).

Sections:
1. **Evals** — the latest run per tier (pass/fail, headline metric, trend sparkline from
   the JSONL history), the confusion matrix for T1, and failing cases with what came back.
2. **Model** — configured tier, resolved model, pin status, last resolution time, and a
   "newer model available" notice.
3. **Cost & latency** — T5 trends: tokens/query, p50/p95, and spend per day.
4. **Usage & abuse** — the existing per-IP table + blacklist, moved from the modal.
5. **Service health** — what the ⚙️ modal shows today.

**Devops emails:** the daily digest (server-management `daily_ops_digest.py`) gets a
`check_evals()` section that reads `evals/latest.json`. The per-box dashboard
(`status-agent.py`) reuses the digest builders, so it gets the evals rows for free. Each
email links to `https://smartmoviesearch.com/admin`. The page needs sign-in, so the link
alone reveals nothing.

**Public/admin**
- The SPA bundle is public, so the `/admin` route's *code* is visible. That's fine,
  because no data is in the bundle and every byte of data comes from gated endpoints.
- Never in eval results: client IPs, API keys, server hostnames. Eval datasets are
  hand-written movie and music questions, which is safe in the public repo. Results stay
  gitignored.

---

## 4. Service status (⚙️ modal) — public eval summary

Add an **"Quality checks"** row to the public status modal: one line per tier (e.g.
`Retrieval ✓ 20/20 · Routing ✓ 94% · Canaries ✓ 6/6 — last run 4h ago, claude-haiku-5-5`).
This makes a good demo: visitors see that the RAG system is continuously evaluated. It
shows summary numbers only. Case lists, failing queries and cost stay on `/admin`. It is
served by a new **public, read-only, summary-only** endpoint `GET /api/evals/summary`,
built from `latest.json` with an explicit allow-list of fields.

---

## 5. Compare-mode fixes

- **A/B looks swapped.** The judge randomises order to avoid position bias, which is
  correct. But the UI always shows RAG on the left, so when the judge saw no-RAG first,
  its "Answer A" is the *right-hand* column.
  - **Fix:** keep the judge blind and randomised. Buffer its short reply and normalise its
    labels so **Answer A is always the left column (With RAG)**. Add a footnote: "the judge
    saw them in random order (this time RAG was shown second)".
  - Add a T0 test for the label swap, including the `Tie` and a "both A and B" sentence.
- **Toggle default.** Keep the 🆚 switch and default it to **RAG (off)**. It already
  defaults to off, so make that explicit. If it's ever persisted, persist RAG.

---

## Build order (each step: code → tests/evals → docs → commit → push, privacy audit first)

1. Compare fixes (§5) — small, visible, independent.
2. Model resolution + capability-aware params (§1). **Gate:** T0 + T2 green, a manual T4
   smoke run on Haiku 5.5, and the e2e test, before flipping the default.
3. T1 routing + T4 canaries + T5 (§2), with results persistence and `latest.json`.
4. Nightly timer + regression email + digest/dashboard section (§2, §3 emails).
5. `/admin` page (§3) + public status summary (§4).
6. T3 judge (weekly).

## Cost envelope

Nightly T1+T2+T4+T5 on Haiku 5.5 costs about **$0.03/night**. Weekly T3 costs about
**$0.20**. That is **under $2/month** all-in, and well under today's live traffic cost,
since moving to Haiku 5.5 alone cuts per-query spend ~10×.

---

# Phase 2 (started 2026-10-30): agent interfaces, observability, safety

Order, each shipped as code → tests/evals → docs → commit → push:

1. ✅ **MCP server** (2026-10-30): public retrieval tools plus an OAuth-protected `ask`. Design
   and threat model in `MCP-PLAN.md`.
2. ✅ **Tracing** (2026-10-30): tracing was already on, but implicitly, with evals mixed in and
   no redaction. Now one trace per question, evals in their own project, PII redaction,
   sampling, and run ids for feedback (`src/tracing.py`).
3. **Guardrails + red-team eval tier**: prompt-injection/jailbreak cases, including
   injection planted in retrieved content.
4. **CI on GitHub**: unit tests plus a server-side copy of the privacy guard on every
   push/PR (the local hooks don't run on web merges), and an eval badge.
5. **Prompt caching**: on the large system prompts, measured with T5.
6. **Reranker** in RAG, with a before/after on T2.
7. **Answer feedback** (👍/👎) feeding the eval datasets.
8. **Structured outputs** for supervisor routing.
