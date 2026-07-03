"""shikhu refresh — check staleness and regenerate questions + summaries."""

from concurrent.futures import ThreadPoolExecutor, as_completed

import typer
from dotenv import find_dotenv, load_dotenv
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from shikhu.commands.summarize import _summarize_one
from shikhu.commands.utils import (
    COVERED_THRESHOLD,
    DEFAULT_EXTENSIONS,
    console,
    ensure_api_key,
    get_trackable_files,
)
from shikhu.ingest import ingest_recent
from shikhu.staleness import mark_stale_questions
from shikhu.store import delete_summaries_not_in, init_db


def refresh(
    extensions: str = typer.Option(DEFAULT_EXTENSIONS, help="File extensions to track."),
    summary_workers: int = typer.Option(
        8, "--summary-workers", help="Parallelism for summary refresh."
    ),
):
    """Check staleness, regenerate questions + summaries, print summary."""
    load_dotenv(find_dotenv(usecwd=True))
    ensure_api_key()
    init_db()
    try:
        ingest_recent()
    except Exception:
        pass  # ingestion is non-critical

    console.print()

    stale_count = mark_stale_questions()
    if stale_count:
        console.print(f"  [yellow]![/yellow] Marked {stale_count} question(s) stale")
    else:
        console.print("  [green]>[/green] All questions up to date")

    # Summaries track .md too (matching `shikhu summarize`'s default), so the orphan
    # pruning below never deletes doc summaries that summarize created.
    summary_extensions = extensions if ".md" in extensions else f"{extensions},.md"

    # Drop summaries for files no longer tracked (deleted, renamed, or .quizignore'd)
    trackable_for_summaries = get_trackable_files(summary_extensions)
    orphans = delete_summaries_not_in(trackable_for_summaries)
    if orphans:
        console.print(f"  [yellow]![/yellow] Pruned {orphans} orphaned summary(ies)")

    # Refresh stale/missing summaries in parallel (with progress bar)
    summary_generated, summary_fresh, summary_errors = 0, 0, 0
    summary_error_details: list[tuple[str, str]] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("  [progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Summaries", total=len(trackable_for_summaries))
        with ThreadPoolExecutor(max_workers=summary_workers) as pool:
            futures = {pool.submit(_summarize_one, p, False): p for p in trackable_for_summaries}
            for fut in as_completed(futures):
                path, status, detail = fut.result()
                if status == "generated":
                    summary_generated += 1
                elif status == "fresh":
                    summary_fresh += 1
                elif status == "error":
                    summary_errors += 1
                    summary_error_details.append((path, detail or "unknown"))
                progress.update(task, advance=1, description=f"Summaries [dim]{path}[/dim]")

    if summary_generated:
        console.print(
            f"  [green]>[/green] Regenerated {summary_generated} summary(ies) ([dim]{summary_fresh} fresh[/dim])"
        )
    else:
        console.print("  [green]>[/green] All summaries up to date")
    if summary_errors:
        console.print(f"  [red]x[/red] {summary_errors} summary(ies) failed:")
        for p, d in summary_error_details:
            console.print(f"      [red]-[/red] {p} — [dim]{d}[/dim]")

    # Attempt to generate new questions for files that need them
    try:
        from shikhu.generator import (
            PROMPT_VERSION,
            _get_unasked_counts,
            _quiz_to_rows,
            generate_question_from_file,
        )
        from shikhu.store import insert_questions

        trackable = get_trackable_files(extensions)
        existing = _get_unasked_counts()
        to_generate = [
            (f, COVERED_THRESHOLD - existing.get(f, 0))
            for f in trackable
            if existing.get(f, 0) < COVERED_THRESHOLD
        ]

        if not to_generate:
            console.print("  [green]>[/green] All files have enough questions")
            console.print()
            return

        console.print(f"  [cyan]...[/cyan] Generating questions for {len(to_generate)} file(s)")

        def _gen_one(file_path: str, needed: int) -> tuple[str, int, str | None]:
            try:
                result = generate_question_from_file(file_path, num_questions=needed)
                if result is None:
                    return file_path, 0, "file missing"
                quiz_obj, _ = result
                ids = insert_questions(
                    file_path, _quiz_to_rows(quiz_obj), prompt_version=PROMPT_VERSION
                )
                return file_path, len(ids), None
            except Exception as e:
                return file_path, 0, f"{type(e).__name__}: {e}"

        total_saved = 0
        gen_errors: list[tuple[str, str]] = []
        with Progress(
            SpinnerColumn(),
            TextColumn("  [progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Questions", total=len(to_generate))
            with ThreadPoolExecutor(max_workers=summary_workers) as pool:
                futures = {pool.submit(_gen_one, fp, n): fp for fp, n in to_generate}
                for fut in as_completed(futures):
                    fp, count, err = fut.result()
                    if err:
                        gen_errors.append((fp, err))
                    else:
                        total_saved += count
                    progress.update(task, advance=1, description=f"Questions [dim]{fp}[/dim]")

        console.print()
        console.print(f"  [bold green]{total_saved} new question(s) generated.[/bold green]")
        if gen_errors:
            console.print(f"  [red]x[/red] {len(gen_errors)} file(s) failed:")
            for fp, err in gen_errors:
                console.print(f"      [red]-[/red] {fp} — [dim]{err}[/dim]")

    except ImportError as e:
        console.print(f"  [yellow]![/yellow] Generation unavailable: {e}")
    except Exception as e:
        console.print(f"  [red]x[/red] Error during generation: {e}")

    console.print()
