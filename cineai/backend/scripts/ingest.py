"""
Ingest movie knowledge documents into Milvus for RAG retrieval.

Usage:
  python scripts/ingest.py                    # ingest ./docs/
  python scripts/ingest.py path/to/docs/      # ingest a specific directory
  python scripts/ingest.py path/to/file.md    # ingest a single file
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running from the backend/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_community.document_loaders import TextLoader, UnstructuredMarkdownLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from src.config import get_config
from src.tools.milvus_retriever import get_vectorstore

LOADER_MAP = {
    ".txt": TextLoader,
    ".md":  UnstructuredMarkdownLoader,
}

DEFAULT_CHUNK_SIZE    = 800
DEFAULT_CHUNK_OVERLAP = 100


def load_files(path: Path) -> list[Document]:
    paths = [path] if path.is_file() else list(path.rglob("*"))
    docs: list[Document] = []
    for p in paths:
        if not p.is_file() or p.suffix.lower() not in LOADER_MAP:
            continue
        loader = LOADER_MAP[p.suffix.lower()](str(p))
        loaded = loader.load()
        for doc in loaded:
            doc.metadata.setdefault("source", str(p))
        docs.extend(loaded)
        print(f"  Loaded: {p} ({len(loaded)} doc(s))")
    return docs


def chunk(docs: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_documents(docs)


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs")

    if not target.exists():
        print(f"ERROR: Path not found: {target}")
        sys.exit(1)

    cfg = get_config()
    print(f"\nCineAI Ingest")
    print(f"  Source : {target.resolve()}")
    print(f"  Milvus : {cfg.milvus_uri}")
    print(f"  Collection: {cfg.milvus_collection}\n")

    print("Loading documents...")
    docs = load_files(target)
    if not docs:
        print("No supported files found (.txt, .md)")
        sys.exit(1)

    print(f"\nChunking {len(docs)} document(s)...")
    chunks = chunk(docs)
    print(f"  → {len(chunks)} chunks (size={DEFAULT_CHUNK_SIZE}, overlap={DEFAULT_CHUNK_OVERLAP})")

    print("\nEmbedding and inserting into Milvus...")
    store = get_vectorstore()
    store.add_documents(chunks)

    print(f"\n✓ Ingested {len(chunks)} chunks into '{cfg.milvus_collection}'")
    print("  RAG is ready. Run a query to test retrieval.\n")


if __name__ == "__main__":
    main()
