"""shikhu generate-from-study — quiz questions seeded by the questions the user asked about a file."""

import typer
from dotenv import find_dotenv, load_dotenv

from shikhu.commands.utils import console, ensure_api_key
from shikhu.staleness import compute_file_hash, mark_stale_questions, pin_commit
from shikhu.store import init_db, insert_questions

# Seed sources, in the order they are generated from: questions asked live through /shikhu-study
# or /shikhu-inquiry, then past questions `shikhu map` found in old transcripts. Each source is
# its own batch, so a question's seeds always come from one table.
LIVE, PAST = "review_questions", "past_prompts"
SOURCE_LABEL = {LIVE: "question(s) you asked", PAST: "past question(s) from `shikhu map`"}


def _seeds(file_path: str, unconsumed: bool) -> dict[str, list[dict]]:
    from shikhu.inquiry import past_question_bar
    from shikhu.store import get_conceptual_study_questions_for_file, get_past_question_seeds

    return {
        LIVE: get_conceptual_study_questions_for_file(file_path, unconsumed=unconsumed),
        PAST: get_past_question_seeds(file_path, unconsumed=unconsumed, **past_question_bar()),
    }


def generate_from_study(
    file_path: str = typer.Argument(
        ..., help="File to generate questions for, seeded by questions you asked about it."
    ),
    n: int = typer.Option(3, "--n", help="Number of questions to generate per seed source."),
):
    """Generate quiz questions for FILE_PATH seeded by the questions you asked about it.

    Seeds come from /shikhu-study and /shikhu-inquiry, and from past questions `shikhu map`
    found in your transcripts. Each seed is used once; a question you asked again that matches
    an unanswered quiz question moves that question up instead of writing a near-duplicate."""
    load_dotenv(find_dotenv(usecwd=True))
    ensure_api_key()
    init_db()

    # Stale older questions against the previous baseline before the new questions move it.
    mark_stale_questions()
    content_hash = compute_file_hash(file_path)
    from shikhu.store import get_requeueable_questions

    seeds = _seeds(file_path, unconsumed=True)
    if not any(seeds.values()):
        if any(_seeds(file_path, unconsumed=False).values()):
            console.print(
                f"[dim]Every question you asked about {file_path} already has a quiz question. "
                f"Try `shikhu quiz --file {file_path}`.[/dim]"
            )
            return
        console.print(f"[yellow]No questions you asked about {file_path} yet.[/yellow]")
        console.print(
            "[dim]Ask about it with /shikhu-inquiry or walk it with /shikhu-study, or run "
            "`shikhu map` to find the ones you asked in past sessions.[/dim]"
        )
        raise typer.Exit(code=1)

    # A re-asked question that matches an unanswered quiz question is re-queued, not duplicated.
    open_questions = {
        q["id"]: f"{q['question_text']} (answer: {q['expected_answer']})"
        for q in get_requeueable_questions(file_path)
    }
    for source, batch in seeds.items():
        if batch:
            _generate(file_path, source, batch, n, content_hash, open_questions)


def _generate(
    file_path: str,
    source: str,
    seeds: list[dict],
    n: int,
    content_hash: str | None,
    open_questions: dict[int, str],
) -> None:
    """Re-queue the seeds that match an open question, then generate from the rest."""
    from shikhu.generator import PROMPT_VERSION, _quiz_to_rows, generate_questions_from_study_seeds
    from shikhu.inquiry import ALIGN_THRESHOLD, match_existing
    from shikhu.store import mark_past_prompts_used, requeue_question

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
            requeue_question(best, [seed["id"]], source=source, asked_at=seed.get("asked_at"))
            if source == PAST:
                mark_past_prompts_used([seed["id"]], best)
            open_questions.pop(best)  # one re-ask per question
            requeued += 1
        else:
            fresh.append(seed)
    if requeued:
        where = (
            "moved to the front of your next quiz"
            if source == LIVE
            # A past question re-queues as of when it was asked, so it may not lead the quiz.
            else "these count as asked again, as of when you asked them"
        )
        console.print(
            f"[green]>[/green] {requeued} {SOURCE_LABEL[source]} already have a quiz question; "
            f"{where}."
        )
    if not fresh:
        return

    try:
        result = generate_questions_from_study_seeds(
            file_path, num_questions=n, seeds=fresh, past=source == PAST
        )
    except RuntimeError as e:  # API errors, and an answer cut off twice
        console.print(f"[yellow]Generation failed: {e}. Nothing was saved; try again.[/yellow]")
        raise typer.Exit(code=1) from e
    if result is None:
        console.print(f"[yellow]{file_path} is missing; nothing generated.[/yellow]")
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
        seed_query_source=source,
        model=stats.get("model"),
        content_hash=content_hash,
        commit_sha=commit_sha,
        extra_files=extra_hashes,
    )
    if source == PAST and ids:
        mark_past_prompts_used(seed_ids, ids[0])

    console.print(
        f"[green]>[/green] Generated [bold]{len(ids)}[/bold] question(s) for "
        f"[bold]{' + '.join([file_path, *extra_paths])}[/bold]"
    )
    console.print(
        f"  [dim]{stats['completion_tokens']} completion tokens, {stats['elapsed']:.1f}s · "
        f"seeded by {len(seed_ids)} {SOURCE_LABEL[source]}[/dim]"
    )
