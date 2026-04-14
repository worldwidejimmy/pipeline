"""
TMDB Agent — fetches real-time movie/TV data and generates a grounded answer.

Strategy:
  1. Use an LLM to extract intent + entities from the question
  2. Call the appropriate TMDB endpoints (in parallel when needed)
  3. Use an LLM to generate a grounded, citation-rich answer from the results
"""
from __future__ import annotations

import asyncio
import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from src.config import get_config
from src.tools import tmdb_client

_EXTRACT_SYSTEM = """Extract structured search intent from the user's movie/TV question.

Respond with JSON only (no markdown, no explanation):
{
  "search_type": "search_movie" | "trending" | "discover" | "search_person" | "movie_and_person",
  "query": "<movie or show title, or null>",
  "person": "<actor/director name when comparing against their filmography, or null>",
  "genre": "<genre name or null>",
  "year": <year as integer or null>,
  "sort_by": "popularity.desc" | "vote_average.desc" | "release_date.desc" | null,
  "min_rating": <float 0-10 or null>
}

Use "movie_and_person" when the question asks about a specific film AND wants to
compare it against an actor's or director's broader filmography.

Examples:
  "What is Inception about?" → {"search_type": "search_movie", "query": "Inception", "person": null, ...nulls}
  "Top sci-fi movies of all time" → {"search_type": "discover", "query": null, "person": null, "genre": "Science Fiction", "sort_by": "vote_average.desc", ...}
  "What's trending this week?" → {"search_type": "trending", "query": null, "person": null, ...nulls}
  "Movies by Christopher Nolan" → {"search_type": "search_person", "query": "Christopher Nolan", "person": null, ...nulls}
  "Is Project Hail Mary Ryan Gosling's best film?" → {"search_type": "movie_and_person", "query": "Project Hail Mary", "person": "Ryan Gosling", ...nulls}
  "How does Barbie compare to Margot Robbie's other work?" → {"search_type": "movie_and_person", "query": "Barbie", "person": "Margot Robbie", ...nulls}
"""

_ANSWER_SYSTEM = """You are a knowledgeable film expert assistant.

Answer the user's question using ONLY the TMDB data provided below.
Be specific — cite movie titles, ratings (vote_average), cast members, release years.
Format your answer clearly. Use bullet points or a ranked list for comparisons.
When filmography data is available, rank the person's movies by rating and place
the film in question within that ranked list.

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

    elif search_type == "movie_and_person":
        # Fetch movie details + person filmography in parallel
        movie_query  = intent.get("query") or question
        person_query = intent.get("person") or ""

        movie_search_coro  = tmdb_client.search_movies(movie_query)
        person_search_coro = tmdb_client.search_person(person_query) if person_query else None

        if person_search_coro:
            movie_search, person_search = await asyncio.gather(
                movie_search_coro, person_search_coro
            )
        else:
            movie_search = await movie_search_coro
            person_search = {"results": []}

        movie_detail: dict = {}
        if movie_search["results"]:
            top = movie_search["results"][0]
            if top.get("id"):
                movie_detail = await tmdb_client.get_movie_details(
                    top["id"], top.get("media_type", "movie")
                )

        person_detail: dict = {}
        if person_search["results"]:
            person_id = person_search["results"][0]["id"]
            person_detail = await tmdb_client.get_person(person_id)

        # Slim the movie payload so person filmography isn't truncated
        movie_slim = {
            k: v for k, v in (movie_detail or {}).items()
            if k not in ("similar", "spoken_languages", "production_companies", "latency_ms")
        } or movie_search

        # Sort person's top movies by rating descending for easy comparison
        if person_detail.get("top_movies"):
            person_detail["top_movies"] = sorted(
                person_detail["top_movies"],
                key=lambda m: m.get("rating") or 0,
                reverse=True,
            )

        tmdb_data = {
            "movie": movie_slim,
            "person_filmography": person_detail,
        }

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
    # movie_and_person queries carry more data — give the LLM more room
    max_out = 1500 if search_type == "movie_and_person" else 1024
    llm_answer = _get_llm(max_tokens=max_out)
    tmdb_text = json.dumps(tmdb_data, indent=2, default=str)
    char_limit = 9000 if search_type == "movie_and_person" else 6000

    answer_resp = await llm_answer.ainvoke([
        SystemMessage(content=_ANSWER_SYSTEM.format(tmdb_data=tmdb_text[:char_limit])),
        HumanMessage(content=question),
    ])

    return {
        "tmdb_result": answer_resp.content,
        "_tmdb_raw": tmdb_data,  # carried in state for frontend events
    }
