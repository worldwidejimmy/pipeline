"""MusicBrainz API client — free, open music database (no API key required)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

# MusicBrainz requires a descriptive User-Agent
_HEADERS = {
    "User-Agent": "CineAI/1.0 (music-search; contact@cineai.app)",
    "Accept": "application/json",
}
_BASE = "https://musicbrainz.org/ws/2"
_TIMEOUT = 10

# MusicBrainz allows ~1 request/second per client and answers 503 when exceeded.
# An artist lookup is two back-to-back requests (search, then details), so without
# spacing the second one intermittently 503'd and the WHOLE answer became an error
# (caught by the T4 "cars" canary running right after "police-albums").
_MIN_INTERVAL_S = 1.1
_RETRY_DELAYS_S = (1.5, 3.0)
_gate = asyncio.Lock()
_last_call = 0.0


async def _get(path: str, params: dict) -> dict:
    """GET a MusicBrainz endpoint: process-wide 1 req/s spacing + retry on 503/429."""
    global _last_call
    for attempt in range(len(_RETRY_DELAYS_S) + 1):
        async with _gate:
            wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
                    resp = await client.get(f"{_BASE}/{path}", params=params)
            finally:
                _last_call = time.monotonic()
        if resp.status_code in (429, 503) and attempt < len(_RETRY_DELAYS_S):
            await asyncio.sleep(_RETRY_DELAYS_S[attempt])
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("unreachable")


def _fmt_release_group(rg: dict) -> dict:
    """Normalise a raw MusicBrainz release-group to a slim dict."""
    return {
        "id":         rg.get("id"),
        "title":      rg.get("title"),
        "type":       rg.get("primary-type"),        # Album, Single, EP, etc.
        # secondary types mark non-studio albums: Compilation, Live, Soundtrack,
        # Remix, DJ-mix, Demo, Mixtape. Empty list => a studio album.
        "secondary_types": rg.get("secondary-types", []) or [],
        "year":       (rg.get("first-release-date") or "")[:4],
        "rating":     rg.get("rating", {}).get("value"),
    }


async def search_artist(name: str) -> dict[str, Any]:
    """Search MusicBrainz for an artist by name. Returns top 3 matches."""
    t0 = time.time()
    data = await _get("artist", {"query": name, "limit": 3, "fmt": "json"})

    artists = []
    for a in data.get("artists", []):
        artists.append({
            "id":           a.get("id"),
            "name":         a.get("name"),
            "type":         a.get("type"),           # Person, Group, etc.
            "country":      a.get("country"),
            "life_span":    a.get("life-span", {}),  # begin/end dates
            "disambiguation": a.get("disambiguation"),
            "score":        a.get("score"),          # MusicBrainz match score
        })

    return {
        "results": artists,
        "total":   data.get("count", 0),
        "latency_ms": int((time.time() - t0) * 1000),
    }


async def get_artist_details(mbid: str) -> dict[str, Any]:
    """Fetch full artist details including release groups (albums/EPs/singles)."""
    t0 = time.time()
    data = await _get(f"artist/{mbid}", {"inc": "release-groups+ratings+tags+aliases", "fmt": "json"})

    # Separate and sort release groups by type then year
    release_groups = [_fmt_release_group(rg) for rg in data.get("release-groups", [])]
    albums  = sorted([r for r in release_groups if r["type"] == "Album"],
                     key=lambda r: r["year"] or "")
    singles = sorted([r for r in release_groups if r["type"] == "Single"],
                     key=lambda r: r["year"] or "")[:10]  # cap singles
    eps     = sorted([r for r in release_groups if r["type"] == "EP"],
                     key=lambda r: r["year"] or "")

    tags = sorted(
        data.get("tags", []), key=lambda t: t.get("count", 0), reverse=True
    )[:8]

    return {
        "id":           data.get("id"),
        "name":         data.get("name"),
        "type":         data.get("type"),
        "country":      data.get("country"),
        "begin":        data.get("life-span", {}).get("begin"),
        "end":          data.get("life-span", {}).get("end"),
        "ended":        data.get("life-span", {}).get("ended", False),
        "disambiguation": data.get("disambiguation"),
        "genres":       [t["name"] for t in tags],
        "albums":       albums,
        "eps":          eps,
        "singles":      singles,
        "latency_ms":   int((time.time() - t0) * 1000),
    }


async def search_release(query: str) -> dict[str, Any]:
    """Search for a specific album or release by name."""
    t0 = time.time()
    data = await _get("release-group", {"query": query, "limit": 5, "fmt": "json"})

    results = [_fmt_release_group(rg) for rg in data.get("release-groups", [])]
    return {
        "results":    results,
        "total":      data.get("release-group-count", 0),
        "latency_ms": int((time.time() - t0) * 1000),
    }
