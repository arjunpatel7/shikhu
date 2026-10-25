"""shikhu map — where your past questions about this codebase have been, against coverage.

Reads this repo's Claude Code transcripts, has Jev judge which past prompts were questions
about the code and which files they were about, then draws the map: the files you have
asked about most, the ones you never have, your golden coverage, and one file to start on.

The first run scores every past prompt; later runs only score prompts added since. Scoring
is the one paid step, so it shows an estimate and asks first.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv
from rich.table import Table

from shikhu import systemone
from shikhu.commands.inquiry import load_summaries
from shikhu.commands.utils import (
    DEFAULT_EXTENSIONS,
    console,
    ensure_api_key,
    get_trackable_files,
    golden_target,
)
from shikhu.inquiry import MAP_ALGO_VERSION, past_question_bar, score_past_prompt
from shikhu.staleness import mark_stale_questions
from shikhu.store import (
    add_past_prompts,
    count_past_prompts,
    get_golden_counts,
    get_past_question_seeds,
    get_past_questions_per_file,
    get_unscored_past_prompts,
    init_db,
    save_past_prompt_score,
)
from shikhu.transcripts import project_transcripts_dir, scan

# Files shorter than this hold no concept to ask about, yet a package __init__ whose summary
# describes the whole package soaks up broad questions. They are left off the map.
MIN_MAP_LINES = 5

# Cost estimate. Jev bills about $0.042 per million tokens; every Noul's instructions ride
# along with the state, so a request costs roughly the summaries plus a fixed share per Noul.
JEV_USD_PER_TOKEN = 0.042e-6
TOKENS_PER_NOUL = 150

TOP_N = 8


def _estimate_usd(prompts: list[dict], summaries: dict[str, str]) -> float:
    summary_tokens = sum(len(s) for s in summaries.values()) // 4
    nouls = len(summaries) + 2  # one per file, plus detection and the presence gate
    tokens = sum(
        summary_tokens
        + nouls * TOKENS_PER_NOUL
        + (len(p["prompt_text"]) + len(p.get("context") or "")) // 4
        for p in prompts
    )
    return tokens * JEV_USD_PER_TOKEN


def _line_count(path: str) -> int:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _score_all(
    prompts: list[dict], summaries: dict[str, str], workers: int
) -> tuple[int, int, bool]:
    """Score prompts in parallel, saving each as it lands. Returns (scored, failed, capped).

    Saving as we go makes the run resumable: anything left unscored, by a failure or by
    the spending cap, is picked up by the next `shikhu map`."""
    scored = failed = 0
    capped = False
    with console.status("") as status, ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(score_past_prompt, p["prompt_text"], summaries, p.get("context")): p
            for p in prompts
        }
        for fut in as_completed(futures):
            p = futures[fut]
            if fut.cancelled():
                continue
            try:
                result, _ = fut.result()
            except systemone.BudgetExceeded:
                capped = True
                for f in futures:
                    f.cancel()
                continue
            except Exception:
                failed += 1
                continue
            save_past_prompt_score(
                p["id"],
                result["conceptual"],
                result["answerable"],
                result["files"],
                MAP_ALGO_VERSION,
            )
            scored += 1
            status.update(
                f"Scoring past prompts… {scored}/{len(prompts)}  "
                f"[dim]${systemone.spent_usd():.4f}[/dim]"
            )
    return scored, failed, capped


def _start_here(lead: dict[str, int], golden: dict[str, int], target: dict[str, int]) -> str | None:
    """One easy file to begin with: among the files your questions were mainly about, the
    shortest one that isn't covered yet, preferring files with no goldens at all.

    Ranked by lead counts, not every file a question touched: a broad "how does it all fit
    together" credits many files without being about any one of them. Never a file you
    haven't asked about: those are likely the hardest, and the first quiz shouldn't be a wall."""
    asked = sorted(lead, key=lambda f: -lead[f])
    if not asked:
        return None
    # The top half by lead count, plus anything tied with the last file in it: a tie at the
    # cut shouldn't be settled by file name.
    bar = lead[asked[max(1, (len(asked) + 1) // 2) - 1]]
    top = [f for f in asked if lead[f] >= bar]
    pool = [f for f in top if golden.get(f, 0) == 0] or [
        f for f in top if golden.get(f, 0) < target[f]
    ]
    if not pool:
        return None
    return min(pool, key=lambda f: (_line_count(f), -lead[f], f))


def _draw(tracked: list[str], prompt_counts: dict) -> None:
    per_file, lead, total = get_past_questions_per_file(**past_question_bar())
    tracked_set = set(tracked)
    per_file = {f: n for f, n in per_file.items() if f in tracked_set}
    lead = {f: n for f, n in lead.items() if f in tracked_set}
    golden = get_golden_counts()
    target = {f: golden_target(f) for f in tracked}
    covered = sum(1 for f in tracked if golden.get(f, 0) >= target[f])
    pct = covered / len(tracked) * 100 if tracked else 0

    console.print()
    console.print("[bold]Your question map[/bold]")
    console.print(
        f"  [bold]{total}[/bold] past questions about this codebase "
        f"[dim](from {prompt_counts['scored']} past prompts)[/dim]"
    )
    console.print(
        f"  Coverage: [bold]{covered}[/bold]/{len(tracked)} files fully covered ({pct:.0f}%)"
    )

    if not per_file:
        console.print(
            "\n  None of your past prompts look like questions about this code yet. "
            "Ask one with [bold]/shikhu-inquiry[/bold] and it will show up here."
        )
        return

    table = Table(title="Asked about most", title_justify="left", box=None, pad_edge=False)
    table.add_column("  file")
    table.add_column("questions", justify="right")
    table.add_column("golden", justify="right")
    for f in sorted(per_file, key=lambda f: (-per_file[f], f))[:TOP_N]:
        table.add_row(f"  {f}", str(per_file[f]), f"{golden.get(f, 0)}/{target[f]}")
    console.print()
    console.print(table)

    never = [f for f in tracked if f not in per_file]
    if never:
        console.print()
        console.print(
            f"[bold]Not asked about yet[/bold] [dim]({len(never)} files, largest first — "
            "worth a look later)[/dim]"
        )
        sizes = {f: _line_count(f) for f in never}
        for f in sorted(never, key=lambda f: (-sizes[f], f))[:5]:
            console.print(f"  {f}  [dim]({sizes[f]} lines)[/dim]")

    pick = _start_here(lead, golden, target)
    if pick:
        n, g = lead[pick], golden.get(pick, 0)
        console.print()
        console.print(f"[bold green]Start here:[/bold green] [bold]{pick}[/bold]")
        console.print(
            f"  {n} of your questions {'was' if n == 1 else 'were'} mainly about it, it has "
            f"{g}/{target[pick]} golden, and it's {_line_count(pick)} lines. "
            "Ask about it with [bold]/shikhu-inquiry[/bold]."
        )
        if get_past_question_seeds(pick, **past_question_bar()):
            console.print(
                "  Or quiz on what you already asked: "
                f"[bold]shikhu generate-from-study {pick}[/bold], then "
                f"[bold]shikhu quiz --file {pick}[/bold]."
            )
    console.print()


def map_(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Scan and estimate the cost, but don't score anything."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Score without asking first."),
    workers: int = typer.Option(8, "--workers", help="Parallel requests while scoring."),
    transcripts: str = typer.Option(
        None,
        "--transcripts",
        help="Transcript directory to read (default: this repo's Claude Code project dir).",
    ),
) -> None:
    """Map where your past questions about this codebase have been, against coverage.

    Reads this repo's Claude Code transcripts (they stay on your machine), then sends each
    past prompt with your file summaries to Jev through OpenRouter to find which were
    questions about the code and which files they were about. Asks before spending."""
    load_dotenv(find_dotenv(usecwd=True))
    init_db()
    # Local and free, as in coverage and quiz: an edited file's goldens shouldn't still count.
    mark_stale_questions()

    # Tiny files are dropped before scoring as well as from the map: no point paying to
    # score a file the map won't show.
    summaries = {f: s for f, s in load_summaries().items() if _line_count(f) >= MIN_MAP_LINES}
    if not summaries:
        console.print("[yellow]No cached file summaries — run `shikhu summarize` first.[/yellow]")
        raise typer.Exit(code=1)
    tracked = [
        f for f in get_trackable_files(DEFAULT_EXTENSIONS) if _line_count(f) >= MIN_MAP_LINES
    ]

    transcripts_dir = Path(transcripts) if transcripts else project_transcripts_dir()
    prompts, n_transcripts = scan(transcripts_dir)
    added = add_past_prompts(prompts)
    counts = count_past_prompts(MAP_ALGO_VERSION)
    if not counts["total"]:
        console.print(
            f"No Claude Code transcripts found for this repo [dim](looked in {transcripts_dir})[/dim]."
        )
        console.print("[dim]shikhu map reads Claude Code sessions run from this directory.[/dim]")
        return

    console.print(
        f"Read {n_transcripts} transcript{'s' if n_transcripts != 1 else ''}: "
        f"{len(prompts)} distinct past prompts, {added} new since the last map."
    )
    missing = len(tracked) - len([f for f in tracked if f in summaries])
    if missing > 0:
        console.print(
            f"[dim]{missing} tracked file(s) have no summary and can't be mapped — "
            "`shikhu summarize` adds them.[/dim]"
        )

    unscored = get_unscored_past_prompts(MAP_ALGO_VERSION)
    if unscored:
        est = _estimate_usd(unscored, summaries)
        console.print(
            f"{len(unscored)} prompt(s) to score. Each is sent with your {len(summaries)} file "
            f"summaries to Jev through OpenRouter: about [bold]${est:.2f}[/bold] on your key."
        )
        if dry_run:
            console.print("[dim]Dry run: nothing was sent. Run `shikhu map` to score them.[/dim]")
        elif yes or typer.confirm("Score them now?", default=False):
            ensure_api_key()
            scored, failed, capped = _score_all(unscored, summaries, workers)
            console.print(
                f"[green]>[/green] Scored {scored} prompt(s) for ${systemone.spent_usd():.4f}."
            )
            if failed:
                console.print(
                    f"[yellow]{failed} failed and stay unscored; the next `shikhu map` retries them.[/yellow]"
                )
            if capped:
                console.print(
                    "[yellow]Stopped at the Jev spending cap (SHIKHU_JEV_BUDGET_USD). "
                    "Run `shikhu map` again to continue.[/yellow]"
                )
            counts = count_past_prompts(MAP_ALGO_VERSION)

    if not counts["scored"]:
        return
    _draw(tracked, counts)
