# SmartMovieSearch / CineAI — Engineering handoff

This document is for anyone taking over the **movie, TV, and music** multi-agent app under `cineai/`. It complements the root [README.md](README.md) with operational detail, recent changes, and known constraints.

---

## What this product is

- **Frontend:** React (Vite), SSE client, observability panels (graph, timeline, events, RAG chunks).
- **Backend:** FastAPI, LangGraph pipeline, **Anthropic Claude** LLM (Haiku/Sonnet/Opus via `src/llm.py`), Milvus hybrid RAG (BM25 + dense), TMDB, optional Tavily, MusicBrainz (no key).
- **Deployment:** Docker Compose in `cineai/docker-compose.yml`; production site referenced from root README.

---

## Repository layout (relevant paths)

| Path | Role |
|------|------|
| `cineai/backend/src/main.py` | FastAPI, SSE streaming, `/api/query`, **`/api/compare`**, **`/api/usage`**, `/api/status`, `/api/knowledge`, **`/api/rules`**, health |
| `cineai/backend/src/usage.py` | **Open-access control**: per-IP rolling rate limit + daily token accounting + unlimited-token check |
| `cineai/backend/src/compare.py` | **RAG-vs-no-RAG** side-by-side compare stream (two concurrent LLM calls) |
| `cineai/backend/src/graph/pipeline.py` | LangGraph: supervisor, agents, synthesiser |
| `cineai/backend/src/agents/supervisor.py` | Routing: **keyword overrides** + LLM; **`SUPERVISOR_LLM_RULE_BULLETS`** (single source for UI API) |
| `cineai/backend/src/agents/tmdb_agent.py` | TMDB: titles, TV vs movie guardrails, markdown links in answers |
| `cineai/backend/src/agents/rag_agent.py` | Milvus hybrid retrieval |
| `cineai/backend/src/agents/music_agent.py` | MusicBrainz + grounded answers + links |
| `cineai/backend/src/agents/search_agent.py` | Tavily |
| `cineai/backend/src/agents/synthesiser.py` | Merge agent outputs; preserve markdown links |
| `cineai/backend/src/tools/musicbrainz_client.py` | MusicBrainz HTTP client |
| `cineai/backend/scripts/ingest.py` | Ingest `.md`/`.txt` from `docs/` into **same** Milvus collection |
| `cineai/backend/scripts/scrape_ebert.py` | Roger Ebert reviews via **Wayback Machine** (live site blocks bots) |
| `cineai/backend/scripts/ingest_ebert.py` | JSONL → chunked embeddings → Milvus **append** |
| `cineai/backend/docs/` | Curated markdown corpus (decades, directors, TV, music, etc.) |
| `cineai/backend/data/` | **Gitignored** generated files: `ebert_urls.json`, `ebert_reviews.jsonl`, `scrape.log`, `nightly-logs/`. **Volume-mounted** into the backend container (so ingest/cron can read it). |
| `cineai/nightly_update.sh` | **Nightly RAG refresh** — scrape (capped `--limit 800`) + ingest new reviews; emails a summary when it adds any. |
| `cineai/backup.sh` | **Nightly backup** — tars `.env` + `data/` to `~/backups/smartmoviesearch` (keeps 14). Milvus is derived; restore via `ingest_ebert.py`. |
| `cineai/devops_check.py` | **Nightly DevOps health check** — containers/app/disk/mem/backup/cert/ingest → emails 🟢/🔴 report. |
| `cineai/send_email.py` | Shared SMTP notifier (reads `SMTP_*`/`ADMIN_EMAIL` from `backend/.env`; no-ops if unset). |
| `cineai/frontend/src/components/RoutingRulesModal.tsx` | **Rules** UI (loads `/api/rules`) |
| `cineai/frontend/src/components/{UsageBadge,CompareView,PasswordGate}.tsx` | Token meter + free-quota chip; RAG compare two-column view; optional sign-in modal |

---

## RAG: one collection, two kinds of content

**Decision:** Ebert reviews and existing markdown docs share the **same** Milvus collection. Chunks are distinguished by the `source` field (e.g. `ebert/the-godfather-1972` vs `docs/...`).

**Why not a second DB:** Simpler ops, one hybrid search, synthesiser already merges signals. Split later only if you need a dedicated “Ebert-only” product surface.

**Regenerating Ebert data (not in git):**

1. From `cineai/backend/` (host with Python + `httpx`, `beautifulsoup4`, `lxml`, or inside the backend container after `docker compose build backend`):

   ```bash
   python3 -u scripts/scrape_ebert.py
   ```

   - First run walks the Internet Archive **CDX API** year-by-year and writes `data/ebert_urls.json` (cache).
   - Then fetches archived HTML per URL into `data/ebert_reviews.jsonl` (resumable; `--limit` for tests).
   - **Live `rogerebert.com` returns 403** to many server IPs; Wayback is the supported path.
   - Some CDX year queries may **timeout**; re-run or tighten year windows in the script if needed.

2. Ingest into Milvus:

   ```bash
   docker compose exec backend python scripts/ingest_ebert.py --skip-existing
   ```

   - **Idempotent:** `--skip-existing` dedupes on a normalized slug (`norm_slug` strips the `ebert/` and `amp/` prefixes), so the AMP mirror pages and canonical pages collapse to one review and re-runs never duplicate.
   - **Bounded memory:** embeds + inserts in batches of `EMBED_BATCH` (256) — embedding the whole corpus at once would OOM this small shared host.
   - Optional: `--limit N`.
   - Current state: **~12,636 distinct reviews / ~99k chunks** ingested (37 markdown docs add ~539 more).

### Nightly automation (INSTALLED) + notifications

Three cron jobs run nightly in the `ubuntu` crontab (**UTC**, quiet US hours; the crontab is not in git):

```
CRON_TZ=UTC
0  8 * * *  cineai/nightly_update.sh   # RAG refresh: scrape --refresh-recent 1 --limit 800, then ingest_ebert.py --skip-existing
0  9 * * *  cineai/backup.sh           # tar .env + data/ → ~/backups/smartmoviesearch (keep 14)
15 9 * * *  cineai/devops_check.py      # health report email
```

- **Ingest** is idempotent + flock-guarded; logs to `data/nightly-logs/`. `--limit 800` bounds it to a ~25-min run that chips at the backlog; failed Wayback URLs are recorded in `data/ebert_failed_urls.json` and skipped on future runs. Emails a summary **only when it adds** review chunks.
- **DevOps check** runs on the host (no sudo), reads live state, and emails 🟢/🔴.
- **Email** is via `send_email.py` using `SMTP_*` + `ADMIN_EMAIL` in `backend/.env` (currently reuses the Brevo relay; From/To point at `admin@dailystockbot.com` until changed). Host scripts read `.env` live — no restart needed to change SMTP settings.
- **Docker log rotation** is set in `docker-compose.yml` (`x-logging`: 10m × 3) — applies on each container's next recreate.

---

## Access control (open access + per-IP quota)

The site is **public** — no password wall. `src/usage.py` enforces:

- **Anonymous:** `FREE_REQUESTS_PER_WINDOW` (default **10**) searches per **`FREE_WINDOW_SECONDS`** (default **3600s / 1h**) per IP. Over limit → `/api/query` and `/api/compare` emit a `pipeline_error` SSE with `code: "ip_limit"` (frontend opens the sign-in modal).
- **Site-wide daily cap:** `GLOBAL_DAILY_CALL_CAP` (default **30**) — total anonymous searches/day across everyone (research/showcase). Over it → `code: "daily_cap"`. Signed-in/admin bypasses. Also `DAILY_TOKEN_HARD_CAP` (default 0/off) pauses anonymous calls once the day's token spend is hit. Both reset at UTC midnight; counters are in-memory.
- **Signed in:** the old `PREVIEW_PASSWORD` now grants **unlimited** access (token via `POST /api/auth`, sent as `X-Access-Token` / `?_t=`). Leaving `PREVIEW_PASSWORD` blank means *no one* can unlock unlimited.
- **Token meter:** cumulative Claude tokens used **today by everyone** (your paid spend), accumulated server-side and exposed at **`GET /api/usage`** alongside the caller's remaining free credits. `DAILY_TOKEN_BUDGET` (default 0 = no cap) optionally sets a meter denominator.
- **Real client IP:** read from `CF-Connecting-IP` (Cloudflare), then `X-Forwarded-For[0]`. **Do not** trust `X-Real-IP` — the frontend-container nginx overwrites it with the docker gateway.

Most counters are in-memory (reset on backend redeploy); the **IP blacklist** persists to `data/ip_blacklist.json` (volume-mounted).

### Abuse controls + admin

- **Bot protection (lightweight):** `/api/query` and `/api/compare` reject empty / known-library User-Agents and present-but-foreign `Origin`/`Referer` (`_looks_like_bot` in `main.py`). Signed-in callers bypass it. Cloudflare **Turnstile** is the intended stronger layer — not yet wired.
- **IP blacklist:** `usage.is_blacklisted` blocks at `consume()`; manage via the admin screen.
- **Auth lockout:** `/api/auth` locks an IP after `AUTH_MAX_FAILS` (5) failures for `AUTH_LOCKOUT_SECONDS` (900s).
- **Admin screen** (🛡️, header — visible only when signed in): `GET /api/admin/usage` (per-IP requests + tokens today) and `POST /api/admin/blacklist` ({ip, action}). Both gated by the access token (`usage.is_unlimited`).

`GET /api/compare?q=` answers the same question twice (grounded on Milvus chunks vs. bare model) and streams both via `compare_token {side}` / `compare_side_end` / `compare_done` events. Costs **one** free credit despite two LLM calls.

## Routing and the Rules UI

- **Keyword overrides** (`_FORCE_MUSIC_KEYWORDS`, `_FORCE_TMDB_KEYWORDS`) run **before** the LLM so small models cannot mis-route obvious cases (e.g. “lyrics” → `music`).
- **LLM rules** for the supervisor are defined once in **`SUPERVISOR_LLM_RULE_BULLETS`** in `supervisor.py` (injected into the supervisor system prompt); **`GET /api/rules`** imports the same tuple so the Rules modal cannot drift from runtime behavior.
- Header button **Routing rules** opens the modal (tabs: agents, routes, keywords, LLM rules).

---

## Configuration and operations

- **Env:** `cineai/backend/.env` (see `.env.example`). Not committed.
- **Model tier:** set `DEFAULT_MODEL_TIER` (`haiku` | `sonnet` | `opus`) in `.env`; one server-wide setting changes every agent (`src/llm.py`). Haiku is default (cheapest/fastest). `ANTHROPIC_API_KEY` required. After `.env` changes, recreate: `docker compose up -d --force-recreate backend`. **Opus tier:** the factory omits `temperature` (Opus 4.8 rejects sampling params with a 400).
- **Frontend text/build:** Static assets are baked into the frontend image; after UI changes run `docker compose build frontend` (or your CI equivalent), not only `up -d`.
- **LangSmith:** Optional tracing via `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`.

---

## Git and release tagging

- Generated scrape artifacts under `cineai/backend/data/` are **ignored** (only `data/.gitkeep` is tracked).
- This handoff was paired with a release tag on `main`; see `git tag -l` and `git show <tag>`.

---

## Open risks / follow-ups

- **Copyright / terms:** Ebert full text is ingested for RAG from archived copies; confirm your use case complies with Internet Archive and site terms. When expanding to TV/music corpora, prefer CC BY-SA sources (Wikipedia, TVmaze, Last.fm) and avoid sites whose ToS ban scraping/AI use (Rotten Tomatoes, Metacritic, Letterboxd).
- **AMP duplicates (resolved):** the scraper captured both `rogerebert.com/reviews/<slug>` and `/reviews/amp/<slug>`; ingest now normalizes the slug so they dedupe. ~6,160 pre-existing duplicate mirrors were removed.
- **CDX completeness:** Missing years after timeouts may need manual CDX retries or quarterly splits. The Wayback CDX endpoint intermittently 503s — `--refresh-recent` handles it gracefully (skips that pass).
- **Star ratings** in scraped JSON may be `null` if the archived HTML layout differs; parser targets `article.entry` and `.star-rating` from legacy rogerebert layouts.

---

## Contact / continuity

- Primary repo: `origin` as configured locally (`worldwidejimmy/pipeline`).
- For “what broke last,” check LangSmith traces and backend logs from `docker compose logs backend`.
