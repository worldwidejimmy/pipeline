#!/usr/bin/env python3
"""
Generate grounded artist docs for the RAG knowledge base from MusicBrainz.

100% factual: every album title + year comes straight from MusicBrainz
release-groups (primary-type=Album, secondary-types empty → studio albums only,
so compilations / live / soundtracks / remix albums are excluded). No LLM prose,
no hallucination — the honest source for a RAG demo.

Writes docs/music/artists/<slug>.md, one per artist. Skips artists that already
have a doc (won't clobber the hand-written ones). Rate-limited to respect
MusicBrainz's ~1 req/sec policy. Re-run ingest.py afterwards to index them.

Usage:
  python3 scripts/gen_music_docs.py                 # default canonical list
  python3 scripts/gen_music_docs.py --only "Bjork,Prince"
  python3 scripts/gen_music_docs.py --force         # overwrite existing docs
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ → import src.*
from src.tools import musicbrainz_client as mb

OUT_DIR = Path(__file__).resolve().parents[1] / "docs" / "music" / "artists"
RATE_SLEEP = 1.1  # seconds between MusicBrainz calls (policy: ~1 req/sec)

# Non-studio secondary types to exclude from the "Studio Albums" list.
_NON_STUDIO = {"Compilation", "Live", "Soundtrack", "Remix", "DJ-mix",
               "Demo", "Mixtape/Street", "Interview", "Audiobook", "Spokenword"}

# ~100 canonical artists across eras & genres (rock, pop, hip-hop, R&B, soul,
# jazz, electronic, country, metal, punk, folk). The 4 hand-written docs
# (The Police, Radiohead, The Beatles, Simon & Garfunkel) are intentionally
# omitted — the skip-existing guard protects them anyway.
ARTISTS = [
    # classic rock / 60s-70s
    "Pink Floyd", "Led Zeppelin", "The Rolling Stones", "The Who", "David Bowie",
    "Fleetwood Mac", "Queen", "The Doors", "Jimi Hendrix", "The Velvet Underground",
    "Bob Dylan", "Neil Young", "Joni Mitchell", "Bruce Springsteen", "The Beach Boys",
    "Creedence Clearwater Revival", "The Kinks", "Van Morrison", "Eric Clapton",
    # punk / new wave / post-punk / 80s
    "The Clash", "Ramones", "Talking Heads", "Joy Division", "New Order",
    "The Cure", "Depeche Mode", "Blondie", "Elvis Costello", "Kate Bush",
    "Prince", "Michael Jackson", "Madonna", "U2", "Peter Gabriel",
    # alt / indie / 90s-00s
    "Nirvana", "Pearl Jam", "Pixies", "R.E.M.", "Sonic Youth",
    "Oasis", "Blur", "Pulp", "Beck", "PJ Harvey",
    "The Smashing Pumpkins", "Nine Inch Nails", "Red Hot Chili Peppers",
    "The White Stripes", "The Strokes", "Arcade Fire", "Wilco",
    "The National", "Vampire Weekend", "LCD Soundsystem",
    # metal / hard rock
    "Black Sabbath", "Metallica", "Iron Maiden", "Tool", "Rage Against the Machine",
    # hip-hop / rap
    "Public Enemy", "N.W.A", "A Tribe Called Quest", "Wu-Tang Clan", "Nas",
    "The Notorious B.I.G.", "2Pac", "OutKast", "Jay-Z", "Eminem",
    "Kanye West", "Kendrick Lamar", "Missy Elliott", "Run-D.M.C.",
    # R&B / soul / funk
    "Stevie Wonder", "Marvin Gaye", "Aretha Franklin", "James Brown",
    "Curtis Mayfield", "Sly and the Family Stone", "D'Angelo", "Erykah Badu",
    "Beyonce", "Frank Ocean", "The Weeknd",
    # pop
    "Fleetwood Mac", "ABBA", "Elton John", "Amy Winehouse", "Adele",
    "Taylor Swift", "Lady Gaga", "Bjork",
    # jazz
    "Miles Davis", "John Coltrane", "Charles Mingus", "Thelonious Monk",
    "Herbie Hancock", "Nina Simone",
    # electronic
    "Kraftwerk", "Aphex Twin", "Daft Punk", "The Chemical Brothers", "Massive Attack",
    "Boards of Canada",
    # country / folk / americana
    "Johnny Cash", "Willie Nelson", "Dolly Parton", "Gillian Welch", "Sufjan Stevens",
    # reggae
    "Bob Marley & The Wailers",
]


def slug(name: str) -> str:
    s = name.lower()
    s = s.replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def _pick_match(results: list[dict], name: str) -> dict | None:
    """Best MusicBrainz match: highest score, preferring an exact name match."""
    if not results:
        return None
    exact = [r for r in results if (r.get("name") or "").lower() == name.lower()]
    pool = exact or results
    return max(pool, key=lambda r: int(r.get("score") or 0))


def render(details: dict, query_name: str) -> str | None:
    albums = [
        a for a in details.get("albums", [])
        if a.get("year") and not (set(a.get("secondary_types", [])) & _NON_STUDIO)
    ]
    if not albums:
        return None  # nothing verifiable to write

    name = details.get("name") or query_name
    kind = "band" if details.get("type") == "Group" else "artist"
    span = ""
    if details.get("begin"):
        span = details["begin"][:4]
        span += f"–{details['end'][:4]}" if details.get("end") else "–present"
    genres = ", ".join(details.get("genres", [])[:5])

    lines = [f"# {name}", ""]
    meta = []
    if kind:
        meta.append(f"**Type:** {details.get('type', '—')}")
    if details.get("country"):
        meta.append(f"**Country:** {details['country']}")
    if span:
        meta.append(f"**Active:** {span}")
    if meta:
        lines += [" · ".join(meta), ""]
    if genres:
        lines += [f"**Genres:** {genres}", ""]

    lines += ["## Studio Albums", ""]
    for a in albums:
        lines.append(f"- {a['title']} ({a['year']})")
    lines += [
        "",
        f"_{len(albums)} studio albums. Source: MusicBrainz "
        f"(artist {details.get('id')}), generated {datetime.now(timezone.utc):%Y-%m-%d}. "
        f"Studio albums only — compilations, live, soundtrack & remix releases excluded._",
        "",
    ]
    return "\n".join(lines)


async def _retry(fn, *args, tries: int = 4):
    """Call an MB coroutine with backoff — MusicBrainz 503s when rate-limited."""
    for attempt in range(tries):
        try:
            return await fn(*args)
        except Exception as e:
            if attempt == tries - 1:
                raise
            await asyncio.sleep(2 * (attempt + 1))  # 2s, 4s, 6s
    return None


async def build_one(name: str, force: bool) -> str:
    out = OUT_DIR / f"{slug(name)}.md"
    if out.exists() and not force:
        return f"skip (exists): {name}"
    try:
        search = await _retry(mb.search_artist, name)
        await asyncio.sleep(RATE_SLEEP)
        match = _pick_match(search.get("results", []), name)
        if not match:
            return f"NO MATCH: {name}"
        details = await _retry(mb.get_artist_details, match["id"])
        await asyncio.sleep(RATE_SLEEP)
        doc = render(details, name)
        if not doc:
            return f"NO ALBUMS: {name}"
        out.write_text(doc)
        n = doc.count("\n- ")
        return f"✓ {name} → {out.name} ({n} albums)"
    except Exception as e:
        return f"ERROR {name}: {str(e)[:80]}"


async def main() -> None:
    force = "--force" in sys.argv
    only = None
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            only = [x.strip() for x in sys.argv[i + 1].split(",") if x.strip()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # de-dup while preserving order (the list has an intentional dupe or two)
    names = only or list(dict.fromkeys(ARTISTS))
    print(f"Generating {len(names)} artist docs → {OUT_DIR} (rate ~{RATE_SLEEP}s/call)")
    t0 = time.time()
    ok = 0
    for i, name in enumerate(names, 1):
        res = await build_one(name, force)
        if res.startswith("✓"):
            ok += 1
        print(f"[{i:>3}/{len(names)}] {res}", flush=True)
    print(f"\nDone: {ok} written, {len(names) - ok} skipped/failed, "
          f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
