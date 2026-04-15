"""
Music Agent — fetches artist/album data from MusicBrainz and generates
a grounded answer. Mirrors the structure of tmdb_agent.py.
"""
from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from src.config import get_config
from src.tools import musicbrainz_client

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
"""


def _get_llm(max_tokens: int = 1024) -> ChatGroq:
    cfg = get_config()
    return ChatGroq(
        model=cfg.groq_model,
        temperature=0.1,
        api_key=cfg.groq_api_key,
        max_tokens=max_tokens,
        streaming=True,
    )


async def music_agent_node(state: dict) -> dict:
    """LangGraph node: extract intent → call MusicBrainz → generate answer."""
    question = state["question"]
    llm_extract = _get_llm(max_tokens=128)

    # Step 1: Extract intent
    intent_resp = await llm_extract.ainvoke([
        SystemMessage(content=_EXTRACT_SYSTEM),
        HumanMessage(content=question),
    ])
    try:
        intent = json.loads(intent_resp.content.strip())
    except Exception:
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
    music_text = json.dumps(music_data, indent=2, default=str)

    answer_resp = await llm_answer.ainvoke([
        SystemMessage(content=_ANSWER_SYSTEM.format(music_data=music_text[:6000])),
        HumanMessage(content=question),
    ])

    return {
        "music_result": answer_resp.content,
        "_music_raw":   music_data,
    }
