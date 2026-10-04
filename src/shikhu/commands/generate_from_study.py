"""shikhu generate-from-study — produce quiz questions seeded by the user's prior /shikhu-study questions for a file."""

import typer
from dotenv import find_dotenv, load_dotenv

from shikhu.commands.utils import console, ensure_api_key
from shikhu.ingest import ingest_recent
from shikhu.staleness import compute_file_hash, mark_stale_questions, pin_commit
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

    # Stale older questions against the previous baseline before the new questions move it.
    mark_stale_questions()
    content_hash = compute_file_hash(file_path)
    from shikhu.inquiry import ALIGN_THRESHOLD, match_existing
    from shikhu.store import (
        get_conceptual_study_questions_for_file,
        get_requeueable_questions,
        requeue_question,
    )

    seeds = get_conceptual_study_questions_for_file(file_path, unconsumed=True)
    if not seeds and get_conceptual_study_questions_for_file(file_path):
        console.print(
            f"[dim]Every question you asked about {file_path} already has a quiz question. "
            f"Try `shikhu quiz --file {file_path}`.[/dim]"
        )
        return

    # A re-asked question that matches an unanswered quiz question is re-queued, not duplicated.
    open_questions = {
        q["id"]: f"{q['question_text']} (answer: {q['expected_answer']})"
        for q in get_requeueable_questions(file_path)
    }
    fresh, requeued = [], 0
    for seed in seeds:
        best, p = None, 0.0
        if open_questions:
            try:
                probs = match_existing(seed["question_text"], open_questions)
                best = max(probs, key=probs.get) if probs else None
                p = probs.get(best, 0.0)
            except Exception:
                pass  # the match is an optimization; never block generation on it
        if best is not None and p >= ALIGN_THRESHOLD:
            requeue_question(best, [seed["id"]])
            open_questions.pop(best)  # one re-ask per question
            requeued += 1
        else:
            fresh.append(seed)
    if requeued:
        console.print(
            f"[green]>[/green] {requeued} question(s) you asked again already have a quiz question; "
            "moved to the front of your next quiz."
        )
    if seeds and not fresh:
        return

    result = generate_questions_from_study_seeds(
        file_path, num_questions=n, seeds=fresh if seeds else None
    )
    if result is None:
        console.print(
            f"[yellow]No conceptual /shikhu-study questions found for {file_path}, or file is missing.[/yellow]"
        )
        console.print(
            "[dim]Run /shikhu-study on the file first to capture some questions, then try again.[/dim]"
        )
        raise typer.Exit(code=1)

    quiz, stats, seed_ids, extra_paths = result
    extra_hashes = {p: compute_file_hash(p) for p in extra_paths}
    pins = {pin_commit(p, h) for p, h in {file_path: content_hash, **extra_hashes}.items()}
    # one commit pin only if EVERY file still matches it; otherwise the pin would be false
    commit_sha = pins.pop() if len(pins) == 1 and None not in pins else None
    rows = _quiz_to_rows(quiz)
    ids = insert_questions(
        file_path,
        rows,
        prompt_version=PROMPT_VERSION,
        seed_query_ids=seed_ids,
        seed_query_source="review_questions",
        model=stats.get("model"),
        content_hash=content_hash,
        commit_sha=commit_sha,
        extra_files=extra_hashes,
    )

    console.print(
        f"[green]>[/green] Generated [bold]{len(ids)}[/bold] question(s) for "
        f"[bold]{' + '.join([file_path, *extra_paths])}[/bold]"
    )
    console.print(
        f"  [dim]{stats['completion_tokens']} completion tokens, {stats['elapsed']:.1f}s · seeded by {len(seed_ids)} /shikhu-study question(s)[/dim]"
    )
