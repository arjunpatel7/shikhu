"""shikhu inquiry-packet, record-inquiry — the conceptual-inquiry loop.

`inquiry-packet` finds which tracked files answer a question and why; `record-inquiry`
banks the exchange so `generate-from-study` can seed a quiz from it. Both are driven by the
shikhu-inquiry skill, which the agent triggers from its own description — there is no
detection pre-pass.
"""

import json

import typer
from dotenv import find_dotenv, load_dotenv

from shikhu.commands.utils import DEFAULT_EXTENSIONS, console, get_trackable_files
from shikhu.inquiry import (
    ALGO_VERSION,
    GATE_THRESHOLD,
    STRONG,
    _threshold,
    answerable,
    rank_files,
)
from shikhu.store import get_summary, init_db


def load_summaries(extensions: str = DEFAULT_EXTENSIONS) -> dict[str, str]:
    """Cached summaries for tracked files — the compressed index salience runs against.

    Files without a cached summary are skipped rather than read from disk: `shikhu
    summarize` owns that; this is the wrong place to spend an LLM call per file.

    Every summarized file is returned. An earlier version capped this, which silently judged
    an arbitrary subset on any repo past the cap and returned a confident-looking answer
    built on part of the codebase; `rank_files` now batches instead."""
    summaries = {}
    for path in get_trackable_files(extensions):
        row = get_summary(path)
        if row and row.get("summary_text"):
            summaries[path] = row["summary_text"]
    return summaries


def inquiry_packet(
    text: str = typer.Argument(..., help="The conceptual question to find files for."),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable output."),
) -> None:
    """Find the tracked files that answer a conceptual question, with the evidence why."""
    load_dotenv(find_dotenv(usecwd=True))
    init_db()
    summaries = load_summaries()
    if not summaries:
        console.print("[yellow]No cached file summaries — run `shikhu summarize` first.[/yellow]")
        raise typer.Exit(code=1)

    g, _ = answerable(text, summaries)
    if g < _threshold("SHIKHU_GATE_THRESHOLD", GATE_THRESHOLD):
        msg = "not answerable from this codebase"
        console.print(json.dumps({"answerable": False}) if as_json else f"[dim]{msg}.[/dim]")
        return

    ranked, stats = rank_files(text, summaries)
    if as_json:
        console.print(json.dumps({"answerable": True, "ranked": ranked}))
        return

    if not ranked:
        console.print("[dim]No file scored above the floor — answer normally.[/dim]")
        return

    strong = [(p, s) for p, s in ranked if s >= STRONG]
    for title, group in (("START HERE", strong), ("RELATED", [r for r in ranked if r[1] < STRONG])):
        if not group:
            continue
        console.print(f"\n[bold]{title}[/bold]")
        for path, s in group:
            console.print(f"  [{s:.2f}]  [bold]{path}[/bold]")
            if title == "START HERE":
                first = summaries[path].strip().split(". ")[0][:200]
                console.print(f"          [dim]{first}.[/dim]")
    console.print(
        f"\n[dim]{len(summaries)} files judged in "
        f"{stats['batches']} request{'s' if stats['batches'] != 1 else ''} · "
        f"{stats['elapsed'] * 1000:.0f}ms · ${stats['cost'] or 0:.6f}[/dim]"
    )


def record_inquiry(
    file_path: str = typer.Argument(..., help="The file the packet led with."),
    text: str = typer.Argument(..., help="The question the user asked."),
    confirmed: bool = typer.Option(
        ..., "--confirmed/--rejected", help="Did the packet point at the right place?"
    ),
    score: float = typer.Option(0.0, "--score", help="Salience score for file_path."),
    runner_up: str = typer.Option(None, "--runner-up", help="Second-ranked file, if any."),
    runner_up_score: float = typer.Option(0.0, "--runner-up-score"),
) -> None:
    """Record a confirmed conceptual inquiry so it can seed quiz questions later.

    Writes through the same tables /shikhu-study uses, so `generate-from-study` picks the
    question up as a seed with no new schema. Confirming validates the *question*; knowledge
    credit still comes only from answering it in a quiz."""
    init_db()
    from shikhu.attribution import AttributionResult
    from shikhu.store import (
        end_review,
        insert_attribution_label,
        log_study_question,
        set_attribution_user_label,
        start_review,
    )

    review_id = start_review(file_path)
    qid = log_study_question(
        review_id, text, was_conceptual=True, answered_satisfactorily=confirmed
    )
    label_id = insert_attribution_label(
        qid,
        "review_questions",
        AttributionResult(
            attributed_file=file_path,
            score=score,
            runner_up_file=runner_up,
            runner_up_score=runner_up_score,
            # The skill, an explicit /shikhu-inquiry, and a future hook all land here;
            # nothing distinguishes them, so record the loop, not a path we cannot know.
            signals={"source": "inquiry-loop"},
            algo_version=ALGO_VERSION,
        ),
    )
    set_attribution_user_label(label_id, "confirmed" if confirmed else "rejected")
    end_review(
        review_id,
        agent_summary=f"Conceptual inquiry via the inquiry loop. Files ranked by {ALGO_VERSION}.",
    )

    # A rejected inquiry is deliberately NOT a quiz seed: the question was real but the file
    # was wrong, so seeding it against that file would quiz the user on the wrong code.
    # get_conceptual_study_questions_for_file enforces this by skipping rejected rows.
    if confirmed:
        console.print(
            f"[green]>[/green] Recorded inquiry for [bold]{file_path}[/bold]. "
            f"Run [bold]shikhu generate-from-study {file_path}[/bold] to turn it into a quiz."
        )
    else:
        console.print("[dim]Recorded as a wrong-file correction — improves future ranking.[/dim]")
