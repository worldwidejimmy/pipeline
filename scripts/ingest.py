#!/usr/bin/env python3
"""
CLI: ingest documents into Milvus.

Usage
─────
  python scripts/ingest.py docs/
  python scripts/ingest.py docs/my_notes.md
  python scripts/ingest.py docs/ --chunk-size 600 --chunk-overlap 80
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

# Ensure src/ is on the path when running from the project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingest.loader import ingest

app = typer.Typer(help="Ingest documents into the Milvus knowledge base.")
console = Console()


@app.command()
def main(
    path: str = typer.Argument(..., help="File or directory to ingest"),
    chunk_size: int = typer.Option(800, help="Characters per chunk"),
    chunk_overlap: int = typer.Option(100, help="Overlap between chunks"),
) -> None:
    target = Path(path)
    if not target.exists():
        console.print(f"[red]Error:[/red] path does not exist: {target}")
        raise typer.Exit(1)

    console.print(f"\n[bold]Ingesting:[/bold] {target.resolve()}")
    console.print(f"  chunk_size={chunk_size}, chunk_overlap={chunk_overlap}\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Loading, chunking, embedding…", total=None)
        t0 = time.monotonic()
        try:
            n = ingest(target, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        except Exception as exc:
            console.print(f"[red]Ingestion failed:[/red] {exc}")
            raise typer.Exit(1) from exc
        elapsed = time.monotonic() - t0
        progress.update(task, description="Done")

    console.print(
        f"[green]✓[/green] Inserted [bold]{n}[/bold] chunks "
        f"in {elapsed:.1f}s."
    )


if __name__ == "__main__":
    app()
