"""shikhu coverage — print knowledge-coverage report."""

import typer
from rich.panel import Panel
from rich.table import Table

from shikhu.commands.utils import (
    COVERED_THRESHOLD,
    DEFAULT_EXTENSIONS,
    changed_files,
    console,
    get_trackable_files,
)
from shikhu.staleness import mark_stale_questions
from shikhu.store import get_golden_counts, init_db
from shikhu.update_check import print_update_nudge, start_update_check


def coverage(
    extensions: str = typer.Option(DEFAULT_EXTENSIONS, help="File extensions to track."),
    queue: int = typer.Option(
        5, "--queue", help="How many least-covered files to surface as a study queue. 0 hides it."
    ),
    since: str = typer.Option(
        None, "--since", help="Only report files changed since this git ref (e.g. main)."
    ),
    check: bool = typer.Option(
        False, "--check", help="Exit 1 if any reported file has fewer than --min fresh goldens."
    ),
    min_golden: int = typer.Option(
        None,
        "--min",
        help=f"Golden questions a file needs to count as covered (default {COVERED_THRESHOLD}, or 1 with --check).",
    ),
):
    """Print a knowledge-coverage report."""
    start_update_check()
    init_db()
    # Local and free: a file edited since the last refresh shouldn't still read as covered.
    mark_stale_questions()
    target = min_golden if min_golden is not None else (1 if check else COVERED_THRESHOLD)

    if since:
        trackable = changed_files(since, extensions)
        if not trackable:
            console.print(f"No changed trackable files since [bold]{since}[/bold].")
            return
    else:
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
        if golden >= target:
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
            f"[bold]{len(fully_covered)}[/bold]/{total} {'changed ' if since else ''}files fully covered  |  "
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
            ((f, golden_map.get(f, 0)) for f in trackable if golden_map.get(f, 0) < target),
            key=lambda fg: (fg[1], fg[0]),
        )[:queue]
        if candidates:
            body = "\n".join(
                f"  [bold]{i}.[/bold] {f}  [dim]({n}/{target} golden)[/dim]"
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
            bar = _progress_str(n, target)
            table.add_row(f, f"[green]{n}/{target}[/green]", bar)

        for f, n in sorted(partial, key=lambda x: x[1], reverse=True):
            bar = _progress_str(n, target)
            table.add_row(f, f"[yellow]{n}/{target}[/yellow]", bar)

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

    if check:
        needs_review = sorted(f for f in trackable if golden_map.get(f, 0) < target)
        if needs_review:
            scope = f" --since {since}" if since else ""
            console.print(
                f"[red]x[/red] {len(needs_review)} file(s) below {target} fresh golden question(s). "
                f"Run [bold]shikhu quiz{scope}[/bold] (or [bold]shikhu refresh{scope}[/bold] if out of questions)."
            )
            raise typer.Exit(code=1)
        console.print(
            f"[green]>[/green] All files have at least {target} fresh golden question(s)."
        )


def _progress_str(current: int, target: int) -> str:
    filled = min(current, target)
    empty = target - filled
    return "[green]" + "=" * filled + "[/green]" + "[dim]" + "-" * empty + "[/dim]"
