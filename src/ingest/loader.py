"""
Document loading, chunking, and ingestion into Milvus.

Supported file types: .txt, .md, .pdf
Add more by extending LOADER_MAP below.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
    UnstructuredMarkdownLoader,
)
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.tools.milvus_retriever import get_vectorstore

LOADER_MAP = {
    ".txt": TextLoader,
    ".md": UnstructuredMarkdownLoader,
    ".pdf": PyPDFLoader,
}

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100


def _iter_files(directory: str | Path) -> Iterator[Path]:
    """Yield all supported files under *directory* recursively."""
    root = Path(directory)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in LOADER_MAP:
            yield path


def load_documents(path: str | Path) -> list[Document]:
    """
    Load all supported documents from *path* (file or directory).
    Returns a flat list of LangChain Document objects.
    """
    path = Path(path)
    paths = [path] if path.is_file() else list(_iter_files(path))

    if not paths:
        raise ValueError(f"No supported files found at: {path}")

    docs: list[Document] = []
    for file_path in paths:
        loader_cls = LOADER_MAP.get(file_path.suffix.lower())
        if loader_cls is None:
            continue
        loader = loader_cls(str(file_path))
        loaded = loader.load()
        # Ensure the source metadata is always set
        for doc in loaded:
            doc.metadata.setdefault("source", str(file_path))
        docs.extend(loaded)

    return docs


def chunk_documents(
    docs: list[Document],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Document]:
    """Split documents into overlapping chunks for embedding."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_documents(docs)


def ingest(
    path: str | Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> int:
    """
    Load → chunk → embed → insert into Milvus.

    Returns the number of chunks inserted.
    """
    docs = load_documents(path)
    chunks = chunk_documents(docs, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    store = get_vectorstore()
    store.add_documents(chunks)

    return len(chunks)
