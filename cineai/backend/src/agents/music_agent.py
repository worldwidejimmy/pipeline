"""
Music Agent — fetches artist/album data from MusicBrainz and generates
a grounded answer. Mirrors the structure of tmdb_agent.py.
"""
from __future__ import annotations

import json

from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from src.llm import extract, get_chat, static_part, system_blocks, text_of

from src.config import get_config
from src.tools import musicbrainz_client
from src.guardrails import UNTRUSTED_RULE, clean_obj, wrap_untrusted

_EXTRACT_SYSTEM = """Extract structured music search intent from the user's question.

Respond with JSON only (no markdown, no explanation):
{
  "search_type": "artist" | "album",
  "artist": "<artist or band name, or null>",
  "album": "<album or song title, or null>"
}

For questions about songwriting, lyrics, or who wrote a song — identify the artist
associated with the song and use search_type "artist" to look up their details.

Examples:
  "tell me about Radiohead"                    → {"search_type": "artist", "artist": "Radiohead", "album": null}
  "what albums did Radiohead make"             → {"search_type": "artist", "artist": "Radiohead", "album": null}
  "tell me about OK Computer"                  → {"search_type": "album", "artist": "Radiohead", "album": "OK Computer"}
  "who made Dark Side of the Moon"             → {"search_type": "album", "artist": null, "album": "Dark Side of the Moon"}
  "who wrote the lyrics to message in a bottle"→ {"search_type": "artist", "artist": "The Police", "album": null}
  "who wrote roxanne"                          → {"search_type": "artist", "artist": "The Police", "album": null}
  "who wrote bohemian rhapsody"                → {"search_type": "artist", "artist": "Queen", "album": null}
  "Jonny Greenwood discography"                → {"search_type": "artist", "artist": "Jonny Greenwood", "album": null}
  "Hans Zimmer film scores"                    → {"search_type": "artist", "artist": "Hans Zimmer", "album": null}
"""

_ANSWER_SYSTEM = """You are a knowledgeable music expert assistant.

Answer the user's question using the MusicBrainz data AND the RAG knowledge base context below.
Be specific — cite album titles, release years, genres, and band member names from the data.
For songwriting questions (who wrote lyrics, who composed a song), identify the primary
songwriter from the artist/band information available.
Format your answer clearly using markdown.

CRITICAL — never hallucinate:
- Use ONLY facts present in the data below.
- Do NOT invent chart positions, awards, sales figures, or any details not in the data.
- If the data is empty or irrelevant, say "I couldn't find music data for that artist or album."

LINKS — always include a MusicBrainz link using the id (mbid) fields:
- Artist link format: [MusicBrainz](https://musicbrainz.org/artist/{{id}})
- Album link format:  [MusicBrainz](https://musicbrainz.org/release-group/{{id}})
- Place the artist link next to the artist name in the header.
- Place album links inline next to each album title where the id is available.

MusicBrainz Data:
{music_data}
""" + UNTRUSTED_RULE
_ANSWER_SYSTEM_STATIC = static_part(_ANSWER_SYSTEM, "MusicBrainz Data:", UNTRUSTED_RULE)   # cached prefix (llm.system_blocks)


class MusicIntent(BaseModel):
    search_type: Literal["artist", "album"]
    artist: Optional[str] = None
    album: Optional[str] = Field(None, description="album or song title")


def _get_llm(max_tokens: int = 1024):
    return get_chat(temperature=0.1, max_tokens=max_tokens, streaming=True)


async def music_agent_node(state: dict) -> dict:
    """LangGraph node: extract intent → call MusicBrainz → generate answer."""
    question = state["question"]

    # Step 1: Extract intent: schema-constrained (see llm.extract).
    intent = await extract(MusicIntent, _EXTRACT_SYSTEM, question, max_tokens=128)
    if intent is None:
        intent = {"search_type": "artist", "artist": question, "album": None}

    search_type = intent.get("search_type", "artist")
    music_data: dict = {}

    # Step 2: Call MusicBrainz
    if search_type == "album":
        album_query = intent.get("album") or question
        artist_name = intent.get("artist")
        query = f"{album_query} {artist_name}".strip() if artist_name else album_query
        music_data = await musicbrainz_client.search_release(query)

    else:  # artist (default)
        artist_name = intent.get("artist") or question
        search = await musicbrainz_client.search_artist(artist_name)

        if not search["results"]:
            return {
                "music_result": f"I couldn't find '{artist_name}' in the music database. "
                                f"Check the spelling or try a different artist name.",
                "_music_raw": search,
            }

        top = search["results"][0]
        if top.get("id"):
            detail = await musicbrainz_client.get_artist_details(top["id"])
            music_data = {"search": search, "detail": detail}
        else:
            music_data = search

    # Step 3: Generate grounded answer
    llm_answer = _get_llm(max_tokens=1024)
    # Compact JSON: indent=2 made a typical artist ~7.4k chars, so the [:6000] cut
    # dropped the tail of the release list mid-entry (found by the T4 cars canary).
    music_text = json.dumps(clean_obj(music_data), separators=(",", ":"), default=str)

    answer_resp = await llm_answer.ainvoke([
        system_blocks(_ANSWER_SYSTEM_STATIC, "MusicBrainz Data:\n" + wrap_untrusted("musicbrainz", music_text[:6000])),
        HumanMessage(content=question),
    ])

    return {
        "music_result": text_of(answer_resp.content),
        "_music_raw":   music_data,
    }
