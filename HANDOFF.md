# SmartMovieSearch / CineAI — Engineering handoff

This document is for anyone taking over the **movie, TV, and music** multi-agent app under `cineai/`. It complements the root [README.md](README.md) with operational detail, recent changes, and known constraints.

---

## What this product is

- **Frontend:** React (Vite), SSE client, observability panels (graph, timeline, events, RAG chunks).
- **Backend:** FastAPI, LangGraph pipeline, Groq LLM, Milvus hybrid RAG (BM25 + dense), TMDB, optional Tavily, MusicBrainz (no key).
- **Deployment:** Docker Compose in `cineai/docker-compose.yml`; production site referenced from root README.

---

## Repository layout (relevant paths)

| Path | Role |
|------|------|
| `cineai/backend/src/main.py` | FastAPI, SSE streaming, `/api/status`, `/api/knowledge`, **`/api/rules`**, health |
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
| `cineai/backend/data/` | **Gitignored** generated files: `ebert_urls.json`, `ebert_reviews.jsonl`, `scrape.log` |
| `cineai/frontend/src/components/RoutingRulesModal.tsx` | **Rules** UI (loads `/api/rules`) |

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
   docker compose exec backend python scripts/ingest_ebert.py
   ```

   Optional: `--limit N`, `--skip-existing` (see script docstrings).

---

## Routing and the Rules UI

- **Keyword overrides** (`_FORCE_MUSIC_KEYWORDS`, `_FORCE_TMDB_KEYWORDS`) run **before** the LLM so small models cannot mis-route obvious cases (e.g. “lyrics” → `music`).
- **LLM rules** for the supervisor are defined once in **`SUPERVISOR_LLM_RULE_BULLETS`** in `supervisor.py` (injected into the supervisor system prompt); **`GET /api/rules`** imports the same tuple so the Rules modal cannot drift from runtime behavior.
- Header button **Routing rules** opens the modal (tabs: agents, routes, keywords, LLM rules).

---

## Configuration and operations

- **Env:** `cineai/backend/.env` (see `.env.example`). Not committed.
- **Groq model:** Prefer a smaller/faster model on free tier (e.g. `llama-3.1-8b-instant`) to reduce rate limits; set `GROQ_MODEL` in `.env`. After `.env` changes, recreate containers: `docker compose up -d --force-recreate backend`.
- **Frontend text/build:** Static assets are baked into the frontend image; after UI changes run `docker compose build frontend` (or your CI equivalent), not only `up -d`.
- **LangSmith:** Optional tracing via `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`.

---

## Git and release tagging

- Generated scrape artifacts under `cineai/backend/data/` are **ignored** (only `data/.gitkeep` is tracked).
- This handoff was paired with a release tag on `main`; see `git tag -l` and `git show <tag>`.

---

## Open risks / follow-ups

- **Copyright / terms:** Ebert full text is ingested for RAG from archived copies; confirm your use case complies with Internet Archive and site terms.
- **CDX completeness:** Missing years after timeouts may need manual CDX retries or quarterly splits.
- **Star ratings** in scraped JSON may be `null` if the archived HTML layout differs; parser targets `article.entry` and `.star-rating` from legacy rogerebert layouts.

---

## Contact / continuity

- Primary repo: `origin` as configured locally (`worldwidejimmy/pipeline`).
- For “what broke last,” check LangSmith traces and backend logs from `docker compose logs backend`.
