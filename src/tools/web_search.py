"""
Web search tool — wraps the Tavily API via langchain-tavily.

Tavily is purpose-built for LLM agents: it returns clean, structured results
optimised for context windows, unlike raw Google/Bing results.

Free tier: https://app.tavily.com  (1,000 searches/month)
"""
from __future__ import annotations

from langchain_core.tools import Tool

from src.config import get_config


def web_search(query: str) -> str:
    """
    Run a live web search via Tavily and return a formatted string of results.

    Falls back to a helpful error message if the API key is missing, so the
    pipeline degrades gracefully rather than crashing.
    """
    cfg = get_config()

    if not cfg.tavily_api_key:
        return (
            "Web search is unavailable: TAVILY_API_KEY is not set. "
            "Sign up for a free key at https://app.tavily.com and add it to .env."
        )

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=cfg.tavily_api_key)
        response = client.search(
            query=query,
            max_results=cfg.search_max_results,
            include_answer=True,          # Tavily's own AI summary
            include_raw_content=False,    # keep context window lean
        )

        parts: list[str] = []

        if response.get("answer"):
            parts.append(f"Summary: {response['answer']}\n")

        for i, result in enumerate(response.get("results", []), start=1):
            title = result.get("title", "No title")
            url = result.get("url", "")
            content = result.get("content", "").strip()
            parts.append(f"[{i}] {title}\n{url}\n{content}")

        return "\n\n---\n\n".join(parts) if parts else "No results found."

    except Exception as exc:
        return f"Web search failed: {exc}"


def build_search_tool() -> Tool:
    """Return a LangChain Tool wrapping the Tavily web search."""
    return Tool(
        name="web_search",
        func=web_search,
        description=(
            "Search the live web for up-to-date information. Use this tool when "
            "the question requires recent news, current events, or information that "
            "may not be in the internal knowledge base. Input should be a clear, "
            "concise search query."
        ),
    )
