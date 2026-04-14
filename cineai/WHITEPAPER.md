# SmartMovieSearch — Technical White Paper

**A production multi-agent AI system for real-time movie intelligence**  
`https://smartmoviesearch.com` · `github.com/worldwidejimmy/pipeline`

---

## Overview

SmartMovieSearch answers natural-language questions about movies and TV shows that no static database or simple search engine can handle. A question like *"Show me good bank heist movies"* requires understanding that heist is a theme, not a genre tag — then pulling live data, curated knowledge, and current web results simultaneously, and synthesising them into a single coherent answer. The system does this in under five seconds, streaming the response token by token as it is generated.

The project is a full-stack production application built with a modern AI stack: a **multi-agent LangGraph pipeline** on the backend, **Milvus hybrid vector search** for the knowledge base, **Groq** for fast LLM inference, and a **React** frontend that visualises the AI pipeline in real time.

---

## The Problem

Movie databases like TMDB are exhaustive but rigid. They expose genre filters, release years, and ratings — but they cannot answer "films with the same moral ambiguity as *No Country for Old Men*" or "does *Project Hail Mary* represent Ryan Gosling's best work?" These questions require:

1. **Semantic understanding** of the question
2. **Real-time structured data** (cast, ratings, release dates)
3. **Curated domain knowledge** (critical analysis, directorial style, genre history)
4. **Current web context** (new releases, recent reviews)
5. **Synthesis** across all three sources into a coherent, cited answer

No single API or model provides all of this. The solution is a **multi-agent architecture** where specialised agents work in parallel and a synthesiser merges their outputs.

---

## Architecture

```
User (browser)
    │  HTTPS via Cloudflare CDN
    │
  Nginx (reverse proxy, OVH VPS)
    ├── /          → React frontend (static, port 5174)
    └── /api/*     → FastAPI backend (uvicorn, port 8001)
                        │  Server-Sent Events stream
                   LangGraph StateGraph
                        │
              ┌─────────┴──────────┐
              │   supervisor_route  │  ← decides which agents to invoke
              └──┬──────┬──────┬───┘
                 │      │      │  (parallel fan-out)
           tmdb_agent  rag_agent  search_agent
           TMDB API    Milvus     Tavily Web
                 │      │      │
              └──┴──────┴──────┴───┘
                        │
                   synthesise         ← merges, streams answer
                        │
                  SSE → browser
```

---

## Core Technologies

### LangGraph — Multi-Agent Orchestration

LangGraph is a framework for building stateful, multi-step AI workflows as directed graphs. Each node in the graph is an agent function; edges represent conditional routing. A shared `StateGraph` object holds the conversation state across turns, persisted by `MemorySaver` so the system remembers context across a multi-turn conversation.

**Why not a single LLM call?** A single prompt cannot simultaneously search a live API, query a vector database, and do a web search. Agents decompose the problem so each specialist gets the right tool for the right task.

**Why LangGraph over LangChain LCEL?** LangGraph provides explicit state management and conditional fan-out. For parallel agents and multi-turn memory, LCEL chains become unmanageable. LangGraph's graph abstraction makes the data flow auditable and extensible.

### Milvus — Hybrid Vector Search

Milvus is a distributed vector database that stores the movie knowledge corpus (critical essays on heist films, director retrospectives, genre guides). It powers the RAG agent using **hybrid search**: a combination of sparse (BM25) and dense (embedding) retrieval, fused with Reciprocal Rank Fusion (RRF).

- **Dense search** (OpenAI `text-embedding-3-small`): converts text to 1536-dimensional vectors; finds semantically similar content even when keywords differ
- **Sparse search** (BM25, computed natively by Milvus 2.5+): exact keyword matching for proper nouns, titles, and specific terms
- **RRF fusion**: combines both ranked lists into one, getting the best of precision and recall

**Why not just embeddings?** A query for "Christopher Nolan" should find documents that contain those exact words, not just semantically similar ones. BM25 handles this; embeddings alone can miss it.

### Groq — Fast LLM Inference

Groq runs `llama-3.3-70b-versatile` on custom LPU (Language Processing Unit) hardware. It delivers roughly 10× the token throughput of comparable cloud GPU providers, which matters enormously in a streaming UI where users watch tokens appear in real time. The supervisor, all agents, and the synthesiser all use Groq.

**Why streaming?** Waiting 8–10 seconds for a complete response before showing anything creates a perception of a broken application. Streaming the answer as it is generated — via Server-Sent Events — makes the system feel instant.

### FastAPI + Server-Sent Events (SSE)

The backend is a FastAPI application that exposes a single streaming endpoint: `GET /api/query?q=...&thread_id=...`. It runs the LangGraph pipeline with `astream_events()` and translates internal events (agent start/end, LLM token, TMDB results, RAG chunks) into a typed SSE stream consumed by the browser.

**Why SSE over WebSockets?** SSE is unidirectional (server → client), which matches this use case exactly. It works through Cloudflare without any special configuration, and `EventSource` in the browser reconnects automatically on drop. WebSockets require additional Cloudflare settings and bidirectional protocol overhead that is unnecessary here.

### React + Vite Frontend

The frontend is a React/TypeScript SPA built with Vite. It maintains a live `EventSource` connection per query and renders the pipeline in real time across four tabs: an animated **pipeline graph** (node status), a **Gantt timeline** (agent latency), a **typed event log**, and a **context panel** (retrieved chunks and TMDB cards). A dark/light theme toggle persists preference to `localStorage`.

### Docker Compose + Nginx + Cloudflare

The full stack (Milvus + etcd + MinIO + backend + frontend) runs as Docker Compose services on a single OVH VPS. Nginx acts as a reverse proxy: it serves the compiled React static files for all non-API routes and proxies `/api/*` to uvicorn. Cloudflare sits in front for SSL termination, DDoS protection, and CDN caching of static assets.

---

## A Query, Step by Step

*"Show me good bank heist movies"*

1. **Supervisor** receives the question, calls Groq to classify intent → routes to all three agents in parallel: `tmdb + rag + search`
2. **TMDB agent** calls Groq to extract intent (`discover`, genre: Crime/Thriller) → hits TMDB API → calls Groq to write a grounded answer citing specific films and ratings
3. **RAG agent** embeds the query with OpenAI → runs hybrid BM25+dense search in Milvus → retrieves chunks from the heist film corpus → calls Groq to synthesise a knowledge-based answer
4. **Search agent** sends the query to Tavily → retrieves current web results → calls Groq to ground the answer
5. **Synthesiser** receives all three agent outputs, calls Groq to merge them into a single coherent answer, deduplicating overlapping film recommendations and citing sources
6. **SSE stream** delivers `pipeline_start`, `routing_decision`, `agent_start/end`, `token` (×N), `tmdb_results`, `chunks_retrieved`, and `done` events — the frontend renders each one as it arrives

Total latency: typically 3–6 seconds. The user sees tokens appearing within ~1 second of submitting the query.

---

## What Makes It Interesting

| Aspect | What was done |
|---|---|
| **Hybrid retrieval** | Sparse BM25 + dense embeddings fused with RRF — outperforms either alone |
| **Parallel agents** | TMDB, RAG, and web search run concurrently, not sequentially |
| **Multi-turn memory** | `MemorySaver` + `thread_id` gives the supervisor conversation context across turns |
| **Observable pipeline** | Every internal event streams to the UI — the AI process is not a black box |
| **Filmography comparison** | `movie_and_person` intent fetches movie detail + actor filmography in parallel; ranks by rating |
| **Structured error UX** | Rate limits, auth failures, and network errors surface as categorised, friendly banners |
| **Production deployed** | Live on OVH VPS behind Cloudflare with Docker Compose, nginx reverse proxy, and SSL |

---

## Technology Summary

| Layer | Technology | Role |
|---|---|---|
| Orchestration | LangGraph 1.1 | Multi-agent state machine with conditional routing and memory |
| LLM inference | Groq (llama-3.3-70b) | All reasoning: routing, intent extraction, answer generation |
| Vector DB | Milvus 2.5 | Hybrid BM25 + dense search over movie knowledge corpus |
| Embeddings | OpenAI text-embedding-3-small | 1536-dim dense vectors for semantic retrieval |
| Movie data | TMDB API | Real-time film/TV metadata, cast, ratings, trending |
| Web search | Tavily API | Current web results as a third retrieval source |
| Backend | FastAPI + uvicorn | Async SSE streaming, typed event protocol |
| Frontend | React + TypeScript + Vite | Real-time pipeline visualisation, conversation UI |
| Infrastructure | Docker Compose + Nginx + Cloudflare | Containerised deployment, reverse proxy, SSL/CDN |
