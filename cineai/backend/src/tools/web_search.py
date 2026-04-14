"""Tavily web search wrapper for live movie/entertainment news."""
from __future__ import annotations

import time
from typing import Any

from src.config import get_config


async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """
    Search the web for current movie/TV information using Tavily.
    Falls back gracefully if Tavily API key is not configured.
    """
    cfg = get_config()

    if not cfg.tavily_api_key:
        return {
            "results": [],
            "formatted": "Web search is unavailable (no TAVILY_API_KEY configured).",
            "latency_ms": 0,
            "query": query,
        }

    try:
        from tavily import TavilyClient  # type: ignore

        t0 = time.time()
        client = TavilyClient(api_key=cfg.tavily_api_key)
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
        )
        latency_ms = int((time.time() - t0) * 1000)

        results = response.get("results", [])
        formatted_parts = []
        for i, r in enumerate(results, 1):
            formatted_parts.append(
                f"[{i}] {r.get('title', 'No title')}\n"
                f"URL: {r.get('url', '')}\n"
                f"{r.get('content', '')[:400]}"
            )

        return {
            "results": results,
            "formatted": "\n\n".join(formatted_parts),
            "latency_ms": latency_ms,
            "query": query,
        }

    except Exception as exc:
        return {
            "results": [],
            "formatted": f"Web search failed: {exc}",
            "latency_ms": 0,
            "query": query,
        }
