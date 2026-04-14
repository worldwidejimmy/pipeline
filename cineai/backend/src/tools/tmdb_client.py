"""TMDB API client — async wrapper around The Movie Database REST API."""
from __future__ import annotations

import time
from typing import Any

import httpx

from src.config import get_config


def _headers() -> dict[str, str]:
    cfg = get_config()
    return {
        "Authorization": f"Bearer {cfg.tmdb_bearer_token}",
        "accept": "application/json",
    }


def _base() -> str:
    return get_config().tmdb_base_url


def _poster_url(path: str | None) -> str | None:
    if not path:
        return None
    return f"{get_config().tmdb_image_base}{path}"


def _fmt_movie(m: dict) -> dict:
    """Normalise a raw TMDB movie object to a slim dict."""
    return {
        "id": m.get("id"),
        "title": m.get("title") or m.get("name"),
        "year": (m.get("release_date") or m.get("first_air_date") or "")[:4],
        "rating": m.get("vote_average"),
        "vote_count": m.get("vote_count"),
        "overview": m.get("overview"),
        "poster": _poster_url(m.get("poster_path")),
        "genres": [g["name"] for g in m.get("genres", [])],
        "media_type": m.get("media_type", "movie"),
    }


async def search_movies(query: str, page: int = 1) -> dict[str, Any]:
    """Search movies and TV shows by title."""
    t0 = time.time()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_base()}/search/multi",
            headers=_headers(),
            params={"query": query, "page": page, "include_adult": False},
        )
        resp.raise_for_status()
        data = resp.json()

    results = [
        _fmt_movie(r)
        for r in data.get("results", [])[:8]
        if r.get("media_type") in ("movie", "tv")
    ]
    return {
        "results": results,
        "total_results": data.get("total_results", 0),
        "latency_ms": int((time.time() - t0) * 1000),
        "endpoint": "/search/multi",
    }


async def get_movie_details(movie_id: int, media_type: str = "movie") -> dict[str, Any]:
    """Fetch full details + credits for a movie or TV show."""
    t0 = time.time()
    async with httpx.AsyncClient(timeout=10) as client:
        detail_resp = await client.get(
            f"{_base()}/{media_type}/{movie_id}",
            headers=_headers(),
            params={"append_to_response": "credits,similar,videos"},
        )
        detail_resp.raise_for_status()
        data = detail_resp.json()

    cast = [
        {"name": c["name"], "character": c["character"], "order": c["order"]}
        for c in data.get("credits", {}).get("cast", [])[:10]
    ]
    crew = [
        {"name": c["name"], "job": c["job"]}
        for c in data.get("credits", {}).get("crew", [])
        if c["job"] in ("Director", "Producer", "Screenplay", "Writer", "Executive Producer")
    ][:8]
    similar = [_fmt_movie(s) for s in data.get("similar", {}).get("results", [])[:5]]

    return {
        **_fmt_movie(data),
        "runtime": data.get("runtime"),
        "status": data.get("status"),
        "tagline": data.get("tagline"),
        "budget": data.get("budget"),
        "revenue": data.get("revenue"),
        "spoken_languages": [l["english_name"] for l in data.get("spoken_languages", [])],
        "production_companies": [c["name"] for c in data.get("production_companies", [])[:4]],
        "cast": cast,
        "crew": crew,
        "similar": similar,
        "latency_ms": int((time.time() - t0) * 1000),
        "endpoint": f"/{media_type}/{movie_id}",
    }


async def get_trending(media_type: str = "movie", time_window: str = "week") -> dict[str, Any]:
    """Fetch trending movies or TV shows."""
    t0 = time.time()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_base()}/trending/{media_type}/{time_window}",
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

    results = [_fmt_movie(r) for r in data.get("results", [])[:10]]
    return {
        "results": results,
        "latency_ms": int((time.time() - t0) * 1000),
        "endpoint": f"/trending/{media_type}/{time_window}",
    }


async def discover_movies(
    genre_id: int | None = None,
    year: int | None = None,
    sort_by: str = "popularity.desc",
    min_rating: float = 0.0,
    page: int = 1,
) -> dict[str, Any]:
    """Discover movies with filters."""
    t0 = time.time()
    params: dict[str, Any] = {
        "sort_by": sort_by,
        "vote_average.gte": min_rating,
        "page": page,
        "vote_count.gte": 100,
    }
    if genre_id:
        params["with_genres"] = genre_id
    if year:
        params["primary_release_year"] = year

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_base()}/discover/movie",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    results = [_fmt_movie(r) for r in data.get("results", [])[:10]]
    return {
        "results": results,
        "total_results": data.get("total_results", 0),
        "latency_ms": int((time.time() - t0) * 1000),
        "endpoint": "/discover/movie",
    }


async def get_person(person_id: int) -> dict[str, Any]:
    """Fetch a person's biography and filmography."""
    t0 = time.time()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_base()}/person/{person_id}",
            headers=_headers(),
            params={"append_to_response": "movie_credits,tv_credits"},
        )
        resp.raise_for_status()
        data = resp.json()

    top_movies = sorted(
        data.get("movie_credits", {}).get("cast", []),
        key=lambda x: x.get("popularity", 0),
        reverse=True,
    )[:10]

    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "biography": data.get("biography"),
        "birthday": data.get("birthday"),
        "known_for": data.get("known_for_department"),
        "top_movies": [_fmt_movie(m) for m in top_movies],
        "latency_ms": int((time.time() - t0) * 1000),
        "endpoint": f"/person/{person_id}",
    }


async def search_person(name: str) -> dict[str, Any]:
    """Search for a person (actor, director, etc.)."""
    t0 = time.time()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_base()}/search/person",
            headers=_headers(),
            params={"query": name},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])[:3]
    return {
        "results": [
            {
                "id": r["id"],
                "name": r["name"],
                "known_for": r.get("known_for_department"),
                "known_for_movies": [
                    _fmt_movie(m) for m in r.get("known_for", [])[:3]
                ],
            }
            for r in results
        ],
        "latency_ms": int((time.time() - t0) * 1000),
        "endpoint": "/search/person",
    }
