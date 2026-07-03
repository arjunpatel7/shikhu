"""shikhu coverage — print knowledge-coverage report."""

import typer
from rich.panel import Panel
from rich.table import Table

from shikhu.commands.utils import (
    COVERED_THRESHOLD,
    DEFAULT_EXTENSIONS,
    console,
    get_trackable_files,
)
from shikhu.store import get_golden_counts, init_db
from shikhu.update_check import print_update_nudge, start_update_check


def coverage(
    extensions: str = typer.Option(DEFAULT_EXTENSIONS, help="File extensions to track."),
    queue: int = typer.Option(
        5, "--queue", help="How many least-covered files to surface as a study queue. 0 hides it."
    ),
):
    """Print a knowledge-coverage report."""
    start_update_check()
    init_db()
    trackable = get_trackable_files(extensions)
    if not trackable:
        console.print("No trackable files found.")
        return

    golden_map = get_golden_counts()

    fully_covered = []
    partial = []
    no_coverage = []

    for f in trackable:
        golden = golden_map.get(f, 0)
        if golden >= COVERED_THRESHOLD:
            fully_covered.append((f, golden))
        elif golden > 0:
            partial.append((f, golden))
        else:
            no_coverage.append(f)

    total = len(trackable)
    fully_pct = (len(fully_covered) / total * 100) if total else 0

    # Header
    console.print()
    console.print(
        Panel(
            f"[bold]{len(fully_covered)}[/bold]/{total} files fully covered  |  "
            f"[yellow]{len(partial)}[/yellow] in progress  |  "
            f"[dim]{len(no_coverage)}[/dim] not started",
            title="[bold]Knowledge Coverage[/bold]",
            subtitle=f"{fully_pct:.0f}% complete",
            border_style="blue",
        )
    )

    # Study queue: lowest-golden files first, alphabetical as tiebreaker.
    # Files already at threshold drop out — nothing to study there.
    if queue > 0:
        candidates = sorted(
            (
                (f, golden_map.get(f, 0))
                for f in trackable
                if golden_map.get(f, 0) < COVERED_THRESHOLD
            ),
            key=lambda fg: (fg[1], fg[0]),
        )[:queue]
        if candidates:
            body = "\n".join(
                f"  [bold]{i}.[/bold] {f}  [dim]({n}/{COVERED_THRESHOLD} golden)[/dim]"
                for i, (f, n) in enumerate(candidates, 1)
            )
            console.print()
            console.print(
                Panel(
                    body,
                    title="[bold]Study these next[/bold]",
                    subtitle="[dim]/shikhu-study <file> then `shikhu generate-from-study <file>`[/dim]",
                    border_style="magenta",
                )
            )

    # Detailed table if there's anything to show
    if fully_covered or partial:
        table = Table(
            show_header=True, header_style="bold", show_lines=False, pad_edge=False, box=None
        )
        table.add_column("File", style="bold")
        table.add_column("Golden", justify="center", width=8)
        table.add_column("Progress", width=20)

        for f, n in sorted(fully_covered):
            bar = _progress_str(n, COVERED_THRESHOLD)
            table.add_row(f, f"[green]{n}/{COVERED_THRESHOLD}[/green]", bar)

        for f, n in sorted(partial, key=lambda x: x[1], reverse=True):
            bar = _progress_str(n, COVERED_THRESHOLD)
            table.add_row(f, f"[yellow]{n}/{COVERED_THRESHOLD}[/yellow]", bar)

        console.print()
        console.print(table)

    # Unexplored files
    if no_coverage:
        console.print()
        console.print(f"[dim]  {len(no_coverage)} files with no coverage yet:[/dim]")
        for f in sorted(no_coverage):
            console.print(f"[dim]    {f}[/dim]")

    console.print()
    print_update_nudge()


def _progress_str(current: int, target: int) -> str:
    filled = min(current, target)
    empty = target - filled
    return "[green]" + "=" * filled + "[/green]" + "[dim]" + "-" * empty + "[/dim]"
