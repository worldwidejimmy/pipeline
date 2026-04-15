# CineAI — Movie, TV & Music Intelligence Platform

> **What a live LLM brings to movie discovery that no static database can.**

A multi-agent RAG pipeline built with LangGraph, Groq, TMDB, Milvus, MusicBrainz, and optional Tavily.
React + Vite frontend with a full LangSmith-style observability dashboard.

**Operations handoff:** [../HANDOFF.md](../HANDOFF.md) (Ebert scrape, `/api/rules`, generated `data/`, deployment notes).

---

## The Core Insight

Static movie sites (IMDB, Rotten Tomatoes, Letterboxd) are **filter machines**.
They can rank by rating, filter by genre tag, and show you popularity rankings.

**They cannot answer:** *"Show me good bank heist movies."*

Here's why — and what this system does differently.

### The "Bank Heist" Problem

TMDB has no "heist" genre. *Heat* is tagged `Crime/Drama/Thriller`. *Ocean's Eleven*
is `Crime/Thriller`. *Dog Day Afternoon* is `Crime/Drama`. A filter UI returns nothing
or wrong things. The LLM knows the **cultural concept** of a heist film regardless of
how a database tagged it 20 years ago.

| Capability | Static Database | CineAI Pipeline |
|---|---|---|
| Find "heist movies" | Keyword search on title/description | Conceptual reasoning across genre knowledge |
| Define "good" | Single metric (RT%, IMDB score) | Multi-signal synthesis: ratings + critical analysis + current consensus |
| Explain *why* a film is great | No | Yes — RAG retrieves critical context, LLM reasons over it |
| Surface hidden gems | Popularity-biased algorithms | LLM knows critically acclaimed but underseen films |
| Match tone/vibe | Dropdown filters | Natural language: "like Heat but faster-paced" |
| Conversational refinement | Restart filters every time | Multi-turn: "I liked that, but less violent" |
| Cross-domain queries | Not possible | "Heist movie with a Blade Runner aesthetic" |
| Constraint reasoning | Basic (runtime filter) | "90 min, can watch with my mom, no graphic violence" |

### What the Pipeline Does for "Show me good bank heist movies"

```
User query → Supervisor (routes to: tmdb + rag + search [+ music when needed])
  │
  ├─▶ TMDB Agent
  │     • Extracts intent: genre=Crime/Thriller, sort=vote_average
  │     • Fetches: Heat, Inside Man, The Town, Ocean's Eleven
  │     • Gets full details: cast, ratings, similar films
  │
  ├─▶ RAG Agent  
  │     • Embeds query → searches Milvus knowledge base
  │     • Retrieves: critical analysis of heist genre, Rififi as genre origin,
  │       Heat's influence on modern crime cinema, Dog Day Afternoon context
  │     • LLM generates citation-grounded analysis
  │
  └─▶ Web Search Agent
        • Searches: "best bank heist movies of all time"
        • Gets: current critical consensus, 2024 releases, hidden gem listicles
        • LLM generates news-grounded summary

Synthesiser: merges all three into a ranked, explained, tone-aware list
```

**Output:** A curated list with ratings, explanations of *why* each film works,
awareness of tone differences, hidden gems alongside blockbusters — something no
filter UI can produce.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    React + Vite Frontend                 │
│  ┌──────────────┐  ┌────────────────────────────────┐   │
│  │  Query +     │  │    Observability Dashboard      │   │
│  │  Answer      │  │  ┌──────┬──────┬───────┬─────┐ │   │
│  │  Panel       │  │  │Graph │Time- │Events │Ctx  │ │   │
│  │              │  │  │      │line  │ Log   │     │ │   │
│  └──────────────┘  └────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────┘
                           │ SSE (typed events, real-time)
┌──────────────────────────▼──────────────────────────────┐
│                   FastAPI Backend :8001                  │
│                                                          │
│  /api/query → LangGraph astream_events() → SSE stream   │
│  /api/trending → TMDB trending                          │
│  /api/search  → TMDB quick search                       │
│  /api/rules   → routing rules JSON (Rules modal)        │
└────────────┬──────────────┬──────────────┬──────────────┘
             │              │              │
    ┌────────▼───┐  ┌───────▼──┐  ┌───────▼──────┐
    │ TMDB Agent │  │ RAG Agent│  │ Search Agent │
    │            │  │          │  │              │
    │ TMDB API   │  │ Milvus   │  │ Tavily Web   │
    │ (httpx)    │  │ :19530   │  │ Search API   │
    └────────────┘  └──────────┘  └──────────────┘
             │              │              │
    ┌────────▼──────────────▼──────────────▼──────┐
    │              Synthesiser Node                │
    │     Groq LLM (GROQ_MODEL in .env, streaming) │
    └─────────────────────────────────────────────┘
```

### LangGraph State Machine

```
START → supervisor_route
           │
     routing decision
     (tmdb|rag|search|music|tmdb+music|tmdb+rag|tmdb+search|rag+search|all)
           │
    ┌──────┼──────┐
    ▼      ▼      ▼
  tmdb   rag  search (+ music agent when routed — not shown in ASCII)
    │      │      │
    └──────┴──────┘
           │
       synthesise
           │
          END
```

### Observability Events (SSE)

The backend transforms LangGraph's `astream_events()` into typed frontend events:

| Event | Triggered When |
|---|---|
| `pipeline_start` | Query submitted |
| `routing_decision` | Supervisor picks agents |
| `agent_start` / `agent_end` | Each node activates / completes |
| `llm_start` / `llm_end` | LLM call begins / finishes (with token counts) |
| `token` | Streaming token from any LLM call |
| `tmdb_results` | TMDB agent returns movie data |
| `chunks_retrieved` | RAG agent returns chunks with similarity scores |
| `done` | Pipeline complete (latency, total tokens, agents used) |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Orchestration | LangGraph StateGraph (async, streaming) |
| LLM | Groq — model from `GROQ_MODEL` in `.env` (e.g. `llama-3.1-8b-instant` for free tier) |
| Movie / TV Data | TMDB API (free tier) — real-time search, details, trending |
| Music Data | MusicBrainz (no key) |
| RAG / Vector DB | Milvus (shared with pipeline project on :19530) |
| Embeddings | OpenAI `text-embedding-3-small` |
| Web Search | Tavily API (optional) |
| Backend | FastAPI + SSE streaming |
| Frontend | React 18 + TypeScript + Vite |
| Observability | LangSmith tracing + custom SSE dashboard |

---

## Quick Start

### Prerequisites

- Pipeline project's Milvus stack running: `cd /home/user/pipeline && docker-compose up -d`
- API keys (all free tiers work):
  - [Groq](https://console.groq.com) — LLM inference
  - [TMDB](https://www.themoviedb.org/settings/api) — movie data (use "API Read Access Token")
  - [OpenAI](https://platform.openai.com) — embeddings only (tiny cost)
  - [Tavily](https://tavily.com) — optional web search

### Setup

```bash
cd /home/user/cineai

# Configure API keys
cp backend/.env.example backend/.env
nano backend/.env   # fill in GROQ_API_KEY, TMDB_BEARER_TOKEN, OPENAI_API_KEY

# Install and start everything
./start.sh
```

Open **http://localhost:5174**

### Ingest movie knowledge base (optional, for RAG)

Drop `.txt`, `.md`, or `.pdf` movie review/analysis files into `docs/`, then:

```bash
cd /home/user/cineai/backend
.venv/bin/python scripts/ingest.py docs/
```

Or point it at the pipeline project's corpus to reuse those docs:

```bash
.venv/bin/python scripts/ingest.py /home/user/pipeline/docs/
```

**Roger Ebert reviews** (optional, same Milvus collection): scrape to `backend/data/` then `python scripts/ingest_ebert.py` — see [../HANDOFF.md](../HANDOFF.md).

**Routing rules in the UI:** header button opens the modal; data from `GET /api/rules`.

---

## Example Queries

| Query | Routing | What the Pipeline Does |
|---|---|---|
| "Show me good bank heist movies" | `tmdb+rag+search` | TMDB Crime/Thriller + critical analysis + best-of lists |
| "What is Inception about?" | `tmdb` | Full TMDB details, cast, similar films |
| "Christopher Nolan's directing style" | `rag+search` | Film theory corpus + current criticism |
| "What's trending in theaters this week?" | `tmdb+search` | TMDB trending + box office news |
| "Best horror movies with a rating above 8" | `tmdb` | TMDB discover with filters |
| "Movies similar to Blade Runner but faster paced" | `tmdb+rag` | TMDB similar + aesthetic analysis |
| "Hidden gems from the 1970s" | `rag+search` | Critical corpus + curated lists |

---

## Frontend Observability Dashboard

The right half of the UI is a real-time observability panel with 4 tabs:

**Graph** — Animated pipeline node diagram. Nodes pulse when active, dim when not
routed to. Shows routing decision and per-node latency.

**Timeline** — Gantt-style execution chart. Horizontal bars for each agent showing
relative start time and duration. Shows parallelism when multiple agents run.

**Events** — Live event log with icons and color-coding by agent. Expandable JSON
for TMDB results and RAG chunks. Relative timestamps from query start.

**Context** — Retrieved RAG chunks with cosine similarity scores and source docs.
TMDB movie cards with posters, ratings, and overviews.

**Metrics bar** (always visible) — Total latency · token count · LLM call count ·
routing decision · agents used.

---

## Roadmap

- [ ] Multi-turn conversation (LangGraph `MemorySaver` for session memory)
- [ ] Streaming tokens shown in real-time in Events tab
- [ ] "Why this recommendation?" — explainability mode
- [ ] Watchlist with persistent storage (PostgreSQL)
- [ ] RAGAS evaluation on recommendation quality
- [ ] Ingest IMDb TSV datasets for richer RAG corpus
- [ ] Semantic caching with Redis for repeated queries
- [x] Hybrid search (BM25 + dense) in Milvus
- [x] Music agent (MusicBrainz) and music routing
- [x] Rules modal (`/api/rules`) and keyword overrides in supervisor

---

## Project Structure

```
cineai/
├── backend/
│   ├── src/
│   │   ├── config.py              Central config (env vars, LLM/embedding selection)
│   │   ├── main.py                FastAPI app + SSE event streaming
│   │   ├── agents/
│   │   │   ├── supervisor.py      Routes queries to appropriate agents
│   │   │   ├── tmdb_agent.py      Intent extraction → TMDB API → grounded answer
│   │   │   ├── rag_agent.py       Milvus retrieval → citation-based answer
│   │   │   ├── search_agent.py    Tavily web search → grounded answer
│   │   │   ├── music_agent.py     MusicBrainz → grounded answer
│   │   │   └── synthesiser.py     Merges all agent outputs → final answer
│   │   ├── tools/
│   │   │   ├── tmdb_client.py     Async TMDB API wrapper (search, discover, person)
│   │   │   ├── milvus_retriever.py  Vector similarity search
│   │   │   ├── musicbrainz_client.py MusicBrainz HTTP client
│   │   │   └── web_search.py      Tavily wrapper with graceful degradation
│   │   └── graph/
│   │       └── pipeline.py        LangGraph StateGraph + conditional fan-out
│   ├── docs/                      RAG markdown corpus
│   ├── data/                      Generated scrape output (gitignored)
│   ├── scripts/
│   │   ├── ingest.py              Markdown/text → Milvus
│   │   ├── scrape_ebert.py        Ebert via Wayback Machine
│   │   └── ingest_ebert.py        Ebert JSONL → Milvus
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── App.tsx                Main layout + SSE connection + state
│   │   ├── types.ts               All SSE event types + TMDB types
│   │   ├── index.css              Dark theme design system
│   │   └── components/
│   │       ├── PipelineGraph.tsx  Animated node diagram
│   │       ├── RoutingRulesModal.tsx  Supervisor rules from /api/rules
│   │       ├── AgentTimeline.tsx  Gantt-style execution timeline
│   │       ├── EventLog.tsx       Real-time typed event log
│   │       ├── ChunksPanel.tsx    RAG chunks + TMDB movie cards
│   │       └── MetricsBar.tsx     Summary metrics footer
│   ├── package.json
│   └── vite.config.ts             Port 5174, proxies /api → :8001
└── start.sh                       One-command startup script
```
