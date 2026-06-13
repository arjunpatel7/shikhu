"""Tests for shikhu quiz command."""

from conftest import _insert_questions, runner

import shikhu.store as store


def test_quiz_shows_question():
    """shikhu quiz displays the question text."""
    from shikhu.cli import app

    _insert_questions("fake.py", n=1)
    result = runner.invoke(app, ["quiz", "--n", "1"], input="a\ns\n")
    assert result.exit_code == 0
    assert "Q0" in result.output


def test_quiz_correct_answer():
    """Answering correctly sets graded_correct=True."""
    from shikhu.cli import app

    ids = _insert_questions("fake.py", n=1)
    result = runner.invoke(app, ["quiz", "--n", "1"], input="a\ns\n")
    assert result.exit_code == 0
    conn = store._get_conn()
    row = conn.execute("SELECT graded_correct FROM questions WHERE id = ?", (ids[0],)).fetchone()
    conn.close()
    assert row["graded_correct"] == 1


def test_quiz_wrong_answer():
    """Answering wrong sets graded_correct=False."""
    from shikhu.cli import app

    ids = _insert_questions("fake.py", n=1)
    result = runner.invoke(app, ["quiz", "--n", "1"], input="b\ns\n")
    assert result.exit_code == 0
    conn = store._get_conn()
    row = conn.execute("SELECT graded_correct FROM questions WHERE id = ?", (ids[0],)).fetchone()
    conn.close()
    assert row["graded_correct"] == 0


def test_quiz_skips_stale():
    """Stale questions are never shown."""
    from shikhu.cli import app

    _insert_questions("fake.py", n=2, stale=True)
    result = runner.invoke(app, ["quiz", "--n", "5"])
    assert result.exit_code == 0
    assert "No questions available" in result.output


def test_quiz_flag_bad():
    """Flagging a question as bad stores quality_flag='bad'."""
    from shikhu.cli import app

    ids = _insert_questions("fake.py", n=1)
    result = runner.invoke(app, ["quiz", "--n", "1"], input="a\nb\n")
    assert result.exit_code == 0
    conn = store._get_conn()
    row = conn.execute("SELECT quality_flag FROM questions WHERE id = ?", (ids[0],)).fetchone()
    conn.close()
    assert row["quality_flag"] == "bad"


def test_quiz_revalidates_golden_on_correct():
    """A pending-revalidation golden answered correctly + confirmed is reinstated."""
    from shikhu.cli import app

    ids = _insert_questions("fake.py", n=1)
    conn = store._get_conn()
    conn.execute(
        "UPDATE questions SET golden = TRUE, stale = TRUE, pending_revalidation = TRUE, "
        "answered_at = CURRENT_TIMESTAMP WHERE id = ?",
        (ids[0],),
    )
    conn.commit()
    conn.close()

    # correct answer ("a" -> "A"), then confirm re-validation ("y")
    result = runner.invoke(app, ["quiz", "--n", "1"], input="a\ny\n")
    assert result.exit_code == 0
    conn = store._get_conn()
    row = conn.execute(
        "SELECT stale, golden, pending_revalidation FROM questions WHERE id = ?", (ids[0],)
    ).fetchone()
    conn.close()
    assert (row["stale"], row["golden"], row["pending_revalidation"]) == (0, 1, 0)
