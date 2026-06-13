"""shikhu summarize — generate cached Mercury summaries of tracked files."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import typer
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from shikhu.commands.utils import SUMMARY_EXTENSIONS, console, ensure_api_key, get_trackable_files
from shikhu.generator import PROMPT_VERSION, generate_summary
from shikhu.staleness import compute_file_hash
from shikhu.store import get_summary, init_db, upsert_summary


def _summarize_one(file_path: str, force: bool) -> tuple[str, str, str | None]:
    """Generate (if needed) and store a summary for one file. Returns (file_path, status, detail).

    Statuses: "generated", "fresh", "missing" (file gone from disk), "error" (API/other failure).
    `detail` holds the exception string on "error", else None.
    """
    current_hash = compute_file_hash(file_path)
    if current_hash is None:
        return file_path, "missing", None

    if not force:
        existing = get_summary(file_path)
        if (
            existing
            and existing["content_hash"] == current_hash
            and existing["prompt_version"] == PROMPT_VERSION
        ):
            return file_path, "fresh", None

    try:
        result = generate_summary(file_path)
    except Exception as e:
        return file_path, "error", f"{type(e).__name__}: {e}"
    if result is None:
        return file_path, "missing", None

    summary_text, _stats = result
    upsert_summary(file_path, current_hash, summary_text, prompt_version=PROMPT_VERSION)
    return file_path, "generated", None


def summarize(
    file: str = typer.Option(
        None, "--file", help="Summarize a single file (regenerates even if fresh)."
    ),
    workers: int = typer.Option(8, "--workers", help="Parallelism for Mercury calls."),
    extensions: str = typer.Option(
        SUMMARY_EXTENSIONS,
        help="Comma-separated extensions to summarize.",
    ),
):
    """Generate cached file summaries via Mercury, in parallel."""
    ensure_api_key()
    init_db()

    if file:
        if not Path(file).is_file():
            basename = Path(file).name
            matches = [f for f in get_trackable_files(extensions) if Path(f).name == basename]
            console.print(f"[red]File not found:[/red] {file}")
            if len(matches) == 1:
                console.print(f"Did you mean: [cyan]{matches[0]}[/cyan]?")
            elif len(matches) > 1:
                console.print("Did you mean one of:")
                for m in matches:
                    console.print(f"  [cyan]{m}[/cyan]")
            raise typer.Exit(code=1)
        targets = [file]
        force = True
    else:
        targets = get_trackable_files(extensions)
        force = False

    if not targets:
        console.print("[dim]No trackable files found.[/dim]")
        return

    generated, fresh, missing, errors = 0, 0, 0, 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Summarizing", total=len(targets))
        error_details: list[tuple[str, str]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_summarize_one, path, force): path for path in targets}
            for fut in as_completed(futures):
                path, status, detail = fut.result()
                if status == "generated":
                    generated += 1
                elif status == "fresh":
                    fresh += 1
                elif status == "error":
                    errors += 1
                    error_details.append((path, detail or "unknown"))
                else:
                    missing += 1
                progress.update(task, advance=1, description=f"Summarizing [dim]{path}[/dim]")

    for path, detail in error_details:
        console.print(f"  [red]x[/red] {path} — [dim]{detail}[/dim]")

    console.print()
    console.print(
        f"[bold green]{generated}[/bold green] generated, "
        f"[dim]{fresh} already fresh[/dim], "
        f"[yellow]{missing} missing[/yellow], "
        f"[red]{errors} error(s)[/red]"
    )
