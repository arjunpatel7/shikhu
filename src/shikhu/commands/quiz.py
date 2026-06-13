"""shikhu quiz — take a quiz on your codebase."""

import json

import typer
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule

from shikhu.commands.utils import console, get_trackable_files
from shikhu.ingest import ingest_recent
from shikhu.store import (
    discard_revalidation,
    flag_question,
    get_attribution_label,
    get_revalidation_questions,
    get_seed_texts,
    get_unasked_questions,
    grade_question,
    init_db,
    mark_golden,
    mark_presented,
    reinstate_golden,
    set_attribution_user_label,
)

# Below this score-vs-runner-up gap, ask the user to confirm/correct the attribution.
ATTRIBUTION_CONFIDENCE_DELTA = 0.5


def _show_seed_context(q):
    """Display the original user queries that seeded this question, if any."""
    raw = q.get("seed_query_ids")
    source = q.get("seed_query_source")
    if not raw or not source:
        return
    try:
        seed_ids = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return
    seeds = get_seed_texts(seed_ids, source)
    if not seeds:
        return
    body = "\n".join(f"  • {s['text']}" for s in seeds[:3])
    if len(seeds) > 3:
        body += f"\n  [dim]…and {len(seeds) - 3} more[/dim]"
    console.print(
        Panel(
            body, title="[dim]Seeded by your earlier questions[/dim]", border_style="bright_black"
        )
    )


def _maybe_prompt_for_attribution(q):
    """Active-learning: prompt user on low-confidence seed attributions, log their verdict.

    Skips: questions without seeds, seeds with no attribution_label, seeds already labeled,
    high-confidence attributions (delta >= threshold)."""
    raw = q.get("seed_query_ids")
    source = q.get("seed_query_source")
    if not raw or not source:
        return
    try:
        seed_ids = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return

    for seed_id in seed_ids:
        label = get_attribution_label(seed_id, source)
        if label is None or label.get("user_label") is not None:
            continue
        delta = (label.get("score") or 0) - (label.get("runner_up_score") or 0)
        if delta >= ATTRIBUTION_CONFIDENCE_DELTA:
            continue

        seed_rows = get_seed_texts([seed_id], source)
        if not seed_rows:
            continue
        snippet = seed_rows[0]["text"]
        if len(snippet) > 80:
            snippet = snippet[:80] + "…"
        attributed = label.get("attributed_file")
        runner = label.get("runner_up_file")

        console.print(f"  [dim]Seed:[/dim] [italic]{snippet}[/italic]")
        choices = ["y", "n", "s"] if runner else ["y", "s"]
        ans = Prompt.ask(
            f"  Was this about [bold]{attributed}[/bold]?",
            choices=choices,
            default="y",
            show_choices=True,
        )
        if ans == "y":
            set_attribution_user_label(label["id"], "confirmed")
        elif ans == "n":
            set_attribution_user_label(label["id"], f"wrong:{runner}")
        else:
            set_attribution_user_label(label["id"], "skipped")


def quiz(
    n: int = typer.Option(5, help="Number of questions to ask."),
    file: str = typer.Option(None, help="Quiz on a single file only."),
):
    """Take a quiz on your codebase."""
    init_db()
    try:
        ingest_recent()
    except Exception:
        pass  # ingestion is non-critical
    # Re-validation questions (goldens whose file changed) come first, then fresh ones.
    questions = get_revalidation_questions(limit=n, file_path=file)
    if len(questions) < n:
        questions += get_unasked_questions(limit=n - len(questions), file_path=file)

    if not questions:
        console.print()
        if file:
            tracked = get_trackable_files()
            if file not in tracked:
                console.print(f"[yellow]File not tracked:[/yellow] [bold]{file}[/bold]")
                console.print(
                    "  Check for typos, or see if it's excluded by [bold].quizignore[/bold]."
                )
            else:
                console.print(
                    f"[dim]No questions available for[/dim] [bold]{file}[/bold]. Run [bold]shikhu refresh[/bold] to generate some."
                )
        else:
            console.print(
                "[dim]No questions available.[/dim] Run [bold]shikhu refresh[/bold] to generate some."
            )
        console.print()
        return

    score = 0
    total = len(questions)

    console.print()
    console.print(Rule(f"[bold]Quiz — {total} question{'s' if total != 1 else ''}[/bold]"))
    console.print()

    for i, q in enumerate(questions, 1):
        choices = json.loads(q["choices"]) if isinstance(q["choices"], str) else q["choices"]
        expected = q["expected_answer"]

        # Show seed context (if this question was seeded by /shikhu-study questions)
        _show_seed_context(q)

        # Build question display
        choice_text = "\n".join(f"  [bold]{chr(65 + j)}[/bold]) {c}" for j, c in enumerate(choices))
        console.print(
            Panel(
                f"{q['question_text']}\n\n{choice_text}",
                title=f"[bold]{i}[/bold] of {total}",
                subtitle=f"[dim]{q['file_path']}[/dim]",
                border_style="cyan",
            )
        )
        mark_presented(q["id"])  # stamp display time so answered_at - presented_at is derivable

        # Get answer
        answer = Prompt.ask("Your answer", choices=["a", "b", "c", "d"], show_choices=True)
        answer_idx = ord(answer.lower()) - ord("a")
        user_answer = choices[answer_idx]
        correct = user_answer == expected

        if correct:
            score += 1
            console.print("  [bold green]Correct![/bold green]")
        else:
            correct_letter = chr(65 + choices.index(expected))
            console.print(
                f"  [bold red]Wrong.[/bold red] Answer was [bold]{correct_letter}[/bold]) {expected}"
            )

        grade_question(q["id"], user_answer=user_answer, correct=correct)

        if q.get("pending_revalidation"):
            # Re-validation: a golden whose file changed. Re-earn it or retire it.
            if correct:
                keep = Prompt.ask(
                    "  [yellow]Golden re-check[/yellow] — the file changed; does this still test understanding of it?",
                    choices=["y", "n"],
                    default="y",
                )
                if keep == "y":
                    reinstate_golden(q["id"])
                    console.print("  [bold yellow]Golden re-validated![/bold yellow]")
                else:
                    discard_revalidation(q["id"])
                    console.print("  [dim]Golden retired.[/dim]")
            else:
                discard_revalidation(q["id"])
                console.print("  [dim]Golden retired (answered incorrectly).[/dim]")
        else:
            # Quality flag
            flag = Prompt.ask(
                "  Rate this question [dim](g=good, b=bad, s=skip)[/dim]",
                choices=["g", "b", "s"],
                default="s",
                show_choices=False,
            )
            if flag == "g":
                flag_question(q["id"], quality_flag="good")
                # Golden question check — only if correct + good
                if correct:
                    rep = Prompt.ask(
                        "  Would this question test someone's understanding of this file?",
                        choices=["y", "n"],
                        default="n",
                    )
                    if rep == "y":
                        mark_golden(q["id"])
                        console.print("  [bold yellow]Golden question![/bold yellow]")
            elif flag == "b":
                flag_question(q["id"], quality_flag="bad")

        # Active-learning: confirm/correct low-confidence seed attributions (dormant for /shikhu-study seeds)
        _maybe_prompt_for_attribution(q)

        console.print()

    # Score summary
    pct = (score / total * 100) if total else 0
    if pct >= 80:
        style = "bold green"
    elif pct >= 50:
        style = "bold yellow"
    else:
        style = "bold red"

    console.print(Rule())
    console.print(f"  [{style}]{score}/{total}[/{style}] correct ({pct:.0f}%)")
    console.print()
