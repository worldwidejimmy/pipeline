"""
Scrape Roger Ebert reviews via the Wayback Machine (web.archive.org).

The live rogerebert.com blocks automated access, but archived snapshots are
freely available through the Internet Archive's CDX and playback APIs.

Strategy:
  1. Query the CDX API to get all archived review URLs + timestamps
  2. Deduplicate and normalize URLs
  3. Fetch each archived snapshot, parse title / stars / review text
  4. Save to data/ebert_reviews.jsonl (one JSON object per line, resumable)

Usage (run from backend/):
  python scripts/scrape_ebert.py                  # all reviews
  python scripts/scrape_ebert.py --limit 100      # test with 100
  python scripts/scrape_ebert.py --reset          # start fresh
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

OUT_FILE  = Path(__file__).parent.parent / "data" / "ebert_reviews.jsonl"
URLS_FILE = Path(__file__).parent.parent / "data" / "ebert_urls.json"
DELAY     = 1.2    # seconds between archive fetches — be polite
TIMEOUT   = 25
HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; academic-research-bot/1.0)"}

CDX_URL  = "http://web.archive.org/cdx/search/cdx"
CDX_REVIEW_RE = re.compile(
    r"https?://(?:www\.)?rogerebert\.com/reviews/[a-z0-9].+", re.I
)


# ── CDX: discover all archived review URLs ───────────────────────────────────

def _cdx_one_year(client: httpx.Client, year: int) -> dict[str, str]:
    """Fetch CDX rows for one calendar year → {norm_url: timestamp}."""
    try:
        r = client.get(CDX_URL, params={
            "url":    "rogerebert.com/reviews/*",
            "output": "json",
            "fl":     "original,timestamp",
            "filter": "statuscode:200",
            "from":   f"{year}0101",
            "to":     f"{year}1231",
            "limit":  "30000",
        }, timeout=45)
        r.raise_for_status()
        rows = r.json()
    except Exception as exc:
        print(f"  ⚠ CDX {year}: {exc}")
        return {}

    result: dict[str, str] = {}
    for original, timestamp in rows[1:]:
        if not CDX_REVIEW_RE.match(original) or "?" in original:
            continue
        norm = re.sub(r"^https?://(?:www\.)?", "", original.lower()).rstrip("/")
        if norm not in result:
            result[norm] = timestamp
    return result


def fetch_cdx_urls() -> dict[str, str]:
    """
    Return {normalized_url: best_timestamp} — loads from cache if available,
    otherwise queries CDX API year-by-year and saves the result to disk.
    """
    if URLS_FILE.exists():
        print(f"Loading cached URL list from {URLS_FILE}…")
        with URLS_FILE.open() as f:
            url_map = json.load(f)
        print(f"  {len(url_map)} URLs loaded from cache")
        return url_map

    print("Querying Wayback Machine CDX API (one year at a time)…")
    url_map: dict[str, str] = {}

    with httpx.Client() as client:
        for year in range(2013, 2026):
            year_map = _cdx_one_year(client, year)
            new = sum(1 for k in year_map if k not in url_map)
            url_map.update(year_map)
            print(f"  {year}: +{new} new (total {len(url_map)})")
            time.sleep(1.0)

    print(f"CDX done — {len(url_map)} unique review URLs")
    URLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with URLS_FILE.open("w") as f:
        json.dump(url_map, f)
    print(f"URL list saved to {URLS_FILE}")
    return url_map


# ── Page parsing ─────────────────────────────────────────────────────────────

def _count_stars(soup: BeautifulSoup) -> str | None:
    """
    Count star icons in the FIRST .star-rating block (= current review).
    icon-star-full = 1, icon-star-half = 0.5
    """
    first_block = soup.select_one(".star-rating")
    if not first_block:
        return None

    full  = len(first_block.find_all(class_="icon-star-full"))
    half  = len(first_block.find_all(class_="icon-star-half"))
    total = full + half * 0.5

    if total == 0:
        return None
    return str(int(total) if total == int(total) else total)


def _parse_year(soup: BeautifulSoup, url: str) -> str | None:
    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            for key in ("datePublished", "dateCreated"):
                val = data.get(key, "")
                if val and len(val) >= 4:
                    return val[:4]
        except Exception:
            pass
    # URL slug: -YYYY at end
    m = re.search(r"-(\d{4})(?:/|$)", url)
    if m:
        return m.group(1)
    return None


def parse_review_page(html: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")

    # Title — prefer the h1 inside the review block
    title_el = (
        soup.select_one(".review-title, .content h1, h1.title")
        or soup.find("h1")
    )
    title = title_el.get_text(strip=True) if title_el else ""
    if not title:
        return None

    # Body — the archive uses article.entry for the review text
    entry = soup.select_one("article.entry, div.review-content, div.entry-content")
    if not entry:
        return None

    for noise in entry.find_all(["nav", "aside", "script", "style", "figure"]):
        noise.decompose()

    paras = [p.get_text(" ", strip=True) for p in entry.find_all("p")
             if len(p.get_text(strip=True)) > 40]
    text  = "\n\n".join(paras)

    if len(text) < 150:
        return None

    return {
        "url":   url,
        "title": title,
        "year":  _parse_year(soup, url),
        "stars": _count_stars(soup),
        "text":  text,
    }


def scrape_one(client: httpx.Client, norm_url: str, timestamp: str) -> dict | None:
    archive_url = f"https://web.archive.org/web/{timestamp}/http://www.{norm_url}"
    try:
        r = client.get(archive_url, headers=HEADERS, timeout=TIMEOUT,
                       follow_redirects=True)
        if r.status_code in (404, 410):
            return None
        r.raise_for_status()
    except Exception as exc:
        print(f"    HTTP error: {exc}")
        return None

    return parse_review_page(r.text, norm_url)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="Max new reviews to scrape (0 = all)")
    parser.add_argument("--reset", action="store_true",
                        help="Delete existing JSONL and restart")
    args = parser.parse_args()

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    if args.reset and OUT_FILE.exists():
        OUT_FILE.unlink()
        print(f"Reset: deleted {OUT_FILE}")

    # Load already-scraped URLs
    done: set[str] = set()
    if OUT_FILE.exists():
        with OUT_FILE.open() as f:
            for line in f:
                try:
                    done.add(json.loads(line)["url"])
                except Exception:
                    pass
        print(f"Resuming: {len(done)} reviews already saved")

    url_map = fetch_cdx_urls()
    with httpx.Client() as client:
        todo    = [(u, ts) for u, ts in url_map.items() if u not in done]

        if args.limit:
            todo = todo[:args.limit]

        print(f"\nScraping {len(todo)} reviews → {OUT_FILE}\n")

        saved = ok = skipped = 0
        with OUT_FILE.open("a") as out:
            for i, (norm_url, timestamp) in enumerate(todo, 1):
                review = scrape_one(client, norm_url, timestamp)

                if review:
                    out.write(json.dumps(review, ensure_ascii=False) + "\n")
                    out.flush()
                    ok += 1
                    saved += 1
                    stars = f"{review['stars']}★" if review.get("stars") else "?★"
                    print(f"  [{i}/{len(todo)}] ✓ {review['title']!r} ({review['year']}) {stars}")
                else:
                    skipped += 1
                    print(f"  [{i}/{len(todo)}] ⚠ skipped: {norm_url}")

                time.sleep(DELAY)

    total = len(done) + saved
    print(f"\n✓ Done — {ok} saved, {skipped} skipped → {OUT_FILE}")
    print(f"  Total in file: {total} reviews")
    print(f"\nNext: python scripts/ingest_ebert.py")


if __name__ == "__main__":
    main()
