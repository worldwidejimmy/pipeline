"""
TMDB Agent — fetches real-time movie/TV data and generates a grounded answer.

Strategy:
  1. Use an LLM to extract intent + entities from the question
  2. Call the appropriate TMDB endpoints
  3. Use an LLM to generate a grounded, citation-rich answer from the results
"""
from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from src.config import get_config
from src.tools import tmdb_client

_EXTRACT_SYSTEM = """Extract structured search intent from the user's movie/TV question.

Respond with JSON only (no markdown, no explanation):
{
  "search_type": "search_movie" | "trending" | "discover" | "search_person",
  "query": "<title or person name if applicable>",
  "genre": "<genre name or null>",
  "year": <year as integer or null>,
  "sort_by": "popularity.desc" | "vote_average.desc" | "release_date.desc" | null,
  "min_rating": <float 0-10 or null>
}

Examples:
  "What is Inception about?" → {"search_type": "search_movie", "query": "Inception", ...nulls}
  "Top sci-fi movies of all time" → {"search_type": "discover", "query": null, "genre": "Science Fiction", "sort_by": "vote_average.desc", ...}
  "What's trending this week?" → {"search_type": "trending", ...nulls}
  "Movies by Christopher Nolan" → {"search_type": "search_person", "query": "Christopher Nolan", ...nulls}
"""

_ANSWER_SYSTEM = """You are a knowledgeable film expert assistant.

Answer the user's question using ONLY the TMDB data provided below.
Be specific — cite movie titles, ratings, cast members, release years.
Format your answer clearly. Use bullet points for lists.
If the data doesn't fully answer the question, say so.

TMDB Data:
{tmdb_data}
"""

# TMDB genre name → ID mapping (top genres)
_GENRE_MAP = {
    "action": 28, "adventure": 12, "animation": 16, "comedy": 35,
    "crime": 80, "documentary": 99, "drama": 18, "fantasy": 14,
    "history": 36, "horror": 27, "music": 10402, "mystery": 9648,
    "romance": 10749, "science fiction": 878, "sci-fi": 878,
    "thriller": 53, "war": 10752, "western": 37,
}


def _get_llm(max_tokens: int = 1024) -> ChatGroq:
    cfg = get_config()
    return ChatGroq(
        model=cfg.groq_model,
        temperature=0.1,
        api_key=cfg.groq_api_key,
        max_tokens=max_tokens,
        streaming=True,
    )


async def tmdb_agent_node(state: dict) -> dict:
    """LangGraph node: extract intent → call TMDB → generate answer."""
    question = state["question"]
    llm_extract = _get_llm(max_tokens=256)

    # Step 1: Extract intent
    intent_resp = await llm_extract.ainvoke([
        SystemMessage(content=_EXTRACT_SYSTEM),
        HumanMessage(content=question),
    ])

    try:
        intent = json.loads(intent_resp.content.strip())
    except Exception:
        intent = {"search_type": "search_movie", "query": question}

    search_type = intent.get("search_type", "search_movie")

    # Step 2: Call TMDB
    tmdb_data: dict = {}

    if search_type == "trending":
        tmdb_data = await tmdb_client.get_trending("movie", "week")

    elif search_type == "search_person":
        query = intent.get("query", question)
        people = await tmdb_client.search_person(query)
        # If we found a person, also fetch their details
        if people["results"]:
            person_id = people["results"][0]["id"]
            person_detail = await tmdb_client.get_person(person_id)
            tmdb_data = {"person": person_detail, "search": people}
        else:
            tmdb_data = people

    elif search_type == "discover":
        genre_name = (intent.get("genre") or "").lower()
        genre_id = _GENRE_MAP.get(genre_name)
        tmdb_data = await tmdb_client.discover_movies(
            genre_id=genre_id,
            year=intent.get("year"),
            sort_by=intent.get("sort_by") or "vote_average.desc",
            min_rating=intent.get("min_rating") or 7.0,
        )

    else:  # search_movie (default)
        query = intent.get("query") or question
        search_result = await tmdb_client.search_movies(query)
        tmdb_data = search_result

        # If we got a specific movie, fetch its full details
        if search_result["results"]:
            top = search_result["results"][0]
            if top.get("id"):
                detail = await tmdb_client.get_movie_details(
                    top["id"], top.get("media_type", "movie")
                )
                tmdb_data = {"search": search_result, "detail": detail}

    # Step 3: Generate grounded answer
    llm_answer = _get_llm(max_tokens=1024)
    tmdb_text = json.dumps(tmdb_data, indent=2, default=str)

    answer_resp = await llm_answer.ainvoke([
        SystemMessage(content=_ANSWER_SYSTEM.format(tmdb_data=tmdb_text[:6000])),
        HumanMessage(content=question),
    ])

    return {
        "tmdb_result": answer_resp.content,
        "_tmdb_raw": tmdb_data,  # carried in state for frontend events
    }
