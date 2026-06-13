"""shikhu study-context — print the cached summary + prior reviews for a file.

Called by the /study skill so the skill doesn't need inline python one-liners.
"""

import typer

from shikhu.commands.utils import console
from shikhu.ingest import ingest_recent
from shikhu.store import get_reviews_for_file, get_summary, init_db


def study_context(
    file_path: str = typer.Argument(..., help="File to load study context for."),
):
    """Print the cached Mercury summary and prior review records for a file."""
    init_db()
    try:
        ingest_recent()
    except Exception:
        pass  # ingestion is non-critical

    summary = get_summary(file_path)
    console.print(f"[bold]=== Summary for {file_path} ===[/bold]")
    if summary is None:
        console.print("[yellow]No cached summary.[/yellow]")
        console.print(f"Run: [cyan]uv run shikhu summarize --file {file_path}[/cyan]")
    else:
        console.print(
            f"[dim](prompt {summary['prompt_version']}, generated {summary['generated_at']})[/dim]"
        )
        console.print(summary["summary_text"])

    reviews = get_reviews_for_file(file_path)
    console.print()
    console.print(f"[bold]=== Prior reviews ({len(reviews)}) ===[/bold]")
    if not reviews:
        console.print("[dim](none)[/dim]")
    else:
        for r in reviews:
            when = r["started_at"]
            agent_summary = r["agent_summary"] or "(no summary)"
            console.print(f"[cyan]{when}[/cyan] — {agent_summary}")
