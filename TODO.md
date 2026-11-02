# TODO

The one list of open work for SmartMovieSearch. Other docs link here instead of keeping their
own roadmaps. Finished work is recorded in the docs and the git history, not here.

## AI and RAG

- [ ] **"RAG value" eval.** Replaces the removed RAG vs no-RAG compare toggle (it always looked
  bad because Claude already knows famous films). Measure whether knowledge-base grounding
  improves answers, especially for reviews newer than the model's training data (the nightly
  ingest adds 2026 reviews). The blind-judge code in `cineai/backend/src/compare.py` and
  `/api/compare` can be reused; drop the endpoint if the eval replaces it.
- [ ] **Is the RAG agent's summary call redundant?** When the knowledge base is relevant, the RAG
  agent summarises the passages before the combiner reads its report. Test letting the combiner
  read the passages directly (one Claude call less per question); gate on T3 and T4.
- [ ] **A genuinely unattended agent.** Today's scheduled jobs are plain scripts (the UI says so).
  Candidate: when the nightly evals regress, an agent reads the failing cases and their traces,
  investigates, and emails a draft diagnosis.
- [ ] **Query expansion for nameless descriptions** (e.g. HyDE). The one T2 miss,
  `wes-anderson-look`, describes a style without naming the director: a recall problem, not a
  ranking one (see `cineai/evals/experiments/README.md`).
- [ ] **`TOP_K` 6 → 4.** Same T2 content coverage with about a third less RAG context; run T3
  before changing it.
- [ ] **Semantic cache** (e.g. Redis) for repeated or near-identical questions.
- [ ] **IMDb datasets ingest** (free TSVs: ratings, principals) for breadth beyond TMDB lookups.
- Ideas, not planned: user-uploaded documents into a per-session collection; poster-image
  embeddings; a watchlist.

## Platform and MCP

- [ ] **Turn on `/mcp/agent` (owner).** Choose a hosted OAuth authorization server that MCP
  clients can register with automatically (e.g. WorkOS AuthKit or Auth0), set the `MCP_OAUTH_*`
  values, then verify sign-in end to end from Claude Code. The resource-server side
  (`JWTVerifier`) is built and tested. See `cineai/MCP-PLAN.md`.
- [ ] **MCP rate limits for web-based clients.** claude.ai and ChatGPT connectors call from the
  provider's servers, so all their users would share one per-IP limit. Connect from claude.ai
  first, then give those provider ranges a shared, larger bucket (or rely on the global cap).
- [ ] **Remaining blocking calls in async code:** `_has_hybrid_schema()` runs
  `describe_collection` on every query (cache it), and the Tavily client is synchronous.
- [ ] **CI, optional (owner):** load the private guard patterns and approved identity as GitHub
  secrets (CI runs generic checks without them); optionally a branch ruleset requiring checks.

## Security (owner)

- [ ] **SEC-3: Anthropic spend limit.** Set a monthly limit and usage alerts in the Anthropic
  console (the in-app daily token and call caps already help). See `cineai/SECURITY-AUDIT.md`.
- [ ] **Optional:** Cloudflare Bot Fight Mode.
