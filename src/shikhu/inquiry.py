"""File salience for a conceptual question — the Jev judgments behind `shikhu inquiry-packet`.

Two stages, in order:

    answerable(text, summaries)   presence gate. Independent per-file Nouls have no "none of
                                  the above", so without this the file scorer confidently
                                  picks files for questions this repo cannot answer at all.
    rank_files(text, summaries)   one Noul per candidate file, all in a single request — they
                                  share the state, so 25 judgments cost barely more than one.

Deciding *whether* a turn is a conceptual question is no longer done here. A Jev pre-pass
did it from a UserPromptSubmit hook; measurement showed the shikhu-inquiry skill's own
description triggers reliably on its own, so the hook and its detection Noul were removed
rather than left dormant. `evals/` keeps the benchmark that retired them.
"""

import os

from shikhu import systemone

GATE_THRESHOLD = 0.5
SHOW_FLOOR = 0.30  # below this, don't even mention the file
STRONG = 0.60  # above this, lead with it

# Salience state is summaries, not source: ~270 tokens per file against a 32k budget, so
# roughly this many files fit in one request. Bigger repos are split across several requests
# rather than narrowed by a cheap prefilter: Phase 1 measured that a prefilter ahead of Jev
# destroys recall (12.7% vs 51%), and at ~$0.0003 per batch there is nothing to save by
# dropping candidates. Batching is only sound because the per-file judgments are independent
# Nouls — scores are absolute, so they stay comparable across requests. A Choice would not
# survive the same treatment.
BATCH_FILES = 100

ALGO_VERSION = f"jev-salience/{systemone.DEFAULT_JEV_MODEL}"

_GATE = systemone.noul(
    "Can the question in `state.question` be answered from the files described in `state.files`?",
    true_means="At least one described file contains the code the question is about.",
    false_means=(
        "The question is about something outside this codebase — another tool, a library, "
        "the assistant itself, or general programming knowledge."
    ),
)


# A re-asked question that matches an existing, unanswered quiz question is re-queued instead of
# generating a near-duplicate. Wrong either way is cheap (a duplicate, or a missed re-queue), so
# the bar sits well above a coin flip but below a near-certain match.
ALIGN_THRESHOLD = 0.7
MAX_ALIGN_CANDIDATES = 40


def match_existing(text: str, candidates: dict[int, str]) -> dict[int, float]:
    """Probability that each existing quiz question tests the concept `text` asked about.

    One independent judgment per candidate, all in one request."""
    ids = list(candidates)[:MAX_ALIGN_CANDIDATES]
    if not ids:
        return {}
    questions = {
        f"q{i}": systemone.noul(
            {
                "quiz_question": str(cid),
                "question": (
                    "Does the quiz question `quiz_question` in `state.quiz_questions` test the "
                    "same concept the developer asked about in `state.inquiry`?"
                ),
            },
            true_means="Answering the quiz question would teach what the developer asked about.",
            false_means="It covers a different concept, or only touches the topic in passing.",
        )
        for i, cid in enumerate(ids)
    }
    answers, _ = systemone.ask(
        state={"inquiry": text, "quiz_questions": {str(c): candidates[c] for c in ids}},
        questions=questions,
    )
    return {ids[int(k[1:])]: v["noul"] for k, v in answers.items()}


def _threshold(name: str, default: float) -> float:
    """Env-overridable threshold, so dogfooding can retune without an edit."""
    raw = os.environ.get(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _batches(summaries: dict[str, str], size: int) -> list[dict[str, str]]:
    items = list(summaries.items())
    return [dict(items[i : i + size]) for i in range(0, len(items), size)] or [{}]


def _merge_stats(parts: list[dict]) -> dict:
    """Combine per-request stats into one. Elapsed is summed: the batches run in sequence."""
    return {
        "elapsed": sum(s.get("elapsed") or 0 for s in parts),
        "cost": sum(s.get("cost") or 0 for s in parts),
        "batches": len(parts),
        "model": next((s.get("model") for s in parts if s.get("model")), None),
    }


def answerable(text: str, summaries: dict[str, str]) -> tuple[float, dict]:
    """Probability that any tracked file can answer `text`.

    Across batches this is a max, not a mean: one batch containing the answer is enough,
    and averaging would let a large repo dilute a confident yes into a no."""
    probs, parts = [], []
    for batch in _batches(summaries, BATCH_FILES):
        answers, stats = systemone.ask(
            state={"question": text, "files": batch}, questions={"gate": _GATE}
        )
        probs.append(answers["gate"]["noul"])
        parts.append(stats)
    return max(probs), _merge_stats(parts)


def rank_files(text: str, summaries: dict[str, str]) -> tuple[list[tuple[str, float]], dict]:
    """Score every candidate file for relevance to `text`, best first.

    Returns only files at or above SHOW_FLOOR — a long tail of 0.02s is noise, not evidence."""
    scored, parts = {}, []
    for batch in _batches(summaries, BATCH_FILES):
        if not batch:
            continue
        batch_scored, stats = _rank_batch(text, batch)
        scored.update(batch_scored)
        parts.append(stats)
    floor = _threshold("SHIKHU_SHOW_FLOOR", SHOW_FLOOR)
    ranked = [(p, s) for p, s in sorted(scored.items(), key=lambda kv: -kv[1]) if s >= floor]
    return ranked, _merge_stats(parts)


def _rank_batch(text: str, summaries: dict[str, str]) -> tuple[dict[str, float], dict]:
    """Score one request's worth of files. All judgments ride a single request."""
    paths = list(summaries)
    questions = {
        f"f{i}": systemone.noul(
            {
                "file": path,
                "question": (
                    "Would a developer need to read `file` to answer the question in "
                    "`state.question` about this codebase?"
                ),
            },
            true_means=(
                "This file contains the code the question is about, or code essential to "
                "explaining it."
            ),
            false_means="This file is unrelated, or only incidentally mentions the topic.",
        )
        for i, path in enumerate(paths)
    }
    answers, stats = systemone.ask(
        state={"question": text, "files": summaries}, questions=questions
    )
    return {paths[int(k[1:])]: v["noul"] for k, v in answers.items()}, stats
