# CineAI — Agent Handoff Document

**Branch:** `claude/review-pipeline-resume-cJuXv` in `worldwidejimmy/pipeline`  
**Project path:** `cineai/` (subdirectory of the pipeline repo)  
**Target deploy:** OVH cloud server + Cloudflare in front  
**Last updated by:** Claude Code session `01Bc5rUUJ4nx2hKTe3uuCr5o`

---

## What This Project Is

CineAI is a multi-agent movie/TV intelligence platform that answers questions static databases (IMDB, RT) cannot. The core insight: TMDB has no "heist" genre tag — but an LLM knows what a heist film is. The system combines real-time TMDB data, RAG over a movie knowledge corpus, and live web search, then synthesises them with a fast LLM (Groq).

**The demo query that explains everything:** `"Show me good bank heist movies"`  
→ TMDB searched for Crime/Thriller, RAG retrieves critical analysis of Rififi/Heat/Hell or High Water, web search gets current best-of lists. Synthesiser combines all three into a curated, explained, tone-aware answer no filter UI can produce.

---

## Repository Layout

```
pipeline/                          ← git root (worldwidejimmy/pipeline)
├── cineai/                        ← THIS project
│   ├── HANDOFF.md                 ← you are here
│   ├── README.md                  ← full project README
│   ├── docker-compose.yml         ← full stack: Milvus + backend + frontend
│   ├── nginx.conf                 ← production nginx for OVH + Cloudflare
│   ├── Makefile                   ← common operations
│   ├── start.sh                   ← dev startup script
│   ├── backend/
│   │   ├── .env.example           ← ALL config vars documented
│   │   ├── requirements.txt       ← Python dependencies
│   │   ├── scripts/
│   │   │   └── ingest.py          ← embed docs into Milvus
│   │   ├── docs/                  ← movie knowledge corpus for RAG
│   │   │   ├── heist/             → bank-heist-films-guide.md
│   │   │   ├── directors/         → christopher-nolan.md, martin-scorsese.md
│   │   │   ├── genres/            → sci-fi-cinema-guide.md
│   │   │   └── classics/          → horror-guide.md
│   │   └── src/
│   │       ├── config.py          ← centralised env var config
│   │       ├── main.py            ← FastAPI app + SSE streaming endpoint
│   │       ├── agents/
│   │       │   ├── supervisor.py  ← routes queries to agents (history-aware)
│   │       │   ├── tmdb_agent.py  ← intent extraction → TMDB API → answer
│   │       │   ├── rag_agent.py   ← Milvus retrieval → grounded answer
│   │       │   ├── search_agent.py← Tavily web search → grounded answer
│   │       │   └── synthesiser.py ← merges agents, maintains history
│   │       ├── tools/
│   │       │   ├── tmdb_client.py ← async TMDB REST client
│   │       │   ├── milvus_retriever.py ← vector similarity search
│   │       │   └── web_search.py  ← Tavily wrapper
│   │       └── graph/
│   │           └── pipeline.py    ← LangGraph StateGraph + MemorySaver
│   └── frontend/
│       ├── package.json
│       ├── vite.config.ts         ← port 5174, proxies /api → :8001
│       └── src/
│           ├── App.tsx            ← main layout, SSE client, conversation state
│           ├── types.ts           ← all SSE event types + TMDB types
│           ├── index.css          ← dark theme design system
│           └── components/
│               ├── PipelineGraph.tsx  ← animated node diagram
│               ├── AgentTimeline.tsx  ← Gantt execution chart
│               ├── EventLog.tsx       ← real-time typed event log
│               ├── ChunksPanel.tsx    ← RAG chunks + TMDB cards
│               └── MetricsBar.tsx     ← latency/tokens/routing footer
└── docs/                          ← pipeline project enterprise corpus
    ├── runbooks/, architecture/, postmortems/, ml-platform/, api/, policies/
```

---

## Architecture

```
Browser (port 80/443 via Cloudflare)
    │
  nginx (OVH)
    ├── /          → serves frontend static files (dist/)
    └── /api/*     → proxy_pass to FastAPI :8001
         │
    FastAPI (uvicorn :8001)
    GET /api/query?q=...&thread_id=...   → SSE stream
    GET /api/history?thread_id=...       → conversation history
    GET /api/trending                    → TMDB trending
    GET /api/search?q=...               → TMDB quick search
         │
    LangGraph StateGraph (compiled with MemorySaver)
         │
    ┌────┴─────────────────────────────────────┐
    │          supervisor_route               │  ← Groq llama-3.1-70b
    │    routes: tmdb|rag|search|combinations │
    └────┬──────────────┬──────────────┬──────┘
         │              │              │ (parallel fan-out)
    tmdb_agent      rag_agent    search_agent
    TMDB API        Milvus       Tavily API
    (httpx)         :19530       (optional)
         │              │              │
    └────┴──────────────┴──────────────┘
                    │
               synthesise
              Groq streaming
                    │
              SSE → browser
```

### LangGraph State (per conversation thread)
```python
class CineState(TypedDict, total=False):
    question: str         # current question
    history:  list[dict]  # [{q, a}, ...] last 10 turns — persisted by MemorySaver
    routing:  str         # supervisor decision
    tmdb_result:  str     # TMDB agent output
    rag_result:   str     # RAG agent output
    search_result: str    # web search output
    answer:   str         # final synthesised answer
```

### SSE Event Flow (what the frontend receives)
```
pipeline_start → routing_decision → agent_start (×N) →
  llm_start → token (streaming) → llm_end →
  chunks_retrieved / tmdb_results →
agent_end (×N) → done
```

---

## API Keys Required

| Key | Where to Get | Cost | Used For |
|---|---|---|---|
| `GROQ_API_KEY` | console.groq.com → API Keys | Free tier: 14,400 req/day | LLM inference (all agents) |
| `TMDB_BEARER_TOKEN` | themoviedb.org → Settings → API | Free | Movie/TV data |
| `OPENAI_API_KEY` | platform.openai.com | ~$0.02/1M tokens | Embeddings only (text-embedding-3-small) |
| `TAVILY_API_KEY` | tavily.com | Free: 1000 searches/month | Web search agent (optional) |

**TMDB note:** Use the **"API Read Access Token (v4 auth)"** — the long JWT-style token, not the short v3 API key. It goes in `TMDB_BEARER_TOKEN`.

**Without Tavily:** The web search agent gracefully returns "unavailable" and the other two agents still work fine.

**Without OpenAI:** You can run Ollama locally (`ollama pull nomic-embed-text`) and set `EMBEDDING_PROVIDER=ollama` in `.env`.

---

## OVH Server Setup (step by step)

### 1. Clone the repo

```bash
git clone https://github.com/worldwidejimmy/pipeline.git
cd pipeline
git checkout claude/review-pipeline-resume-cJuXv
cd cineai
```

### 2. Configure environment

```bash
cp backend/.env.example backend/.env
nano backend/.env
# Fill in: GROQ_API_KEY, TMDB_BEARER_TOKEN, OPENAI_API_KEY
# Set: MILVUS_URI=http://localhost:19530
```

### 3. Start the full stack with Docker Compose

```bash
# This starts: Milvus + etcd + MinIO + Attu + backend + frontend
docker compose up -d

# Check everything is healthy
docker compose ps
docker compose logs backend --tail=20
```

### 4. Ingest the movie knowledge corpus

```bash
# Wait for Milvus to be ready (about 30s after docker compose up)
docker compose exec backend python scripts/ingest.py docs/

# Verify ingestion
docker compose exec backend python -c "
from src.tools.milvus_retriever import get_vectorstore
vs = get_vectorstore()
print('Collection ready')
"
```

### 5. Test the API

```bash
# Health check
curl http://localhost:8001/api/health

# Quick movie search (no LLM needed)
curl "http://localhost:8001/api/search?q=inception"

# Full pipeline (SSE stream)
curl -N "http://localhost:8001/api/query?q=show+me+good+heist+movies&thread_id=test1"
```

### 6. Build and deploy frontend

```bash
cd frontend
npm install
npm run build
# Static files are in frontend/dist/ — nginx serves these
```

---

## Nginx + Cloudflare Setup

### Nginx config (see `nginx.conf` in this directory)

Key points:
- Serves `frontend/dist/` as static files for all non-API routes
- Proxies `/api/*` to uvicorn on `localhost:8001`
- SSE requires `proxy_buffering off` and specific timeout settings
- Cloudflare handles SSL termination — nginx can use HTTP internally

```bash
sudo cp nginx.conf /etc/nginx/sites-available/cineai
sudo ln -s /etc/nginx/sites-available/cineai /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### Cloudflare settings
- **SSL/TLS mode:** Full (not Full Strict — OVH nginx uses HTTP internally)
- **Minimum TLS:** 1.2
- **Cache:** Disable caching for `/api/*` paths (Page Rule or Cache Rule)
- **Timeout:** Set "Response Buffering" to OFF for SSE to work through Cloudflare
- **Rocket Loader:** OFF (breaks SSE)

---

## Development Mode (no Docker, local testing)

```bash
# Terminal 1: start Milvus stack from pipeline project
cd /path/to/pipeline
docker compose up -d

# Terminal 2: backend
cd cineai/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env
uvicorn src.main:app --host 0.0.0.0 --port 8001 --reload

# Terminal 3: frontend
cd cineai/frontend
npm install
npm run dev  # runs on :5174, proxies /api → :8001
```

Open: http://localhost:5174

---

## Makefile Commands

```bash
make up          # docker compose up -d (full stack)
make down        # docker compose down
make logs        # tail all service logs
make ingest      # embed docs/ into Milvus
make build       # build frontend static files
make dev         # start backend + frontend in dev mode
make test-api    # run curl smoke tests against running backend
```

---

## Current State — What Works

- [x] Multi-agent LangGraph pipeline (supervisor → tmdb/rag/search → synthesise)
- [x] SSE streaming — real-time typed events to frontend
- [x] Multi-turn conversation — MemorySaver + thread_id, supervisor history-aware
- [x] TMDB agent — search, discover, trending, person lookup, movie details
- [x] RAG agent — Milvus semantic search, citation-based answers
- [x] Web search agent — Tavily, graceful degradation if key missing
- [x] Synthesiser — merges agents, deduplicates, follow-up aware
- [x] Frontend observability: Pipeline Graph, Timeline, Event Log, Context Panel
- [x] Conversation history UI — shows previous turns, "New Chat" button
- [x] Movie knowledge corpus — heist, Nolan, Scorsese, sci-fi, horror
- [x] Ingest script — `scripts/ingest.py docs/`
- [x] Production nginx config + docker-compose

## Known Gaps / Roadmap (in priority order)

- [ ] **Hybrid search** — Milvus sparse (BM25) + dense. Currently dense-only. Fails on exact title matches ("The Dark Knight" vs "dark knight").
- [ ] **Streaming tokens in Event Log** — tokens currently only go to answer panel. Add a "Token Stream" sub-view to Event Log tab.
- [ ] **RAGAS evaluation** — no automated quality measurement. Add `scripts/eval.py` with 20 Q&A pairs covering heist/horror/director queries.
- [ ] **Redis semantic cache** — repeated queries hit Groq every time. `langchain.cache.RedisSemanticCache` with cosine threshold 0.95.
- [ ] **Watchlist** — PostgreSQL-backed. User saves movies to a named list. Frontend `/watchlist` route.
- [ ] **IMDb dataset ingest** — `datasets.imdbws.com` has free TSV files (title.basics, title.ratings, title.principals). Ingesting ratings for 10M+ titles would make RAG much richer.
- [ ] **"Why this recommendation?"** — add an explain mode that shows which sources contributed which facts.
- [ ] **Production Dockerfile** — backend and frontend currently rely on host Python/Node. Add multi-stage Dockerfile for proper containerisation.

---

## Demo Queries (run these to show the system working)

### Simple — TMDB only
```
"What is Inception about?"
"What are trending movies this week?"
"Show me horror movies with a rating above 8"
```

### Multi-source — the impressive ones
```
"Show me good bank heist movies"
→ TMDB (Crime/Thriller discover) + RAG (heist corpus) + web (best-of lists)

"Tell me about Christopher Nolan's directing style"
→ RAG (nolan.md) + web (current criticism)

"What's the best sci-fi film of all time and why?"
→ TMDB + RAG (sci-fi guide) + web
```

### Multi-turn — shows conversation memory
```
Turn 1: "Show me good bank heist movies"
Turn 2: "What about the director of Heat?"      ← supervisor knows context = Michael Mann
Turn 3: "What are his other films like that?"   ← still knows Michael Mann
Turn 4: "Which one should I watch first?"       ← can now recommend within Mann's filmography
```

### Follow-up that shows routing adaptation
```
Turn 1: "Who directed The Departed?"     → tmdb (quick lookup)
Turn 2: "Tell me more about his style"   → supervisor routes rag+search (needs depth)
Turn 3: "What's his latest film?"        → tmdb+search (recent news)
```

---

## Key Design Decisions (context for future changes)

**Why LangGraph over LangChain LCEL?**
LangGraph gives explicit state management and conditional fan-out. For multi-agent systems with parallel execution and conversation history, LCEL becomes spaghetti. LangGraph's `MemorySaver` makes multi-turn trivial.

**Why Groq?**
`llama-3.1-70b-versatile` on Groq is ~10x faster than OpenAI GPT-4o at 1/10th the cost. For a streaming UI where users watch tokens appear, latency matters enormously. Free tier is generous for development.

**Why Milvus over Chroma/Pinecone?**
Milvus handles billion-scale vectors in distributed mode (same collection, just add nodes). It supports native hybrid search (BM25 + dense) in v2.4+, which is the next planned feature. Chroma doesn't scale; Pinecone is expensive.

**Why TMDB over OMDB/IMDB?**
TMDB has a generous free API (50 req/s), active maintenance, poster images, and covers TV equally well. IMDB's API is expensive. OMDB is limited. TMDB is the industry standard for indie movie apps.

**Why SSE over WebSocket?**
SSE is one-directional (server → client) which matches our use case exactly. It's simpler, works through Cloudflare without extra configuration (unlike WebSockets which need specific CF settings), and EventSource reconnects automatically on drop.

**The `error` → `pipeline_error` rename:**
`EventSource` has a built-in `error` event for connection failures. Naming our pipeline errors `error` means they never reach the application handler — they're swallowed by the connection error handler. Renamed to `pipeline_error` on both ends.

---

## Files to Know

| File | Why You'd Edit It |
|---|---|
| `backend/src/config.py` | Add new env vars, change defaults |
| `backend/src/graph/pipeline.py` | Add new agent nodes, change routing logic |
| `backend/src/agents/supervisor.py` | Change routing rules or add new routing targets |
| `backend/src/tools/tmdb_client.py` | Add new TMDB endpoints (e.g., `/movie/{id}/reviews`) |
| `frontend/src/types.ts` | Add new SSE event types |
| `frontend/src/components/EventLog.tsx` | Change how events are displayed |
| `frontend/src/App.tsx` | Change layout, add new UI features |
| `backend/docs/` | Add more movie knowledge for RAG |
