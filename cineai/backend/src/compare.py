"""
RAG vs no-RAG comparison stream.

Answers the same question two ways and streams both side by side:
  • "rag"  — bare LLM grounded on retrieved Milvus chunks
  • "base" — the same LLM with no retrieval (parametric knowledge only)

This isolates the effect of retrieval — the cleanest demo of what RAG buys you.
Both calls share the model/prompt skeleton; only the injected context differs.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from src.config import get_config
from src.tools.milvus_retriever import retrieve
from src import usage

_RAG_SYSTEM = """You are a film expert with deep knowledge of cinema history, theory, and criticism.

Answer the user's question using ONLY the context documents provided below.
Cite sources by referencing the document name at the end of relevant sentences.
If the documents don't contain sufficient information, clearly state that.
Do NOT hallucinate or invent information not present in the context.

Context:
{context}
"""

_BASE_SYSTEM = """You are a film expert with deep knowledge of cinema history, theory, and criticism.

Answer the user's question from your own knowledge. You have no reference documents
to draw on — rely only on what you already know."""


def _sse(event_type: str, payload: dict) -> str:
    payload["ts"] = int(time.time() * 1000)
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


def _llm() -> ChatGroq:
    cfg = get_config()
    return ChatGroq(
        model=cfg.groq_model,
        temperature=0.1,
        api_key=cfg.groq_api_key,
        max_tokens=900,
        streaming=True,
    )


async def compare_stream(question: str) -> AsyncIterator[str]:
    """Stream both grounded and ungrounded answers concurrently as SSE events.

    Events:
      compare_start   {question}
      chunks_retrieved {chunks, count}
      compare_token   {side: 'rag'|'base', content}
      compare_side_end {side, prompt_tokens, completion_tokens}
      compare_error   {side, message}
      compare_done    {total_prompt_tokens, total_completion_tokens, latency_ms}
    """
    start_ms = int(time.time() * 1000)
    yield _sse("compare_start", {"question": question})

    # ── Retrieve once; feed the grounded side ────────────────────────────────
    try:
        retrieval = await retrieve(question)
    except Exception as exc:
        yield _sse("compare_error", {"side": "rag", "message": f"Retrieval failed: {exc}"})
        retrieval = {"chunks": [], "context": ""}

    chunks = retrieval.get("chunks", [])
    yield _sse("chunks_retrieved", {"chunks": chunks[:5], "count": len(chunks)})

    context = retrieval.get("context", "")
    rag_messages = [
        SystemMessage(content=_RAG_SYSTEM.format(
            context=context or "(no relevant documents were found)")),
        HumanMessage(content=question),
    ]
    base_messages = [
        SystemMessage(content=_BASE_SYSTEM),
        HumanMessage(content=question),
    ]

    queue: asyncio.Queue = asyncio.Queue()
    totals = {"prompt": 0, "completion": 0}

    async def run(side: str, messages: list) -> None:
        full = None
        try:
            async for chunk in _llm().astream(messages):
                full = chunk if full is None else full + chunk
                content = chunk.content if isinstance(getattr(chunk, "content", None), str) else ""
                if content:
                    await queue.put(_sse("compare_token", {"side": side, "content": content}))
            um = getattr(full, "usage_metadata", None) or {}
            p = um.get("input_tokens", 0) or 0
            c = um.get("output_tokens", 0) or 0
            totals["prompt"] += p
            totals["completion"] += c
            await queue.put(_sse("compare_side_end", {
                "side": side, "prompt_tokens": p, "completion_tokens": c,
            }))
        except Exception as exc:
            await queue.put(_sse("compare_error", {"side": side, "message": str(exc)[:200]}))
        finally:
            await queue.put(None)  # sentinel: this side finished

    tasks = [
        asyncio.create_task(run("rag", rag_messages)),
        asyncio.create_task(run("base", base_messages)),
    ]

    finished = 0
    while finished < len(tasks):
        item = await queue.get()
        if item is None:
            finished += 1
            continue
        yield item

    await asyncio.gather(*tasks, return_exceptions=True)

    usage.add_tokens(totals["prompt"], totals["completion"])
    yield _sse("compare_done", {
        "total_prompt_tokens":     totals["prompt"],
        "total_completion_tokens": totals["completion"],
        "latency_ms":              int(time.time() * 1000) - start_ms,
    })
