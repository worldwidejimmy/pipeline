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
    existing_sources: set[str] = set()
    if args.skip_existing:
        print("Checking existing sources in Milvus…")
        try:
            rows = client.query(
                cfg.milvus_collection,
                filter='source like "ebert/%"',
                output_fields=["source"],
                limit=100_000,
            )
            existing_sources = {r["source"] for r in rows}
            print(f"  {len(existing_sources)} Ebert chunks already present")
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
        slug = review.get("url", "").split("/reviews/")[-1].rstrip("/") or review.get("title", "unknown")
        source = f"ebert/{slug}"

        if source in existing_sources:
            skipped += 1
            continue

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

    # Embed in batches
    print(f"\nEmbedding via OpenAI text-embedding-3-small…")
    dense_vecs = embedder.embed_documents(all_texts)

    # Insert in batches
    rows = [
        {"text": text, "dense_vector": vec, "source": source}
        for text, vec, source in zip(all_texts, dense_vecs, all_sources)
    ]

    inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        client.insert(cfg.milvus_collection, batch)
        inserted += len(batch)
        print(f"  Inserted {inserted}/{len(rows)}", end="\r")

    print(f"  Inserted {inserted}/{len(rows)} chunks          ")

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
