"""
Keep one third-party outage from failing the whole answer.

Each data-source agent (TMDB, MusicBrainz, web search) is wrapped so that an HTTP error
from its upstream API, after the client's own retries, becomes a short "unavailable"
note in that agent's result slot. The synthesiser then answers from the other sources
(usually the knowledge base) and says the live data was missing, instead of the request
ending in a pipeline_error.

Only upstream HTTP failures are absorbed (httpx errors and timeouts). Bugs, LLM API
errors and anything else still raise, so they stay visible. The raw error text never
reaches the prompt or the answer: the note is fixed text, and the details go to the log
and to the `_upstream_error` field (streamed as a `source_unavailable` event, which the
T4 canaries use to tell an upstream outage from a regression).
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Awaitable, Callable

import httpx

log = logging.getLogger(__name__)

UPSTREAM_ERRORS: tuple[type[BaseException], ...] = (httpx.HTTPError, asyncio.TimeoutError)

Node = Callable[[dict], Awaitable[dict]]


def unavailable_note(source: str) -> str:
    return (f"Live data from {source} is unavailable right now (the service did not respond), "
            f"so there is nothing from {source} for this question.")


def is_transient(status: int | None) -> bool:
    """An outage on their side (5xx, 429, timeout/connection error), not a fault of ours (other 4xx)."""
    return status is None or status == 429 or status >= 500


def resilient(node: Node, *, result_key: str, source: str) -> Node:
    @functools.wraps(node)
    async def wrapped(state: dict) -> dict:
        try:
            return await node(state)
        except UPSTREAM_ERRORS as exc:
            resp = getattr(exc, "response", None) if isinstance(exc, httpx.HTTPStatusError) else None
            status = resp.status_code if resp is not None else None
            log.warning("%s unavailable (%s): answering without it", source,
                        status or type(exc).__name__)
            return {result_key: unavailable_note(source),
                    "_upstream_error": {"source": source, "status": status,
                                        "transient": is_transient(status)}}
    return wrapped
