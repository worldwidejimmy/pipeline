"""
CineAI FastAPI backend.

Endpoints:
  GET /api/query?q=<question>    SSE stream of pipeline events + final answer
  GET /api/trending              Trending movies from TMDB (JSON)
  GET /api/search?q=<title>      Quick TMDB title search (JSON)
  GET /api/health                Health check
"""
from __future__ import annotations

import json
import time
from typing import AsyncIterator

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk

from src.graph.pipeline import build_pipeline, CineState
from src.tools import tmdb_client

app = FastAPI(title="CineAI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Agent node names (must match graph node names) ────────────────────────────

_AGENT_NODES = {
    "supervisor_route", "tmdb_agent", "rag_agent", "search_agent", "synthesise"
}

# ── Event helpers ─────────────────────────────────────────────────────────────

def _sse(event_type: str, payload: dict) -> str:
    """Format a Server-Sent Event."""
    payload["ts"] = int(time.time() * 1000)
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


# ── SSE pipeline stream ───────────────────────────────────────────────────────

async def _stream_pipeline(question: str) -> AsyncIterator[str]:
    """
    Run the LangGraph pipeline and transform astream_events output into
    frontend-friendly SSE events.
    """
    pipeline = build_pipeline()
    initial_state: CineState = {"question": question}

    start_ms = int(time.time() * 1000)
    current_agent: str = "supervisor_route"
    agent_start_times: dict[str, int] = {}
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    agents_used: list[str] = []
    synthesis_streaming = False

    yield _sse("pipeline_start", {"question": question})

    try:
        async for event in pipeline.astream_events(initial_state, version="v2"):
            etype = event["event"]
            ename = event.get("name", "")
            data  = event.get("data", {})
            now   = int(time.time() * 1000)

            # ── Node lifecycle ────────────────────────────────────────────────

            if etype == "on_chain_start" and ename in _AGENT_NODES:
                current_agent = ename
                agent_start_times[ename] = now
                if ename not in ("supervisor_route",):
                    agents_used.append(ename)
                synthesis_streaming = (ename == "synthesise")
                yield _sse("agent_start", {"agent": ename})

            elif etype == "on_chain_end" and ename in _AGENT_NODES:
                latency = now - agent_start_times.get(ename, now)
                output = data.get("output", {}) or {}

                payload: dict = {"agent": ename, "latency_ms": latency}

                # Attach routing decision when supervisor finishes
                if ename == "supervisor_route":
                    routing = output.get("routing", "tmdb")
                    payload["routing"] = routing
                    yield _sse("routing_decision", {"routing": routing, "agent": ename})

                # Attach retrieved chunks when RAG agent finishes
                if ename == "rag_agent":
                    chunks = output.get("_rag_chunks", [])
                    if chunks:
                        yield _sse("chunks_retrieved", {
                            "chunks": chunks[:5],  # send top 5 to frontend
                            "count": len(chunks),
                        })

                # Attach TMDB results when TMDB agent finishes
                if ename == "tmdb_agent":
                    raw = output.get("_tmdb_raw", {})
                    results = raw.get("results", [])
                    if not results and "detail" in raw:
                        results = [raw["detail"]]
                    if results:
                        yield _sse("tmdb_results", {
                            "results": results[:5],
                            "count": len(results),
                        })

                yield _sse("agent_end", payload)

            # ── LLM lifecycle ─────────────────────────────────────────────────

            elif etype == "on_llm_start":
                model_name = ""
                if hasattr(data.get("serialized"), "get"):
                    model_name = data["serialized"].get("kwargs", {}).get("model_name", "")
                yield _sse("llm_start", {
                    "agent": current_agent,
                    "model": model_name or "groq",
                })

            elif etype == "on_llm_stream":
                chunk = data.get("chunk")
                if chunk is None:
                    continue
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                if content:
                    yield _sse("token", {
                        "content": content,
                        "agent": current_agent,
                        "is_final": synthesis_streaming,
                    })

            elif etype == "on_llm_end":
                output = data.get("output", {})
                usage = {}
                if hasattr(output, "usage_metadata") and output.usage_metadata:
                    usage = output.usage_metadata
                elif isinstance(output, dict):
                    usage = output.get("usage_metadata", {})

                prompt_t  = usage.get("input_tokens", 0) if isinstance(usage, dict) else getattr(usage, "input_tokens", 0)
                compl_t   = usage.get("output_tokens", 0) if isinstance(usage, dict) else getattr(usage, "output_tokens", 0)
                total_prompt_tokens     += prompt_t
                total_completion_tokens += compl_t

                yield _sse("llm_end", {
                    "agent": current_agent,
                    "prompt_tokens": prompt_t,
                    "completion_tokens": compl_t,
                })

    except Exception as exc:
        yield _sse("error", {"message": str(exc)})
        return

    total_ms = int(time.time() * 1000) - start_ms
    yield _sse("done", {
        "total_latency_ms": total_ms,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "agents_used": list(dict.fromkeys(agents_used)),  # deduplicated, ordered
    })


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/query")
async def query_stream(q: str = Query(..., min_length=1)):
    """SSE stream: runs the full pipeline and emits observability events."""
    return StreamingResponse(
        _stream_pipeline(q),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


@app.get("/api/trending")
async def trending(media_type: str = "movie"):
    """Trending movies or TV shows from TMDB."""
    return await tmdb_client.get_trending(media_type)


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1)):
    """Quick TMDB title search — no RAG, no LLM."""
    return await tmdb_client.search_movies(q)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "cineai-backend"}
