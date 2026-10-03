"""Tests for the attempts table: who answered what, kept apart by respondent.

The point of the table is that two respondents never share a slot. questions.graded_correct
is one cell holding the latest answer, so an agent answering a question would overwrite the
user's result and silently move their coverage. These tests are mostly about that.
"""

import sqlite3

import pytest
from conftest import _insert_questions

import shikhu.store as store


def _question_row(qid):
    conn = store._get_conn()
    row = conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()
    conn.close()
    return row


def test_insert_and_read_back_every_field():
    (qid,) = _insert_questions(n=1)
    aid = store.insert_attempt(
        qid,
        "claude-code/haiku-4.5",
        "open",
        answer_text="It starts a review and prints the id.",
        correct=True,
        score=0.94,
        graded_by="jev-1.13/match-v1",
        condition="file",
        run_id="run-1",
        meta={"input_tokens": 1200, "cost_usd": 0.002, "tool_calls": 3},
    )
    (row,) = store.get_attempts(question_id=qid)
    assert row["id"] == aid
    assert row["respondent"] == "claude-code/haiku-4.5"
    assert row["mode"] == "open"
    assert row["answer_text"] == "It starts a review and prints the id."
    assert row["correct"] == 1
    assert row["score"] == 0.94
    assert row["graded_by"] == "jev-1.13/match-v1"
    assert row["condition"] == "file"
    assert row["run_id"] == "run-1"
    assert row["meta"] == {"input_tokens": 1200, "cost_usd": 0.002, "tool_calls": 3}
    assert row["answered_at"] is not None


def test_a_failed_attempt_keeps_its_error_apart_from_a_wrong_answer():
    """An answer that was wrong and a respondent that crashed are different findings."""
    (qid,) = _insert_questions(n=1)
    store.insert_attempt(qid, "gemini", "open", correct=False, error="RuntimeError: truncated")
    store.insert_attempt(qid, "gemini", "open", answer_text="a wrong answer", correct=False)
    failed, wrong = store.get_attempts(question_id=qid)
    assert failed["error"] and not wrong["error"]


def test_filters_by_question_respondent_and_run():
    q1, q2 = _insert_questions(n=2)
    store.insert_attempt(q1, "human", "choice", correct=True)
    store.insert_attempt(q1, "agent-a", "open", run_id="r1")
    store.insert_attempt(q2, "agent-a", "open", run_id="r2")
    store.insert_attempt(q2, "agent-b", "open", run_id="r2")
    assert len(store.get_attempts()) == 4
    assert len(store.get_attempts(question_id=q1)) == 2
    assert len(store.get_attempts(respondent="agent-a")) == 2
    assert len(store.get_attempts(run_id="r2")) == 2
    assert len(store.get_attempts(question_id=q2, respondent="agent-b")) == 1
    assert store.get_attempts(respondent="nobody") == []


def test_an_agent_attempt_never_touches_the_users_result():
    """The whole reason this table exists."""
    (qid,) = _insert_questions(n=1)
    store.grade_question(qid, user_answer="A", correct=False)

    store.insert_attempt(qid, "claude-code/haiku-4.5", "open", answer_text="x", correct=True)
    store.insert_attempt(qid, "pi/gpt-5", "open", answer_text="y", correct=True)

    row = _question_row(qid)
    assert row["graded_correct"] == 0
    assert row["user_answer"] == "A"
    humans = store.get_attempts(question_id=qid, respondent="human")
    assert [a["correct"] for a in humans] == [0]


def test_grade_question_still_updates_the_columns_coverage_reads():
    (qid,) = _insert_questions(n=1)
    store.grade_question(qid, user_answer="B", correct=True)
    row = _question_row(qid)
    assert (row["user_answer"], row["graded_correct"]) == ("B", 1)
    assert row["answered_at"] is not None


def test_grade_question_also_records_a_human_attempt():
    (qid,) = _insert_questions(n=1)
    store.grade_question(qid, user_answer="B", correct=True)
    (a,) = store.get_attempts(question_id=qid)
    assert (a["respondent"], a["mode"], a["answer_text"], a["correct"], a["graded_by"]) == (
        "human",
        "choice",
        "B",
        1,
        "key",
    )


def test_reanswering_keeps_the_history_the_questions_table_loses():
    """grade_question overwrote the previous answer; attempts is what finally remembers it."""
    (qid,) = _insert_questions(n=1)
    store.grade_question(qid, user_answer="A", correct=False)
    store.grade_question(qid, user_answer="B", correct=True)
    assert [a["answer_text"] for a in store.get_attempts(question_id=qid)] == ["A", "B"]
    row = _question_row(qid)
    assert (row["user_answer"], row["graded_correct"]) == ("B", 1)  # latest only, as before


def test_attempt_for_a_missing_question_is_rejected():
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_attempt(9999, "human", "choice")


# --- backfilling history that predates the table ---


def _answer_without_an_attempt(qid, answer="A", correct=True):
    """Reproduce a DB from before the table existed: answered, but no attempt row."""
    conn = store._get_conn()
    conn.execute(
        "UPDATE questions SET answered_at = CURRENT_TIMESTAMP, user_answer = ?, "
        "graded_correct = ? WHERE id = ?",
        (answer, correct, qid),
    )
    conn.execute("DELETE FROM attempts WHERE question_id = ?", (qid,))
    conn.commit()
    conn.close()


def test_backfill_carries_over_existing_answers_once():
    answered, unanswered = _insert_questions(n=2)
    _answer_without_an_attempt(answered, answer="C", correct=False)

    store.init_db()
    store.init_db()  # init_db runs on every command; it must not duplicate

    rows = store.get_attempts()
    assert len(rows) == 1
    a = rows[0]
    assert (a["question_id"], a["respondent"], a["answer_text"], a["correct"]) == (
        answered,
        "human",
        "C",
        0,
    )
    assert a["run_id"] == "backfill" and a["graded_by"] == "key"
    assert store.get_attempts(question_id=unanswered) == []


def test_backfill_does_not_shadow_a_later_reanswer():
    (qid,) = _insert_questions(n=1)
    _answer_without_an_attempt(qid, answer="A", correct=False)
    store.init_db()
    store.grade_question(qid, user_answer="B", correct=True)
    store.init_db()
    assert [a["answer_text"] for a in store.get_attempts(question_id=qid)] == ["A", "B"]


def test_init_db_survives_a_legacy_questions_table(tmp_path):
    """A database that predates the answer columns must still open: init_db runs on every
    command, so a backfill assuming those columns would break shikhu for that user entirely."""
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY, file_path TEXT)")
    conn.commit()
    conn.close()

    store.DB_PATH = str(old)
    store.init_db()

    conn = sqlite3.connect(old)
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    conn.close()


def test_coverage_inputs_are_unaffected_by_attempts():
    """Attempts is the record, not yet the source of truth — goldens still come from questions."""
    ids = _insert_questions(n=2)
    store.grade_question(ids[0], user_answer="A", correct=True)
    store.mark_golden(ids[0])
    before = store.get_golden_counts()
    for qid in ids:
        store.insert_attempt(qid, "agent", "open", correct=True)
    assert store.get_golden_counts() == before
