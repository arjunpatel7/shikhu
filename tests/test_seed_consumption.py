"""Seeds are used once, and a re-asked question is re-queued instead of duplicated.

`generate-from-study` used to reuse every seed on every run, so running it twice wrote
near-duplicate questions. A seed is now consumed once a fresh question exists for it; a seed that
matches an unanswered question joins that question and moves it up the next quiz.
"""

import json
import sqlite3
from unittest.mock import patch

from conftest import runner

import shikhu.store as store
from shikhu import inquiry
from shikhu.cli import app
from shikhu.generator import MCQuestion, Quiz


def _inquiry(file_path, text, confirmed=True):
    rid = store.start_review(file_path)
    return store.log_study_question(
        rid, text, was_conceptual=True, answered_satisfactorily=confirmed
    )


def _question(file_path="a.py", seeds=None, text="Q?", **cols):
    (qid,) = store.insert_questions(
        file_path,
        [{"question_text": text, "choices": ["A", "B", "C", "D"], "expected_answer": "A"}],
        seed_query_ids=seeds,
        seed_query_source="review_questions" if seeds else None,
    )
    if cols:
        conn = store._get_conn()
        for col, val in cols.items():
            conn.execute(f"UPDATE questions SET {col} = ? WHERE id = ?", (val, qid))
        conn.commit()
        conn.close()
    return qid


def _row(qid):
    conn = store._get_conn()
    row = conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()
    conn.close()
    return row


def _texts(file_path="a.py", **kw):
    return [
        s["question_text"] for s in store.get_conceptual_study_questions_for_file(file_path, **kw)
    ]


# --- which seeds are consumed ---


def test_a_seed_with_a_fresh_question_is_consumed():
    used = _inquiry("a.py", "used")
    _inquiry("a.py", "unused")
    _question(seeds=[used])
    assert _texts() == ["used", "unused"]  # default keeps returning everything
    assert _texts(unconsumed=True) == ["unused"]


def test_a_seed_whose_question_went_stale_is_available_again():
    sid = _inquiry("a.py", "topic")
    _question(seeds=[sid], stale=True)
    assert _texts(unconsumed=True) == ["topic"]


def test_the_limit_applies_after_consumed_seeds_are_dropped():
    ids = [_inquiry("a.py", f"s{i}") for i in range(4)]
    _question(seeds=ids[:2])
    assert _texts(unconsumed=True, limit=1) == ["s2"]


def test_one_question_can_consume_several_seeds():
    ids = [_inquiry("a.py", f"s{i}") for i in range(3)]
    _question(seeds=ids[:2])
    assert _texts(unconsumed=True) == ["s2"]


# --- re-queueing ---


def test_requeueable_questions_are_fresh_unanswered_and_include_linked_files():
    own = _question("a.py", text="own")
    _question("a.py", text="answered", answered_at="2026-01-01 00:00:00")
    _question("a.py", text="stale", stale=True)
    (cross,) = store.insert_questions(
        "z.py",
        [{"question_text": "cross", "choices": ["A", "B", "C", "D"], "expected_answer": "A"}],
        extra_files={"a.py": None},
    )
    got = {q["id"] for q in store.get_requeueable_questions("a.py")}
    assert got == {own, cross}


def test_requeue_merges_seeds_and_stamps_reasked_at():
    first = _inquiry("a.py", "first")
    again = _inquiry("a.py", "again")
    qid = _question(seeds=[first])
    assert _row(qid)["reasked_at"] is None
    store.requeue_question(qid, [again])
    row = _row(qid)
    assert json.loads(row["seed_query_ids"]) == sorted([first, again])
    assert row["reasked_at"] is not None
    store.requeue_question(qid, [again])  # idempotent
    assert json.loads(_row(qid)["seed_query_ids"]) == sorted([first, again])


def test_requeue_does_not_mix_seed_sources():
    qid = _question(seed_query_source="raw_prompts", seed_query_ids="[7]")
    store.requeue_question(qid, [99])
    row = _row(qid)
    assert json.loads(row["seed_query_ids"]) == [7] and row["reasked_at"] is not None


def test_a_reasked_old_question_counts_as_recently_seeded():
    """The recency window reads the later of created_at and reasked_at: no new weight to tune."""
    old = _question(text="old", seeds=[1], created_at="2020-01-01 00:00:00")
    reasked = _question(text="reasked", seeds=[2], created_at="2020-01-01 00:00:00")
    store.requeue_question(reasked, [3])
    deterministic = store._SEED_BIAS_SQL.replace("(ABS(RANDOM()) % 100000)", "1000")
    conn = store._get_conn()
    weights = dict(conn.execute(f"SELECT id, {deterministic} FROM questions").fetchall())
    conn.close()
    assert weights[old] == 1000 * store.SEED_WEIGHT_OLDER
    assert weights[reasked] == 1000 * store.SEED_WEIGHT_RECENT


# --- the Jev match ---


def test_match_existing_scores_each_candidate_in_one_request():
    seen = {}

    def fake_ask(state, questions, **kw):
        seen["state"], seen["n"] = state, len(questions)
        return {k: {"noul": 0.9 if k == "q1" else 0.1} for k in questions}, {}

    with patch.object(inquiry.systemone, "ask", fake_ask):
        got = inquiry.match_existing("how does X work?", {10: "about X", 20: "about Y"})
    assert got == {10: 0.1, 20: 0.9}
    assert seen["n"] == 2 and seen["state"]["inquiry"] == "how does X work?"


def test_match_existing_with_no_candidates_makes_no_call():
    with patch.object(inquiry.systemone, "ask", side_effect=AssertionError("called")):
        assert inquiry.match_existing("q", {}) == {}


# --- the command ---


def _quiz():
    return Quiz(
        questions=[MCQuestion(question="Why?", choices=["w", "x", "y", "z"], correct_index=0)]
    )


def _run(match=None, generated=None):
    """Invoke generate-from-study with Jev and the writer mocked; returns (result, generator)."""
    gen = generated or (lambda *a, **k: (_quiz(), {"completion_tokens": 1, "elapsed": 0.1}, [], []))
    calls = []

    def fake_gen(file_path, num_questions=3, seeds=None):
        calls.append(seeds)
        return gen()

    with (
        patch("shikhu.generator.generate_questions_from_study_seeds", fake_gen),
        patch("shikhu.inquiry.match_existing", match or (lambda t, c: {})),
        patch("shikhu.commands.generate_from_study.ingest_recent"),
    ):
        return runner.invoke(app, ["generate-from-study", "a.py"]), calls


def test_no_seeds_at_all_still_exits_one():
    """The real generator returns None when a file has no seeds, before any API call."""
    with patch("shikhu.commands.generate_from_study.ingest_recent"):
        result = runner.invoke(app, ["generate-from-study", "a.py"])
    assert result.exit_code == 1


def test_all_seeds_consumed_says_so_and_does_not_generate():
    sid = _inquiry("a.py", "topic")
    _question(seeds=[sid])
    result, calls = _run()
    assert result.exit_code == 0 and calls == []
    assert "already has a quiz question" in result.output


def test_a_matching_seed_is_requeued_not_generated():
    old = _inquiry("a.py", "topic")
    qid = _question(seeds=[old])
    again = _inquiry("a.py", "same topic again")
    result, calls = _run(match=lambda t, c: {qid: 0.95})
    assert result.exit_code == 0 and calls == []
    assert "moved to the front" in result.output
    assert again in json.loads(_row(qid)["seed_query_ids"]) and _row(qid)["reasked_at"]


def test_only_the_unmatched_seeds_are_generated():
    old = _inquiry("a.py", "topic")
    qid = _question(seeds=[old])
    _inquiry("a.py", "same topic again")
    new = _inquiry("a.py", "a different topic")

    def match(text, cands):
        return {qid: 0.9 if "again" in text else 0.05}

    result, calls = _run(match=match)
    assert result.exit_code == 0, result.output
    assert [[s["id"] for s in calls[0]]] == [[new]]


def test_a_weak_match_still_generates():
    old = _inquiry("a.py", "topic")
    qid = _question(seeds=[old])
    _inquiry("a.py", "somewhat related")
    result, calls = _run(match=lambda t, c: {qid: 0.4})
    assert len(calls) == 1 and "moved to the front" not in result.output


def test_a_jev_failure_does_not_block_generation():
    old = _inquiry("a.py", "topic")
    _question(seeds=[old])
    _inquiry("a.py", "another")

    def boom(t, c):
        raise RuntimeError("jev down")

    result, calls = _run(match=boom)
    assert result.exit_code == 0 and len(calls) == 1


def test_an_answered_match_is_not_requeued():
    """Answered questions are not in the pool: relearning is tested by a new question."""
    old = _inquiry("a.py", "topic")
    qid = _question(seeds=[old], answered_at="2026-01-01 00:00:00")
    _inquiry("a.py", "topic again")
    asked = []
    result, calls = _run(match=lambda t, c: asked.append(c) or {})
    assert asked == [] and len(calls) == 1  # nothing to match against, so it generates
    assert qid not in {q["id"] for q in store.get_requeueable_questions("a.py")}


def test_old_databases_get_the_reasked_column():
    conn = sqlite3.connect(store.DB_PATH)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    conn.close()
    assert "reasked_at" in cols
