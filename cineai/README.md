# CineAI — Movie, TV & Music Intelligence Platform

> **What a live LLM brings to movie discovery that no static database can.**

A multi-agent RAG pipeline built with LangGraph, Anthropic Claude, TMDB, Milvus, MusicBrainz, and optional Tavily.
React + Vite chat frontend: every answer shows a step trace of how it was produced, with an
optional LangSmith-style observability dashboard (⚡) and a 🧪 Evals & ops panel.

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
User query → Understand (a follow-up is rewritten as a standalone question; no LLM call on a first question)
  → KB lookup (knowledge base first: relevant / nothing useful / Haiku grader in the unsure band)
  → Supervisor (routes to: tmdb + rag + search [+ music when needed]; RAG joins if the KB is relevant)
  │
  ├─▶ TMDB Agent
  │     • Extracts intent: genre=Crime/Thriller, sort=vote_average
  │     • Fetches: Heat, Inside Man, The Town, Ocean's Eleven
  │     • Gets full details: cast, ratings, similar films
  │
  ├─▶ RAG Agent  
  │     • Reuses the passages the KB lookup judged useful (no second search)
  │     • e.g. critical analysis of heist genre, Rififi as genre origin,
  │       Heat's influence on modern crime cinema, Dog Day Afternoon context
  │     • LLM generates citation-grounded analysis
  │
  └─▶ Web Search Agent
        • Searches: "best bank heist movies of all time"
        • Gets: current critical consensus, 2024 releases, hidden gem listicles
        • LLM generates news-grounded summary

Synthesiser: merges by question, not by source — one ranked, explained, tone-aware list,
sources credited inline, disagreements noted where they occur
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
│  │  Chat +      │  │  Observability Dashboard (⚡)   │   │
│  │  step trace  │  │  ┌──────┬──────┬───────┬─────┐ │   │
│  │  per answer  │  │  │Graph │Time- │Events │Ctx  │ │   │
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
│  /api/ops/overview → jobs + ingest history (Evals & ops)│
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
    │  Claude (DEFAULT_MODEL_TIER in .env, streaming) │
    └─────────────────────────────────────────────┘
```

### LangGraph State Machine

```
START → understand → kb_lookup → supervisor_route
           │
     routing decision
     (tmdb|rag|search|music|tmdb+music|tmdb+rag|tmdb+search|rag+search|all)
     + KB adjustment (relevant → add rag; nothing useful → drop rag)
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
| `rewrite` | A follow-up was rewritten as a standalone question |
| `kb_lookup` | Knowledge-base verdict and how it was decided (score vs. thresholds, or grader) |
| `routing_decision` | Supervisor picks agents (raw decision, keyword or model, KB adjustment) |
| `agent_start` / `agent_end` | Each node activates / completes |
| `llm_start` / `llm_end` | LLM call begins / finishes (with token counts) |
| `token` | Streaming token from any LLM call |
| `tmdb_results` | TMDB agent returns movie data |
| `chunks_retrieved` | RAG agent's passages with cosine similarity |
| `search_results` / `music_results` | Web / MusicBrainz results |
| `guardrail` | LinkGuard removed links from the answer |
| `done` | Pipeline complete (latency, total tokens, agents used, model) |

LLM events are attributed to the LangGraph node that made the call (`langgraph_node`
metadata), so parallel agents' tokens no longer get mixed up.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Orchestration | LangGraph StateGraph (async, streaming) |
| LLM | Anthropic Claude — tier from `DEFAULT_MODEL_TIER` in `.env` (`haiku` default, `sonnet`/`opus` available) |
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

- Docker with Compose (the stack includes Milvus, etcd and MinIO; nothing else to install)
- API keys (TMDB/Tavily have free tiers; Anthropic is paid):
  - [Anthropic](https://console.anthropic.com) — LLM inference (paid; Haiku is the cheap default)
  - [TMDB](https://www.themoviedb.org/settings/api) — movie data (use "API Read Access Token")
  - [OpenAI](https://platform.openai.com) — embeddings only (tiny cost)
  - [Tavily](https://tavily.com) — optional web search

### Setup

```bash
git clone https://github.com/worldwidejimmy/pipeline.git
cd pipeline/cineai

# Configure API keys (keep the file private: chmod 600)
cp backend/.env.example backend/.env
nano backend/.env   # fill in ANTHROPIC_API_KEY, TMDB_BEARER_TOKEN, OPENAI_API_KEY

# Start everything (Milvus + backend + frontend)
make up             # = docker compose up -d
```

Open **http://localhost:5174**

### Ingest the knowledge base (for RAG)

The curated Markdown guides live in `backend/docs/` (mounted into the backend container):

```bash
make ingest         # chunks, embeds and loads backend/docs/ into Milvus
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

## Frontend

**Chat.** Questions appear as chat messages; the question box docks at the bottom once a
conversation starts. Each answer keeps a **step trace** (small gray italics): the follow-up
rewrite, the knowledge-base verdict and how it was decided, routing (the router's raw decision
as a chip, e.g. `tmdb+rag`, plus any knowledge-base adjustment), each source's result with a
source chip (themoviedb.org, Milvus vector DB, Tavily API, MusicBrainz), per step the free
lookups vs. Claude calls with purpose and tokens in/out, what the combiner received, guardrail
link removals and the model. Older turns fold to one line: sources · seconds · tokens in
(cached %) / out · model. **Show prompt & response** under a step opens exactly what went in
and out: the source's raw data (cyan), each Claude call's system and user messages (indigo) and
Claude's reply (purple), so you can see which words came from TMDB, MusicBrainz, the web or the
knowledge base and which from Claude. **🕘 Chats** keeps earlier conversations (in this browser
only); "+ New chat" never discards one.

**🧪 Evals & ops** (header): each eval tier (what / how / when / latest); scheduled jobs (UTC and
local time, what each does, whether it uses an LLM, last run); ingest & embeddings (source →
chunking → embedding → index → query time, plus the nightly ingest history: new reviews,
passages embedded, tokens, cost, index size). Data: `GET /api/ops/overview`.

The old "Compare RAG vs no-RAG" toggle was removed from the UI (Claude already knows famous
films, so it always looked bad); `/api/compare` remains for evals.

### Observability dashboard (optional, ⚡ header button)

A real-time observability panel with 4 tabs:

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

- [x] Multi-turn conversation (LangGraph `MemorySaver`) with follow-up rewriting
- [x] Knowledge base first, with a calibrated relevance gate
- [x] Hybrid search (BM25 + dense) in Milvus
- [x] Music agent (MusicBrainz) and music routing
- [x] Rules modal (`/api/rules`) and keyword overrides in supervisor
- [x] Automated evals (seven tiers, nightly) and the MCP server

Open work: **[../TODO.md](../TODO.md)**.

---

## Project Structure

```
cineai/
├── backend/
│   ├── src/
│   │   ├── config.py              Central config (env vars, LLM/embedding selection)
│   │   ├── main.py                FastAPI app + SSE event streaming
│   │   ├── kb_gate.py             Relevance-gate thresholds + RAG-agent adjustment
│   │   ├── ops.py                 Public Evals & ops data (allow-listed)
│   │   ├── agents/
│   │   │   ├── understand.py      Follow-up → standalone question
│   │   │   ├── kb_lookup.py       Knowledge base first (search + gate + grader)
│   │   │   ├── supervisor.py      Routes queries to appropriate agents
│   │   │   ├── tmdb_agent.py      Intent extraction → TMDB API → grounded answer
│   │   │   ├── rag_agent.py       kb_lookup's passages → citation-based answer
│   │   │   ├── search_agent.py    Tavily web search → grounded answer
│   │   │   ├── music_agent.py     MusicBrainz → grounded answer
│   │   │   └── synthesiser.py     Merges all agent outputs → final answer
│   │   ├── tools/
│   │   │   ├── tmdb_client.py     Async TMDB API wrapper (search, discover, person)
│   │   │   ├── milvus_retriever.py  Hybrid search + per-passage cosine similarity
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
│   │       ├── StepTrace.tsx      Per-answer step trace + one-line summary
│   │       ├── OpsModal.tsx       🧪 Evals & ops
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
