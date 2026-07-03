"""shikhu generate-from-study — produce quiz questions seeded by the user's prior /shikhu-study questions for a file."""

import typer
from dotenv import find_dotenv, load_dotenv

from shikhu.commands.utils import console, ensure_api_key
from shikhu.ingest import ingest_recent
from shikhu.store import init_db, insert_questions


def generate_from_study(
    file_path: str = typer.Argument(
        ..., help="File to generate questions for, seeded by prior /shikhu-study questions."
    ),
    n: int = typer.Option(3, "--n", help="Number of questions to generate."),
):
    """Generate quiz questions for FILE_PATH seeded by conceptual questions the user asked during prior /shikhu-study sessions on this file.

    Manual trigger only. Run `/shikhu-study <file>` first to capture seed questions; this command then turns those questions into quiz questions that test the same concepts."""
    load_dotenv(find_dotenv(usecwd=True))
    ensure_api_key()
    init_db()
    try:
        ingest_recent()
    except Exception:
        pass  # ingestion is non-critical

    from shikhu.generator import (
        PROMPT_VERSION,
        _quiz_to_rows,
        generate_questions_from_study_seeds,
    )

    result = generate_questions_from_study_seeds(file_path, num_questions=n)
    if result is None:
        console.print(
            f"[yellow]No conceptual /shikhu-study questions found for {file_path}, or file is missing.[/yellow]"
        )
        console.print(
            "[dim]Run /shikhu-study on the file first to capture some questions, then try again.[/dim]"
        )
        raise typer.Exit(code=1)

    quiz, stats, seed_ids = result
    rows = _quiz_to_rows(quiz)
    ids = insert_questions(
        file_path,
        rows,
        prompt_version=PROMPT_VERSION,
        seed_query_ids=seed_ids,
        seed_query_source="review_questions",
    )

    console.print(
        f"[green]>[/green] Generated [bold]{len(ids)}[/bold] question(s) for [bold]{file_path}[/bold]"
    )
    console.print(
        f"  [dim]{stats['completion_tokens']} completion tokens, {stats['elapsed']:.1f}s · seeded by {len(seed_ids)} /shikhu-study question(s)[/dim]"
    )
