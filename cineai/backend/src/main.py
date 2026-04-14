"""
CineAI FastAPI backend.

Endpoints:
  GET /api/query?q=<question>&thread_id=<id>  SSE stream (pipeline events + answer)
  GET /api/history?thread_id=<id>             Conversation history for a thread
  GET /api/trending                           Trending movies from TMDB
  GET /api/search?q=<title>                   Quick TMDB title search
  GET /api/health                             Health check
"""
from __future__ import annotations

import json
import time
from typing import AsyncIterator

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.graph.pipeline import build_pipeline, CineState, _get_memory
from src.tools import tmdb_client

app = FastAPI(title="SmartMovieSearch", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "https://smartmoviesearch.com",
        "https://www.smartmoviesearch.com",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

_AGENT_NODES = {
    "supervisor_route", "tmdb_agent", "rag_agent", "search_agent", "synthesise"
}


def _sse(event_type: str, payload: dict) -> str:
    payload["ts"] = int(time.time() * 1000)
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


async def _stream_pipeline(question: str, thread_id: str) -> AsyncIterator[str]:
    """Run the pipeline and stream typed SSE events to the frontend."""
    pipeline = build_pipeline()
    config   = {"configurable": {"thread_id": thread_id}}

    # Load existing history from checkpointer so agents have context
    existing = pipeline.get_state(config)
    history  = (existing.values or {}).get("history", []) if existing.values else []

    initial_state: CineState = {
        "question": question,
        "history":  history,
    }

    start_ms = int(time.time() * 1000)
    current_agent: str = "supervisor_route"
    agent_start_times: dict[str, int] = {}
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    agents_used: list[str] = []
    synthesis_streaming = False

    yield _sse("pipeline_start", {
        "question":    question,
        "thread_id":   thread_id,
        "turn_number": len(history) + 1,
    })

    try:
        async for event in pipeline.astream_events(
            initial_state, config=config, version="v2"
        ):
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
                output  = data.get("output", {}) or {}
                payload: dict = {"agent": ename, "latency_ms": latency}

                if ename == "supervisor_route":
                    routing = output.get("routing", "tmdb")
                    payload["routing"] = routing
                    yield _sse("routing_decision", {"routing": routing, "agent": ename})

                if ename == "rag_agent":
                    chunks = output.get("_rag_chunks", [])
                    if chunks:
                        yield _sse("chunks_retrieved", {
                            "chunks": chunks[:5],
                            "count": len(chunks),
                        })

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

            # ── LLM lifecycle (LangChain 1.2+ emits on_chat_model_* for BaseChatModel) ─

            elif etype == "on_chat_model_start":
                yield _sse("llm_start", {
                    "agent": current_agent,
                    "model": event.get("name", "groq"),
                })

            elif etype == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk is None:
                    continue
                content = chunk.content if isinstance(getattr(chunk, "content", None), str) else ""
                if content:
                    yield _sse("token", {
                        "content":  content,
                        "agent":    current_agent,
                        "is_final": synthesis_streaming,
                    })

            elif etype == "on_chat_model_end":
                output = data.get("output")
                um = getattr(output, "usage_metadata", None)
                prompt_t = getattr(um, "input_tokens", 0) or 0
                compl_t  = getattr(um, "output_tokens", 0) or 0
                total_prompt_tokens     += prompt_t
                total_completion_tokens += compl_t
                yield _sse("llm_end", {
                    "agent":             current_agent,
                    "prompt_tokens":     prompt_t,
                    "completion_tokens": compl_t,
                })

    except Exception as exc:
        raw = str(exc)
        if "rate_limit_exceeded" in raw or "429" in raw:
            # Parse retry time if present (e.g. "Please try again in 4m10s")
            import re
            wait = ""
            m = re.search(r"try again in ([\d]+m[\d.]+s|[\d.]+s)", raw)
            if m:
                wait = f" Try again in {m.group(1)}."
            yield _sse("pipeline_error", {
                "code":    "rate_limit",
                "message": f"API rate limit reached — daily free-tier token quota used up.{wait}",
                "detail":  raw,
            })
        elif "401" in raw or "invalid_api_key" in raw.lower() or "authentication" in raw.lower():
            yield _sse("pipeline_error", {
                "code":    "auth_error",
                "message": "API key invalid or missing. Check GROQ_API_KEY / OPENAI_API_KEY in your .env file.",
                "detail":  raw,
            })
        elif "Connection" in raw or "connect" in raw.lower() or "timeout" in raw.lower():
            yield _sse("pipeline_error", {
                "code":    "connection_error",
                "message": "Could not reach an upstream API (Groq / TMDB / Tavily). Check network and service status.",
                "detail":  raw,
            })
        else:
            yield _sse("pipeline_error", {
                "code":    "pipeline_error",
                "message": "Something went wrong in the pipeline.",
                "detail":  raw,
            })
        return

    total_ms = int(time.time() * 1000) - start_ms
    yield _sse("done", {
        "total_latency_ms":        total_ms,
        "total_prompt_tokens":     total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "agents_used":             list(dict.fromkeys(agents_used)),
    })


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/query")
async def query_stream(
    q:         str = Query(..., min_length=1),
    thread_id: str = Query(default="default"),
):
    """SSE stream: run pipeline for a question within a conversation thread."""
    return StreamingResponse(
        _stream_pipeline(q, thread_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/history")
async def get_history(thread_id: str = Query(default="default")):
    """Return conversation history for a thread."""
    pipeline = build_pipeline()
    config   = {"configurable": {"thread_id": thread_id}}
    state    = pipeline.get_state(config)
    history  = (state.values or {}).get("history", []) if state.values else []
    return {"thread_id": thread_id, "turns": history}


@app.delete("/api/history")
async def clear_history(thread_id: str = Query(default="default")):
    """Clear conversation history for a thread (start fresh)."""
    # MemorySaver doesn't expose a delete — we write empty history
    pipeline = build_pipeline()
    config   = {"configurable": {"thread_id": thread_id}}
    await pipeline.aupdate_state(config, {"history": [], "answer": ""})
    return {"thread_id": thread_id, "cleared": True}


@app.get("/api/trending")
async def trending(media_type: str = "movie"):
    return await tmdb_client.get_trending(media_type)


@app.get("/api/search")
async def search(q: str = Query(..., min_length=1)):
    return await tmdb_client.search_movies(q)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "smartmoviesearch-backend", "version": "2.0.0"}
