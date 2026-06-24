"""
CineAI FastAPI backend.

Endpoints:
  POST /api/auth                              Validate preview password → session token
  GET  /api/query?q=<question>&thread_id=<id> SSE stream (pipeline events + answer)
  GET  /api/history?thread_id=<id>            Conversation history for a thread
  GET  /api/trending                          Trending movies from TMDB
  GET  /api/search?q=<title>                  Quick TMDB title search
  GET  /api/rules                             Routing rules JSON (Rules modal)
  GET  /api/health                            Health check
"""
from __future__ import annotations

import json
import time
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.graph.pipeline import build_pipeline, CineState, _get_memory
from src.tools import tmdb_client
from src import usage
from src.compare import compare_stream

app = FastAPI(title="SmartMovieSearch", version="2.0.0")

# ── Open access ──────────────────────────────────────────────────────────────
# The site is public. Anonymous visitors get a small per-IP free quota; signing
# in with the preview password lifts the limit (see src/usage.py). The password
# is validated server-side and never exposed in the JS bundle.
_PREVIEW_PASSWORD = usage.PREVIEW_PASSWORD
_ACCESS_TOKEN     = usage.ACCESS_TOKEN

_CORS_ORIGINS = [
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "https://smartmoviesearch.com",
    "https://www.smartmoviesearch.com",
]

# ── In-memory rate-limit tracker ────────────────────────────────────────────
# Set whenever a pipeline call hits a Claude 429; cleared when status ping succeeds.
_llm_rate_limit: dict | None = None   # {"message": str, "retry_in": str, "at": float}

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def _ip_limit_stream(gate: dict) -> AsyncIterator[str]:
    """One-shot SSE stream emitted when an anonymous IP is out of free credits."""
    mins = max(1, round(gate.get("reset_in", 0) / 60))
    yield _sse("pipeline_error", {
        "code":    "ip_limit",
        "message": (f"You've used your {gate['limit']} free searches for the hour. "
                    f"Sign in with the access password for unlimited searches, "
                    f"or try again in ~{mins} min."),
        "reset_in": gate.get("reset_in", 0),
    })


async def _oneshot_error(code: str, message: str) -> AsyncIterator[str]:
    yield _sse("pipeline_error", {"code": code, "message": message})


# ── Lightweight bot protection ────────────────────────────────────────────────
# (Turnstile can be layered on later for stronger guarantees.)
_ALLOWED_REFERER_HOSTS = ("smartmoviesearch.com", "localhost", "127.0.0.1")
_BOT_UA_MARKERS = (
    "bot", "spider", "crawler", "scrapy", "curl", "wget", "python-requests",
    "httpx", "go-http", "java/", "okhttp", "headless", "phantomjs", "slurp",
)


def _looks_like_bot(request: Request) -> bool:
    """Heuristic: empty/known-library User-Agent, or a present-but-foreign
    Origin/Referer. Absent Origin/Referer is allowed (privacy browsers, EventSource)."""
    ua = (request.headers.get("user-agent") or "").lower()
    if not ua or any(m in ua for m in _BOT_UA_MARKERS):
        return True
    for val in (request.headers.get("origin", ""), request.headers.get("referer", "")):
        if val and not any(h in val for h in _ALLOWED_REFERER_HOSTS):
            return True
    return False


_AGENT_NODES = {
    "supervisor_route", "tmdb_agent", "rag_agent", "search_agent", "music_agent", "synthesise"
}


def _sse(event_type: str, payload: dict) -> str:
    payload["ts"] = int(time.time() * 1000)
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


async def _stream_pipeline(question: str, thread_id: str, client_ip: str | None = None) -> AsyncIterator[str]:
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

                if ename == "music_agent":
                    raw = output.get("_music_raw", {})
                    detail = raw.get("detail", {})
                    if detail:
                        yield _sse("music_results", {
                            "artist": detail.get("name"),
                            "albums": detail.get("albums", [])[:5],
                            "genres": detail.get("genres", []),
                        })

                yield _sse("agent_end", payload)

            # ── LLM lifecycle (LangChain 1.2+ emits on_chat_model_* for BaseChatModel) ─

            elif etype == "on_chat_model_start":
                yield _sse("llm_start", {
                    "agent": current_agent,
                    "model": event.get("name", "claude"),
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
                # usage_metadata is a TypedDict (dict at runtime) on modern
                # langchain-core — use dict access, not getattr (which silently
                # returns 0). Some response_metadata fallbacks differ per provider.
                um = getattr(output, "usage_metadata", None) or {}
                if isinstance(um, dict):
                    prompt_t = um.get("input_tokens", 0) or 0
                    compl_t  = um.get("output_tokens", 0) or 0
                else:
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
        usage.add_tokens(total_prompt_tokens, total_completion_tokens, ip=client_ip)
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
            global _llm_rate_limit
            _llm_rate_limit = {
                "message":  "Claude API rate limit reached.",
                "retry_in": wait,
                "at":       time.time(),
            }
            yield _sse("pipeline_error", {
                "code":    "rate_limit",
                "message": f"API rate limit reached.{(' Try again in ' + wait + '.') if wait else ''}",
                "detail":  _sanitize(raw),
            })
        elif "401" in raw or "invalid_api_key" in raw.lower() or "authentication" in raw.lower():
            yield _sse("pipeline_error", {
                "code":    "auth_error",
                "message": "API key invalid or missing. Check ANTHROPIC_API_KEY / OPENAI_API_KEY in your .env file.",
                "detail":  _sanitize(raw),
            })
        elif "Connection" in raw or "connect" in raw.lower() or "timeout" in raw.lower():
            yield _sse("pipeline_error", {
                "code":    "connection_error",
                "message": "Could not reach an upstream API (Claude / TMDB / Tavily). Check network and service status.",
                "detail":  _sanitize(raw),
            })
        else:
            yield _sse("pipeline_error", {
                "code":    "pipeline_error",
                "message": "Something went wrong in the pipeline.",
                "detail":  _sanitize(raw),
            })
        return

    usage.add_tokens(total_prompt_tokens, total_completion_tokens, ip=client_ip)
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
    request:   Request,
    q:         str = Query(..., min_length=1),
    thread_id: str = Query(default="default"),
):
    """SSE stream: run pipeline for a question within a conversation thread.
    Anonymous callers spend one per-IP free credit; signed-in callers are unlimited."""
    if _looks_like_bot(request) and not usage.is_unlimited(request):
        return StreamingResponse(_oneshot_error("bot_blocked",
            "Automated access is not allowed. Use the website in a browser."),
            media_type="text/event-stream", headers=_SSE_HEADERS)
    gate = usage.consume(request)
    if not gate["allowed"]:
        if gate.get("blocked"):
            return StreamingResponse(_oneshot_error("blocked",
                "Access from your network has been blocked."),
                media_type="text/event-stream", headers=_SSE_HEADERS)
        return StreamingResponse(_ip_limit_stream(gate),
                                 media_type="text/event-stream", headers=_SSE_HEADERS)
    return StreamingResponse(_stream_pipeline(q, thread_id, usage.client_ip(request)),
                             media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/compare")
async def compare_query(
    request: Request,
    q:       str = Query(..., min_length=1),
):
    """SSE stream: answer the same question with and without RAG, side by side.
    Costs one free credit (same as a normal search) even though it makes two LLM calls."""
    if _looks_like_bot(request) and not usage.is_unlimited(request):
        return StreamingResponse(_oneshot_error("bot_blocked",
            "Automated access is not allowed. Use the website in a browser."),
            media_type="text/event-stream", headers=_SSE_HEADERS)
    gate = usage.consume(request)
    if not gate["allowed"]:
        if gate.get("blocked"):
            return StreamingResponse(_oneshot_error("blocked",
                "Access from your network has been blocked."),
                media_type="text/event-stream", headers=_SSE_HEADERS)
        return StreamingResponse(_ip_limit_stream(gate),
                                 media_type="text/event-stream", headers=_SSE_HEADERS)
    return StreamingResponse(compare_stream(q, usage.client_ip(request)),
                             media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/usage")
async def get_usage(request: Request):
    """Current per-IP free quota + daily token usage for the caller."""
    return usage.snapshot(request)


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
        "anthropic": bool(cfg.anthropic_api_key and not cfg.anthropic_api_key.startswith("sk-ant-your")),
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

    # ── Anthropic (Claude) ──────────────────────────────────────────────
    global _llm_rate_limit
    from src.llm import model_id
    mid = model_id()
    try:
        from langchain_core.messages import HumanMessage
        from src.llm import get_chat
        t0 = time.time()
        llm = get_chat(max_tokens=5)
        await llm.ainvoke([HumanMessage(content="hi")])
        latency = int((time.time() - t0) * 1000)
        # Ping succeeded — if we had a recent rate-limit (< 5 min ago), keep warning.
        if _llm_rate_limit and (time.time() - _llm_rate_limit["at"]) < 300:
            results["anthropic"] = {
                "status":     "rate_limited",
                "model":      mid,
                "latency_ms": latency,
                "retry_in":   _llm_rate_limit.get("retry_in", ""),
                "detail":     _llm_rate_limit["message"] + " (status ping uses minimal tokens)",
            }
        else:
            _llm_rate_limit = None   # clear stale warning
            results["anthropic"] = {"status": "ok", "model": mid, "latency_ms": latency}
    except Exception as e:
        raw = str(e)
        if "rate_limit" in raw or "429" in raw:
            import re
            wait = ""
            m = re.search(r"try again in ([\w.]+)", raw)
            if m: wait = m.group(1)
            _llm_rate_limit = {"message": "Rate limit reached.", "retry_in": wait, "at": time.time()}
            results["anthropic"] = {"status": "rate_limited", "model": mid,
                                "retry_in": wait, "detail": "Rate limit reached"}
        elif "401" in raw or "authentication" in raw.lower() or "invalid" in raw.lower():
            results["anthropic"] = {"status": "auth_error", "model": mid}
        else:
            results["anthropic"] = {"status": "error", "detail": raw[:120]}

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
    """Summarise the Milvus RAG knowledge base.

    The corpus has two very differently-shaped parts: a few dozen curated markdown
    docs (listed individually) and tens of thousands of Roger Ebert reviews
    (collapsed into one summary bucket — listing them all would be useless). Counts
    use Milvus count(*) so they stay accurate at any scale."""
    try:
        from pymilvus import MilvusClient
        from src.config import get_config
        from collections import Counter
        cfg = get_config()
        client = MilvusClient(uri=cfg.milvus_uri)

        def count(flt: str) -> int:
            return client.query(cfg.milvus_collection, filter=flt,
                                output_fields=["count(*)"])[0]["count(*)"]

        total_chunks  = count("")
        ebert_chunks  = count('source like "ebert/%"')

        # Markdown docs are few (~hundreds of rows) — safe to list individually.
        doc_rows = client.query(
            cfg.milvus_collection,
            filter='source like "docs/%"',
            output_fields=["source"],
            limit=16384,
        )
        counts = Counter(r["source"] for r in doc_rows)
        docs = [{"source": src, "chunks": n} for src, n in sorted(counts.items())]

        # Distinct review count (slug-level), de-duping amp/ mirror variants.
        review_slugs: set[str] = set()
        if ebert_chunks:
            it = client.query_iterator(
                cfg.milvus_collection,
                filter='source like "ebert/%"',
                output_fields=["source"],
                batch_size=16_000,
            )
            while True:
                batch = it.next()
                if not batch:
                    it.close()
                    break
                for r in batch:
                    slug = r["source"].removeprefix("ebert/").removeprefix("amp/")
                    review_slugs.add(slug)

        reviews = {
            "label":   "Roger Ebert Reviews",
            "reviews": len(review_slugs),
            "chunks":  ebert_chunks,
        } if ebert_chunks else None

        return {
            "total_chunks": total_chunks,
            "total_docs":   len(docs) + (1 if reviews else 0),
            "docs":         docs,
            "reviews":      reviews,
        }
    except Exception as exc:
        return {"total_chunks": 0, "total_docs": 0, "docs": [], "reviews": None, "error": str(exc)}


@app.get("/api/rules")
async def get_routing_rules():
    """Return the routing rules and keyword overrides used by the supervisor agent."""
    from src.agents.supervisor import (
        _FORCE_MUSIC_KEYWORDS,
        _FORCE_TMDB_KEYWORDS,
        SUPERVISOR_LLM_RULE_BULLETS,
    )
    from src.llm import model_id
    return {
        "model": model_id(),
        "agents": [
            {
                "id": "tmdb",
                "name": "TMDB Agent",
                "icon": "🎬",
                "description": "Real-time movie & TV database — ratings, cast, plot, trending, release info",
                "source": "themoviedb.org",
            },
            {
                "id": "rag",
                "name": "RAG Agent",
                "icon": "📚",
                "description": "Knowledge base search — film theory, director styles, history, deep analysis",
                "source": "Milvus vector DB",
            },
            {
                "id": "search",
                "name": "Web Search Agent",
                "icon": "🌐",
                "description": "Live web search — current news, this week's box office, just-released content",
                "source": "Tavily API",
            },
            {
                "id": "music",
                "name": "Music Agent",
                "icon": "🎵",
                "description": "Music artist & album data — discography, genres, release years, songwriting",
                "source": "MusicBrainz",
            },
        ],
        "routing_decisions": [
            { "key": "tmdb",         "description": "Movie/TV database only" },
            { "key": "rag",          "description": "Knowledge base only" },
            { "key": "search",       "description": "Live web search only" },
            { "key": "music",        "description": "Music database only" },
            { "key": "tmdb+rag",     "description": "Movie/TV data + deep analysis" },
            { "key": "tmdb+search",  "description": "Movie/TV data + current news" },
            { "key": "tmdb+music",   "description": "Movie/TV data + music (film composers, concert docs)" },
            { "key": "music+search", "description": "Music data + current news (new releases)" },
            { "key": "rag+search",   "description": "Knowledge base + current news" },
            { "key": "all",          "description": "All agents in parallel" },
        ],
        "keyword_overrides": {
            "description": "Deterministic rules checked before the LLM — these always win",
            "music": _FORCE_MUSIC_KEYWORDS,
            "tmdb":  _FORCE_TMDB_KEYWORDS,
        },
        "llm_rules": list(SUPERVISOR_LLM_RULE_BULLETS),
    }


class _AuthRequest(BaseModel):
    password: str

@app.post("/api/auth")
async def authenticate(body: _AuthRequest, request: Request):
    """Validate the preview password → session token. Locks an IP out after
    repeated failures to blunt brute-force attempts."""
    ip = usage.client_ip(request)
    if usage.auth_locked(ip):
        raise HTTPException(status_code=429,
                            detail="Too many failed attempts. Try again later.")
    if body.password and body.password == _PREVIEW_PASSWORD:
        usage.reset_auth_fails(ip)
        return {"token": _ACCESS_TOKEN}
    usage.record_auth_fail(ip)
    raise HTTPException(status_code=401, detail="Invalid password")


# ── Admin (gated by the same access token / password) ─────────────────────────
def _require_admin(request: Request) -> None:
    if not usage.is_unlimited(request):
        raise HTTPException(status_code=403, detail="Admin access required")


@app.get("/api/admin/usage")
async def admin_usage(request: Request):
    """Per-IP usage table + blacklist for the admin screen."""
    _require_admin(request)
    return usage.admin_snapshot()


class _BlacklistRequest(BaseModel):
    ip: str
    action: str   # "add" | "remove"

@app.post("/api/admin/blacklist")
async def admin_blacklist(body: _BlacklistRequest, request: Request):
    _require_admin(request)
    ip = body.ip.strip()
    if not ip:
        raise HTTPException(status_code=400, detail="ip required")
    if body.action == "add":
        usage.blacklist_add(ip)
    elif body.action == "remove":
        usage.blacklist_remove(ip)
    else:
        raise HTTPException(status_code=400, detail="action must be add|remove")
    return {"ok": True, "blacklist": usage.admin_snapshot()["blacklist"]}


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "smartmoviesearch-backend", "version": "2.0.0"}
