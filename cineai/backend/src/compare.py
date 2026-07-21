"""
RAG vs no-RAG comparison stream.

Answers the same question two ways and streams both side by side:
  • "rag"  — bare LLM grounded on retrieved Milvus chunks
  • "base" — the same LLM with no retrieval (parametric knowledge only)

then runs a BLIND judge: a third LLM call receives both answers in random
order labeled A/B, is NOT told which used retrieval, and rules on which is
stronger. The frontend reveals the mapping after the verdict — the cleanest
demo of whether retrieval actually helped.

Alongside its own compare_* events the stream emits the standard pipeline
events (pipeline_start / agent_* / llm_* / chunks_retrieved / done) so the
observability panel — graph, timeline, event log, context, metrics — works
in compare mode too.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from typing import AsyncIterator

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm import get_chat, model_id
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

_JUDGE_SYSTEM = """You are an impartial judge evaluating two anonymous answers to the same
film/TV/music question. One answer MAY have used retrieved reference documents; the
other answered from memory alone. You are NOT told which is which — judge only what
is on the page.

Compare them on: specificity (names, dates, ratings, concrete details), apparent
factual reliability (flag claims that look invented), grounding/citations, and how
directly they answer the question.

Reply in markdown, at most ~150 words:
**Verdict:** Answer A / Answer B / Tie — one sentence why.

Then 2-3 bullets on the concrete differences that decided it. Do not speculate
about which answer used documents."""


def _sse(event_type: str, payload: dict) -> str:
    payload["ts"] = int(time.time() * 1000)
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


def _llm(max_tokens: int = 900):
    return get_chat(temperature=0.1, max_tokens=max_tokens, streaming=True)


async def compare_stream(question: str, ip: str | None = None) -> AsyncIterator[str]:
    """Stream grounded + ungrounded answers concurrently, then a blind judge verdict.

    Compare-specific events:
      compare_start    {question}
      compare_token    {side: 'rag'|'base', content}
      compare_side_end {side, prompt_tokens, completion_tokens}
      compare_error    {side, message}
      judge_start      {a_side}           # which side is "Answer A" (revealed to UI only)
      judge_token      {content}
      judge_end        {a_side, prompt_tokens, completion_tokens}
      compare_done     {total_prompt_tokens, total_completion_tokens, latency_ms}

    Plus the standard pipeline events for the observability panel.
    """
    start_ms = int(time.time() * 1000)
    yield _sse("compare_start", {"question": question})
    yield _sse("pipeline_start", {"question": question})

    # ── Retrieve once; feed the grounded side ────────────────────────────────
    yield _sse("agent_start", {"agent": "rag_agent"})
    t0 = time.time()
    try:
        retrieval = await retrieve(question)
    except Exception as exc:
        yield _sse("compare_error", {"side": "rag", "message": f"Retrieval failed: {exc}"})
        retrieval = {"chunks": [], "context": ""}

    chunks = retrieval.get("chunks", [])
    yield _sse("chunks_retrieved", {"chunks": chunks[:5], "count": len(chunks)})
    yield _sse("agent_end", {"agent": "rag_agent", "latency_ms": int((time.time() - t0) * 1000)})

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
    answers = {"rag": "", "base": ""}
    _AGENT = {"rag": "rag_answer", "base": "base_answer"}

    async def run(side: str, messages: list) -> None:
        agent = _AGENT[side]
        t_side = time.time()
        await queue.put(_sse("agent_start", {"agent": agent}))
        await queue.put(_sse("llm_start", {"agent": agent, "model": model_id()}))
        full = None
        try:
            async for chunk in _llm().astream(messages):
                full = chunk if full is None else full + chunk
                content = chunk.content if isinstance(getattr(chunk, "content", None), str) else ""
                if content:
                    answers[side] += content
                    await queue.put(_sse("compare_token", {"side": side, "content": content}))
            um = getattr(full, "usage_metadata", None) or {}
            p = um.get("input_tokens", 0) or 0
            c = um.get("output_tokens", 0) or 0
            totals["prompt"] += p
            totals["completion"] += c
            await queue.put(_sse("llm_end", {
                "agent": agent, "prompt_tokens": p, "completion_tokens": c,
            }))
            await queue.put(_sse("compare_side_end", {
                "side": side, "prompt_tokens": p, "completion_tokens": c,
            }))
        except Exception as exc:
            await queue.put(_sse("compare_error", {"side": side, "message": str(exc)[:200]}))
        finally:
            await queue.put(_sse("agent_end", {
                "agent": agent, "latency_ms": int((time.time() - t_side) * 1000)}))
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

    # ── Blind judge: both answers, random order, provenance withheld ─────────
    if answers["rag"].strip() and answers["base"].strip():
        a_side = random.choice(["rag", "base"])
        b_side = "base" if a_side == "rag" else "rag"
        yield _sse("judge_start", {"a_side": a_side})
        yield _sse("agent_start", {"agent": "judge"})
        yield _sse("llm_start", {"agent": "judge", "model": model_id()})
        t_judge = time.time()
        judge_messages = [
            SystemMessage(content=_JUDGE_SYSTEM),
            HumanMessage(content=(
                f"Question: {question}\n\n"
                f"## Answer A\n{answers[a_side][:4000]}\n\n"
                f"## Answer B\n{answers[b_side][:4000]}"
            )),
        ]
        full = None
        try:
            async for chunk in _llm(max_tokens=400).astream(judge_messages):
                full = chunk if full is None else full + chunk
                content = chunk.content if isinstance(getattr(chunk, "content", None), str) else ""
                if content:
                    yield _sse("judge_token", {"content": content})
            um = getattr(full, "usage_metadata", None) or {}
            p = um.get("input_tokens", 0) or 0
            c = um.get("output_tokens", 0) or 0
            totals["prompt"] += p
            totals["completion"] += c
            yield _sse("llm_end", {"agent": "judge", "prompt_tokens": p, "completion_tokens": c})
            yield _sse("judge_end", {"a_side": a_side, "prompt_tokens": p, "completion_tokens": c})
        except Exception as exc:
            yield _sse("compare_error", {"side": "judge", "message": str(exc)[:200]})
        finally:
            yield _sse("agent_end", {
                "agent": "judge", "latency_ms": int((time.time() - t_judge) * 1000)})

    usage.add_tokens(totals["prompt"], totals["completion"], ip=ip)
    total_ms = int(time.time() * 1000) - start_ms
    yield _sse("done", {
        "total_latency_ms":        total_ms,
        "total_prompt_tokens":     totals["prompt"],
        "total_completion_tokens": totals["completion"],
        "agents_used":             ["rag_agent", "rag_answer", "base_answer", "judge"],
    })
    yield _sse("compare_done", {
        "total_prompt_tokens":     totals["prompt"],
        "total_completion_tokens": totals["completion"],
        "latency_ms":              total_ms,
    })
