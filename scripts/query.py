#!/usr/bin/env python3
"""
CLI: ask a question through the full multi-agent research pipeline.

Usage
─────
  python scripts/query.py "What is LangGraph?"
  python scripts/query.py "Latest news on Milvus 2.6?" --verbose
  python scripts/query.py  # interactive mode (reads from stdin)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.graph.pipeline import run

app = typer.Typer(help="Query the multi-agent research pipeline.")
console = Console()


def _run_with_spinner(query: str) -> tuple[str, float]:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        progress.add_task("Thinking…", total=None)
        t0 = time.monotonic()
        answer = run(query)
        elapsed = time.monotonic() - t0
    return answer, elapsed


@app.command()
def main(
    query: str = typer.Argument(
        default="",
        help="Question to ask the pipeline. Omit for interactive mode.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show timing info."),
) -> None:
    if not query:
        console.print("\n[bold]Research Pipeline[/bold] — interactive mode")
        console.print("Type your question and press Enter. Ctrl+C to exit.\n")
        try:
            while True:
                query = console.input("[bold cyan]>[/bold cyan] ").strip()
                if not query:
                    continue
                _ask(query, verbose)
                console.print()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            return

    _ask(query, verbose)


def _ask(query: str, verbose: bool) -> None:
    console.print()
    answer, elapsed = _run_with_spinner(query)

    console.print(
        Panel(
            Markdown(answer),
            title="[bold green]Answer[/bold green]",
            border_style="green",
        )
    )

    if verbose:
        console.print(f"[dim]Completed in {elapsed:.1f}s[/dim]\n")


if __name__ == "__main__":
    app()
