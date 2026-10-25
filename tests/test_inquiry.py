"""Tests for file salience — the gate and the per-file ranking behind `shikhu inquiry-packet`.

Batching is the subtle part: judgments are split across requests when a repo has more files
than fit one state, and answer keys restart at f0 in every batch, so a mapping slip would
silently attribute a question to the wrong file.
"""

from unittest.mock import patch

import pytest

from shikhu import inquiry
from shikhu.commands.inquiry import load_summaries


def _answer(p: float) -> dict:
    return {"noul": p}


# --- ranking ---


def test_rank_files_one_request_and_floor(monkeypatch):
    """All files ride one request, and the noise tail is dropped."""
    summaries = {"a.py": "sa", "b.py": "sb", "c.py": "sc"}
    answers = {"f0": _answer(0.93), "f1": _answer(0.05), "f2": _answer(0.41)}
    with patch(
        "shikhu.inquiry.systemone.ask", return_value=(answers, {"elapsed": 0.2, "cost": 0.0})
    ) as ask:
        ranked, _ = inquiry.rank_files("q", summaries)
    assert ask.call_count == 1
    assert ranked == [("a.py", 0.93), ("c.py", 0.41)]  # b.py below SHOW_FLOOR, sorted desc


def test_rank_files_maps_answers_back_to_paths(monkeypatch):
    """Answer keys are positional; a mismatch would silently attribute the wrong file."""
    summaries = {f"f{i}.py": "s" for i in range(12)}
    answers = {f"f{i}": _answer(0.9 if i == 11 else 0.0) for i in range(12)}
    with patch("shikhu.inquiry.systemone.ask", return_value=(answers, {"elapsed": 0, "cost": 0})):
        ranked, _ = inquiry.rank_files("q", summaries)
    assert ranked == [("f11.py", 0.9)]


# --- summaries ---


def test_load_summaries_skips_unsummarized_and_keeps_everything_else(monkeypatch):
    """Files without a cached summary are skipped; summarized ones are never dropped.

    An earlier cap silently judged an arbitrary subset on a large repo, which looked like a
    confident answer but was computed from part of the codebase."""
    paths = [f"f{i}.py" for i in range(250)]
    monkeypatch.setattr("shikhu.commands.inquiry.get_trackable_files", lambda ext: paths)
    monkeypatch.setattr(
        "shikhu.commands.inquiry.get_summary",
        lambda p: {"summary_text": "s"} if p != "f2.py" else None,
    )
    out = load_summaries()
    assert "f2.py" not in out
    assert len(out) == 249


# --- a rejected inquiry must not become a quiz seed ---


def _seed_texts(file_path):
    from shikhu.store import get_conceptual_study_questions_for_file

    return [r["question_text"] for r in get_conceptual_study_questions_for_file(file_path)]


def test_rejected_inquiry_is_not_a_quiz_seed():
    """Rejecting a packet says "wrong file". Seeding it anyway would generate quiz questions
    about code the user already told us the question was not about."""
    from shikhu.store import log_study_question, start_review

    rid = start_review("src/shikhu/store.py")
    log_study_question(rid, "confirmed one", was_conceptual=True, answered_satisfactorily=True)
    log_study_question(rid, "rejected one", was_conceptual=True, answered_satisfactorily=False)
    log_study_question(rid, "unjudged one", was_conceptual=True, answered_satisfactorily=None)

    seeds = _seed_texts("src/shikhu/store.py")
    assert "rejected one" not in seeds
    # Unjudged questions predate this distinction and must keep working.
    assert seeds == ["confirmed one", "unjudged one"]


def test_non_conceptual_questions_still_excluded():
    """The pre-existing was_conceptual filter must survive the new clause."""
    from shikhu.store import log_study_question, start_review

    rid = start_review("a.py")
    log_study_question(rid, "chit chat", was_conceptual=False, answered_satisfactorily=True)
    assert _seed_texts("a.py") == []


# --- batching across the state budget ---


def test_rank_files_batches_large_repos_and_keeps_scores_comparable(monkeypatch):
    """Per-file Nouls are independent, so scores from different requests rank together."""
    monkeypatch.setattr(inquiry, "BATCH_FILES", 10)
    summaries = {f"f{i}.py": "s" for i in range(25)}

    # The best file sits in the last batch; a cap would have thrown it away entirely.
    def fake_ask(state, questions, model=None):
        names = list(state["files"])
        scores = {
            f"f{i}": _answer(0.97 if names[i] == "f24.py" else 0.01) for i in range(len(names))
        }
        return scores, {"elapsed": 0.1, "cost": 0.0001, "model": "jev-1.13"}

    with patch("shikhu.inquiry.systemone.ask", side_effect=fake_ask) as ask:
        ranked, stats = inquiry.rank_files("q", summaries)
    assert ask.call_count == 3  # 10 + 10 + 5
    assert stats["batches"] == 3
    assert ranked == [("f24.py", 0.97)]
    assert stats["cost"] == pytest.approx(0.0003)


def test_rank_files_batch_indices_do_not_collide(monkeypatch):
    """Answer keys restart at f0 each batch; mapping them to the wrong file would silently
    attribute a question to whatever happened to sit at that index in batch one."""
    monkeypatch.setattr(inquiry, "BATCH_FILES", 2)
    summaries = {"a.py": "s", "b.py": "s", "c.py": "s", "d.py": "s"}

    def fake_ask(state, questions, model=None):
        names = list(state["files"])
        # Only the second file of each batch is relevant.
        return {f"f{i}": _answer(0.9 if i == 1 else 0.0) for i in range(len(names))}, {
            "elapsed": 0,
            "cost": 0,
        }

    with patch("shikhu.inquiry.systemone.ask", side_effect=fake_ask):
        ranked, _ = inquiry.rank_files("q", summaries)
    assert sorted(ranked) == [("b.py", 0.9), ("d.py", 0.9)]


def test_gate_takes_the_max_across_batches(monkeypatch):
    """One batch holding the answer is enough — averaging would let a big repo dilute it."""
    monkeypatch.setattr(inquiry, "BATCH_FILES", 1)
    summaries = {"a.py": "s", "b.py": "s", "c.py": "s"}
    probs = iter([0.04, 0.91, 0.02])
    with patch(
        "shikhu.inquiry.systemone.ask",
        side_effect=lambda **kw: ({"gate": _answer(next(probs))}, {"elapsed": 0, "cost": 0}),
    ):
        g, stats = inquiry.answerable("q", summaries)
    assert g == 0.91
    assert stats["batches"] == 3


def test_empty_summaries_does_not_call_the_api(monkeypatch):
    with patch("shikhu.inquiry.systemone.ask") as ask:
        ranked, stats = inquiry.rank_files("q", {})
    assert ranked == [] and stats["batches"] == 0
    ask.assert_not_called()
