"""
Search Agent — runs a live web search via Tavily and uses the LLM to
synthesise a concise, cited answer from the results.

Role in the pipeline
────────────────────
The supervisor routes here when the question requires up-to-date or external
information not present in the internal knowledge base.  This agent:
  1. Calls the Tavily web search tool with the user's query
  2. Passes the search results + query to the LLM
  3. Returns a structured answer string back into the LangGraph state
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import get_config
from src.tools.web_search import web_search

SEARCH_SYSTEM_PROMPT = """\
You are a research assistant that answers questions based on live web search \
results. Summarise the most relevant findings clearly and concisely. \
Cite the result number (e.g. [1], [2]) for each fact you include. \
If the search results don't answer the question, say so.
"""


def run_search_agent(state: dict) -> dict:
    """
    LangGraph node function.

    Expects state keys:
      - "query"  (str) : the user's original question

    Adds to state:
      - "search_result"  (str) : the synthesised answer from web search
    """
    cfg = get_config()
    query: str = state["query"]

    raw_results = web_search(query)

    llm = cfg.get_llm()
    messages = [
        SystemMessage(content=SEARCH_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Search results:\n\n{raw_results}\n\n"
                f"---\n\nQuestion: {query}\n\nAnswer:"
            )
        ),
    ]

    response = llm.invoke(messages)
    answer: str = response.content if hasattr(response, "content") else str(response)

    return {**state, "search_result": answer}
