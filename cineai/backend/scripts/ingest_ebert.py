"""
Ingest scraped Roger Ebert reviews into the existing Milvus collection.

Reads data/ebert_reviews.jsonl produced by scrape_ebert.py.
Each review is formatted as a rich text chunk and embedded with OpenAI.

Usage (run from backend/):
  python scripts/ingest_ebert.py                 # ingest all
  python scripts/ingest_ebert.py --limit 100     # test with first 100
  python scripts/ingest_ebert.py --skip-existing # skip reviews already in Milvus
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from pymilvus import MilvusClient

from src.config import get_config
from scripts.ingest import ensure_collection, TEXT_MAX_LEN, BATCH_SIZE

IN_FILE = Path(__file__).parent.parent / "data" / "ebert_reviews.jsonl"

CHUNK_SIZE    = 900   # slightly larger for reviews — full paragraphs read better
CHUNK_OVERLAP = 120
EMBED_BATCH   = 256   # chunks embedded+inserted per batch (keeps peak memory low)


def norm_slug(source_or_slug: str) -> str:
    """Canonical review key — strips the ebert/ and amp/ prefixes so that the
    AMP mirror (ebert/amp/foo) and the canonical page (ebert/foo) collapse to the
    same key. This is what keeps re-ingests (incl. the nightly cron) from
    reintroducing the duplicate-mirror problem."""
    s = source_or_slug.removeprefix("ebert/").removeprefix("amp/")
    return s.rstrip("/")


def format_review(review: dict) -> str:
    """Format a review dict into a rich text block for embedding."""
    title  = review.get("title", "Unknown")
    year   = review.get("year") or "Unknown"
    stars  = review.get("stars")
    text   = review.get("text", "")

    star_line = f"Rating: {stars}/4 stars\n" if stars else ""
    header = (
        f"Roger Ebert Review: {title} ({year})\n"
        f"{star_line}"
        f"Source: rogerebert.com\n\n"
    )
    return header + text


def load_reviews(path: Path, limit: int) -> list[dict]:
    reviews = []
    with path.open() as f:
        for line in f:
            try:
                reviews.append(json.loads(line))
            except Exception:
                pass
            if limit and len(reviews) >= limit:
                break
    return reviews


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Ebert reviews into Milvus")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max reviews to ingest (0 = all)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Query Milvus first and skip already-ingested reviews")
    args = parser.parse_args()

    if not IN_FILE.exists():
        print(f"ERROR: {IN_FILE} not found.")
        print("Run: python scripts/scrape_ebert.py --limit 50")
        sys.exit(1)

    cfg    = get_config()
    client = MilvusClient(uri=cfg.milvus_uri)

    print(f"\nEbert Review Ingest")
    print(f"  Source    : {IN_FILE}")
    print(f"  Milvus    : {cfg.milvus_uri}")
    print(f"  Collection: {cfg.milvus_collection}")
    print()

    # Ensure collection exists with hybrid schema
    ensure_collection(client, cfg, reset=False)

    # Optionally find already-ingested sources
    # seen_slugs holds canonical review keys already present (or seen this run).
    # Comparing on the normalized slug dedupes amp mirrors against canonical pages.
    seen_slugs: set[str] = set()
    if args.skip_existing:
        print("Checking existing sources in Milvus…")
        try:
            # Milvus caps a single query window at 16,384 rows, so paginate with
            # an iterator — the corpus is far larger than that.
            it = client.query_iterator(
                cfg.milvus_collection,
                filter='source like "ebert/%"',
                output_fields=["source"],
                batch_size=16_000,
            )
            chunks_seen = 0
            while True:
                batch = it.next()
                if not batch:
                    it.close()
                    break
                chunks_seen += len(batch)
                seen_slugs.update(norm_slug(r["source"]) for r in batch)
            print(f"  {chunks_seen} Ebert chunks already present "
                  f"({len(seen_slugs)} distinct reviews)")
        except Exception as e:
            print(f"  Could not query existing: {e}")

    # Load reviews
    print(f"Loading reviews from {IN_FILE}…")
    reviews = load_reviews(IN_FILE, args.limit)
    print(f"  {len(reviews)} reviews loaded")

    # Build chunks
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    embedder = OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=cfg.openai_api_key,
    )

    all_texts:   list[str] = []
    all_sources: list[str] = []

    skipped = 0
    for review in reviews:
        raw_slug = review.get("url", "").split("/reviews/")[-1].rstrip("/")
        slug = norm_slug(raw_slug) or review.get("title", "unknown")
        source = f"ebert/{slug}"   # always canonical form, never ebert/amp/…

        if slug in seen_slugs:   # already in Milvus, or an amp/canonical twin this run
            skipped += 1
            continue
        seen_slugs.add(slug)

        full_text = format_review(review)
        chunks    = splitter.split_text(full_text)

        for chunk in chunks:
            all_texts.append(chunk[:TEXT_MAX_LEN])
            all_sources.append(source)

    print(f"  {skipped} reviews skipped (already in Milvus)")
    print(f"  {len(all_texts)} chunks to embed and insert")

    if not all_texts:
        print("Nothing to insert.")
        return

    # Embed + insert in bounded batches so peak memory stays flat (important on
    # a small shared host — embedding ~150k vectors at once would OOM the box).
    # Each batch is embedded then immediately inserted and freed; a failed batch
    # is logged and skipped rather than losing the whole run.
    print(f"\nEmbedding + inserting via OpenAI text-embedding-3-small (batches of {EMBED_BATCH})…")
    total = len(all_texts)
    inserted = 0
    failed = 0
    for i in range(0, total, EMBED_BATCH):
        texts   = all_texts[i : i + EMBED_BATCH]
        sources = all_sources[i : i + EMBED_BATCH]
        try:
            vecs = embedder.embed_documents(texts)
            rows = [
                {"text": t, "dense_vector": v, "source": s}
                for t, v, s in zip(texts, vecs, sources)
            ]
            client.insert(cfg.milvus_collection, rows)
            inserted += len(rows)
        except Exception as e:
            failed += len(texts)
            print(f"\n  ⚠️  batch at offset {i} failed ({str(e)[:120]}) — skipping")
        print(f"  Inserted {inserted}/{total}" + (f" ({failed} failed)" if failed else ""), end="\r")

    client.flush(cfg.milvus_collection)
    print(f"  Inserted {inserted}/{total} chunks" + (f" — {failed} failed" if failed else "") + "          ")

    # Final stats
    stats = client.get_collection_stats(cfg.milvus_collection)
    total = stats.get("row_count", "?")
    print(f"\n✓ Done — {inserted} chunks added, {total} total in Milvus")
    print(f"  Reviews now answer questions like:")
    print(f"    'What did Ebert think of The Godfather?'")
    print(f"    'Best 4-star Ebert reviews'")
    print(f"    'Did Ebert like Blade Runner?'\n")


if __name__ == "__main__":
    main()
