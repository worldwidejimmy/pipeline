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

# ── In-memory rate-limit tracker ────────────────────────────────────────────
# Set whenever a pipeline call hits a Groq 429; cleared when status ping succeeds.
_groq_rate_limit: dict | None = None   # {"message": str, "retry_in": str, "at": float}

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
        import re as _re
        raw = str(exc)

        def _sanitize(text: str) -> str:
            """Remove account identifiers from error text while keeping actionable info."""
            # "in organization org_XXXX service tier on_demand" — drop silently
            text = _re.sub(r"\s+in organization\s+\S+\s+service tier\s+\S+", "", text)
            # Any remaining bare org_/user_/proj_ tokens
            text = _re.sub(r"\borg_[A-Za-z0-9]+\b", "", text)
            text = _re.sub(r"\buser_[A-Za-z0-9]+\b", "", text)
            text = _re.sub(r"\bproj_[A-Za-z0-9]+\b", "", text)
            return text.strip()

        if "rate_limit_exceeded" in raw or "429" in raw:
            wait = ""
            m = _re.search(r"try again in ([\d]+m[\d.]+s|[\d.]+s)", raw)
            if m:
                wait = m.group(1)
            global _groq_rate_limit
            _groq_rate_limit = {
                "message":  "Daily free-tier token quota used up (100k tokens/day).",
                "retry_in": wait,
                "at":       time.time(),
            }
            yield _sse("pipeline_error", {
                "code":    "rate_limit",
                "message": f"API rate limit reached — daily free-tier token quota used up.{(' Try again in ' + wait + '.') if wait else ''}",
                "detail":  _sanitize(raw),
            })
        elif "401" in raw or "invalid_api_key" in raw.lower() or "authentication" in raw.lower():
            yield _sse("pipeline_error", {
                "code":    "auth_error",
                "message": "API key invalid or missing. Check GROQ_API_KEY / OPENAI_API_KEY in your .env file.",
                "detail":  _sanitize(raw),
            })
        elif "Connection" in raw or "connect" in raw.lower() or "timeout" in raw.lower():
            yield _sse("pipeline_error", {
                "code":    "connection_error",
                "message": "Could not reach an upstream API (Groq / TMDB / Tavily). Check network and service status.",
                "detail":  _sanitize(raw),
            })
        else:
            yield _sse("pipeline_error", {
                "code":    "pipeline_error",
                "message": "Something went wrong in the pipeline.",
                "detail":  _sanitize(raw),
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


@app.get("/api/status")
async def service_status():
    """Return health of all services and API key presence (not values)."""
    import os, time, httpx
    from src.config import get_config
    cfg = get_config()

    results: dict = {}

    # ── API key presence ────────────────────────────────────────────────
    results["keys"] = {
        "groq":    bool(cfg.groq_api_key   and not cfg.groq_api_key.startswith("gsk_your")),
        "openai":  bool(cfg.openai_api_key and not cfg.openai_api_key.startswith("sk-your")),
        "tmdb":    bool(cfg.tmdb_bearer_token and len(cfg.tmdb_bearer_token) > 20),
        "tavily":  bool(os.getenv("TAVILY_API_KEY", "").startswith("tvly-") and
                        not os.getenv("TAVILY_API_KEY","").endswith("_here")),
    }

    # ── Milvus ──────────────────────────────────────────────────────────
    try:
        from pymilvus import MilvusClient
        t0 = time.time()
        client = MilvusClient(uri=cfg.milvus_uri)
        stats  = client.get_collection_stats(cfg.milvus_collection)
        results["milvus"] = {
            "status":   "ok",
            "latency_ms": int((time.time() - t0) * 1000),
            "chunks":   stats.get("row_count", 0),
            "collection": cfg.milvus_collection,
        }
    except Exception as e:
        results["milvus"] = {"status": "error", "detail": str(e)[:120]}

    # ── Groq ────────────────────────────────────────────────────────────
    global _groq_rate_limit
    try:
        from langchain_groq import ChatGroq
        from langchain_core.messages import HumanMessage
        t0 = time.time()
        llm = ChatGroq(model=cfg.groq_model, api_key=cfg.groq_api_key, max_tokens=5)
        await llm.ainvoke([HumanMessage(content="hi")])
        latency = int((time.time() - t0) * 1000)
        # Ping succeeded — if we had a recent rate-limit (< 5 min ago), it may still
        # affect real queries (tiny pings use almost no tokens). Keep warning if recent.
        if _groq_rate_limit and (time.time() - _groq_rate_limit["at"]) < 300:
            results["groq"] = {
                "status":     "rate_limited",
                "model":      cfg.groq_model,
                "latency_ms": latency,
                "retry_in":   _groq_rate_limit.get("retry_in", ""),
                "detail":     _groq_rate_limit["message"] + " (status ping uses minimal tokens)",
            }
        else:
            _groq_rate_limit = None   # clear stale warning
            results["groq"] = {"status": "ok", "model": cfg.groq_model, "latency_ms": latency}
    except Exception as e:
        raw = str(e)
        if "rate_limit" in raw or "429" in raw:
            import re
            wait = ""
            m = re.search(r"try again in ([\w.]+)", raw)
            if m: wait = m.group(1)
            _groq_rate_limit = {"message": "Daily token quota used up.", "retry_in": wait, "at": time.time()}
            results["groq"] = {"status": "rate_limited", "model": cfg.groq_model,
                                "retry_in": wait, "detail": "Daily token quota used up"}
        elif "401" in raw or "invalid" in raw.lower():
            results["groq"] = {"status": "auth_error", "model": cfg.groq_model}
        else:
            results["groq"] = {"status": "error", "detail": raw[:120]}

    # ── TMDB ────────────────────────────────────────────────────────────
    try:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{cfg.tmdb_base_url}/configuration",
                headers={"Authorization": f"Bearer {cfg.tmdb_bearer_token}"},
            )
            r.raise_for_status()
        results["tmdb"] = {"status": "ok", "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        results["tmdb"] = {"status": "error", "detail": str(e)[:80]}

    return results


@app.get("/api/knowledge")
async def knowledge_base():
    """Return a summary of what's in the Milvus RAG knowledge base."""
    try:
        from pymilvus import MilvusClient
        from src.config import get_config
        cfg = get_config()
        client = MilvusClient(uri=cfg.milvus_uri)
        rows = client.query(
            cfg.milvus_collection,
            filter="",
            output_fields=["source"],
            limit=5000,
        )
        from collections import Counter
        counts = Counter(r["source"] for r in rows)
        docs = [{"source": src, "chunks": n} for src, n in sorted(counts.items())]
        return {
            "total_chunks": sum(counts.values()),
            "total_docs": len(docs),
            "docs": docs,
        }
    except Exception as exc:
        return {"total_chunks": 0, "total_docs": 0, "docs": [], "error": str(exc)}


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "smartmoviesearch-backend", "version": "2.0.0"}
