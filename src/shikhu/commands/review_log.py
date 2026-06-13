"""shikhu log-review, log-study-question — persistence helpers called by the /study skill."""

import typer

from shikhu.commands.utils import console
from shikhu.store import (
    end_review,
    init_db,
    start_review,
)
from shikhu.store import (
    log_study_question as _log_study_question,
)


def log_review(
    file_path: str = typer.Argument(..., help="File being reviewed."),
    transcript: str = typer.Option(
        None, "--transcript", help="Path to the Claude Code session transcript (jsonl)."
    ),
    summary: str = typer.Option(
        None, "--summary", help="Subagent-written summary of the review session."
    ),
    notes: str = typer.Option(None, "--notes", help="Freeform notes."),
    review_id: int = typer.Option(
        None,
        "--review-id",
        help="Existing review id to close. If omitted, starts + ends a review in one call.",
    ),
    start_only: bool = typer.Option(
        False, "--start", help="Only start the review; print the new review_id and exit."
    ),
):
    """Record a study/review session. Use --start at the beginning to get a review_id; call again with --review-id to close."""
    init_db()

    if start_only:
        review_id = start_review(file_path, transcript_path=transcript)
        typer.echo(review_id)
        return

    if review_id is None:
        raise typer.BadParameter("Pass --start to open a review, or --review-id <id> to close one.")
    end_review(review_id, agent_summary=summary, notes=notes, transcript_path=transcript)

    console.print(
        f"[green]Logged review[/green] [bold]#{review_id}[/bold] for [bold]{file_path}[/bold]"
    )
    typer.echo(review_id)


def log_study_question(
    review_id: int = typer.Argument(..., help="Review id this question belongs to."),
    question_text: str = typer.Argument(..., help="The question the user asked."),
    conceptual: bool = typer.Option(
        True, "--conceptual/--not-conceptual", help="Whether the question is conceptual."
    ),
    answered: bool = typer.Option(
        None, "--answered/--unanswered", help="Whether the subagent answered it satisfactorily."
    ),
):
    """Record a question the user asked during a study session."""
    init_db()
    qid = _log_study_question(
        review_id,
        question_text,
        was_conceptual=conceptual,
        answered_satisfactorily=answered,
    )
    typer.echo(qid)
