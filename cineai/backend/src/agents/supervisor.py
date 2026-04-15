"""
Supervisor agent — classifies the user's question and decides which
downstream agents to invoke. Uses conversation history for follow-up
questions so routing stays coherent across turns.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from src.config import get_config

# Exposed for GET /api/rules — keep in sync with _SYSTEM "Rules" section below.
SUPERVISOR_LLM_RULE_BULLETS: tuple[str, ...] = (
    "Questions about a specific movie OR TV show/series title → tmdb",
    "Questions about a specific actor, director, or person in film or TV → tmdb",
    "Questions about plot, cast, ratings, runtime, release year, seasons, episodes → tmdb",
    "Questions about trending, popular, or recently released movies or TV shows → tmdb",
    "Questions about film/TV theory, director style, cinematography, historical context → rag",
    "Questions about box office this week, upcoming releases, recent news → search",
    "Questions about a currently airing TV show + recent news → tmdb+search",
    'Questions combining "what is X about" + "how does it compare historically" → tmdb+rag',
    "Questions about music artists, bands, singers, musicians → music",
    "Questions about albums, discography, songs, music genres → music",
    "Questions about lyrics, who wrote a song, songwriting credits → music",
    'Questions containing words like "lyrics", "songwriter", "wrote the song", "composed the music" → music',
    "Questions about film composers (Hans Zimmer, Jonny Greenwood, Ennio Morricone) → tmdb+music",
    "Questions about musicians who also appear in films or have concert documentaries → tmdb+music",
    "Questions about new or recent music releases → music+search",
    "General recommendations with context → all",
    "Follow-up questions that reference previous answers → use same or broader routing",
    "When in doubt between tmdb and rag, prefer tmdb",
    "When in doubt whether music is needed, add it (tmdb+music is fine to be generous)",
)

_RULES_BLOCK = "Rules:\n" + "\n".join(f"- {line}" for line in SUPERVISOR_LLM_RULE_BULLETS)

_SYSTEM = f"""You are a routing agent for a movie, TV, and music intelligence system.

Classify the user's question into exactly one of these routing decisions:
  tmdb        – needs real-time movie/TV database data (ratings, cast, plot, trending)
  rag         – needs deep analysis from stored knowledge base (film theory, history, reviews)
  search      – needs current news or very recent events (this week's box office, just released)
  music       – needs music artist/album/discography data
  tmdb+rag    – needs both real-time movie/TV data AND deep analysis/context
  tmdb+search – needs real-time movie/TV data AND current web news
  tmdb+music  – needs both movie/TV data AND music data (e.g. film composer, band with concert films)
  music+search – needs music data AND current web news (e.g. new album just released)
  rag+search  – needs stored knowledge AND current web news
  all         – needs all sources

{_RULES_BLOCK}

{{history_block}}
Reply with ONLY the routing decision word(s). No explanation. No punctuation.
Examples: "tmdb" or "tmdb+rag" or "all"
"""

_HISTORY_BLOCK = """Conversation so far (most recent first):
{turns}

The user's new question may be a follow-up to the above.
"""


def _get_llm() -> ChatGroq:
    cfg = get_config()
    return ChatGroq(
        model=cfg.groq_model,
        temperature=0,
        api_key=cfg.groq_api_key,
        max_tokens=16,
    )


# Keyword patterns that deterministically force a routing decision,
# regardless of what the LLM says. Checked before the LLM call.
_FORCE_MUSIC_KEYWORDS = [
    "lyrics", "lyric", "wrote the song", "wrote the lyrics", "songwriter",
    "songwriting", "who wrote", "composed the song", "music video",
    "discography", "album", "albums", "band members", "debut album",
    "studio album", "music genre", "music genres", "tracklist", "track list",
]
_FORCE_TMDB_KEYWORDS = [
    "box office", "rotten tomatoes", "imdb rating", "streaming on",
    "what episode", "season finale", "tv schedule",
]


def _keyword_route(question: str) -> str | None:
    """Return a forced routing if strong keywords are present, else None."""
    q = question.lower()
    if any(kw in q for kw in _FORCE_MUSIC_KEYWORDS):
        return "music"
    if any(kw in q for kw in _FORCE_TMDB_KEYWORDS):
        return "tmdb"
    return None


async def supervisor_route_node(state: dict) -> dict:
    """LangGraph node: classify question → routing decision."""
    question = state["question"]
    history  = state.get("history") or []

    # Fast deterministic path — skip the LLM for obvious signals
    forced = _keyword_route(question)
    if forced:
        return {"routing": forced}

    # Build history context for the prompt
    if history:
        recent = history[-3:]  # last 3 turns
        turns_text = "\n".join(
            f"Q: {h['q']}\nA: {h['a'][:150]}…" for h in reversed(recent)
        )
        history_block = _HISTORY_BLOCK.format(turns=turns_text)
    else:
        history_block = ""

    system = _SYSTEM.format(history_block=history_block)
    llm = _get_llm()

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=question),
    ])

    raw = response.content.strip().lower()
    valid = {"tmdb", "rag", "search", "music", "tmdb+rag", "tmdb+search", "tmdb+music",
             "music+search", "rag+search", "all"}
    routing = raw if raw in valid else "tmdb+rag"

    return {"routing": routing}
