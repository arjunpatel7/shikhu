"""Tests for golden questions and related coverage."""

from unittest.mock import patch

from conftest import _insert_questions, runner

import shikhu.store as store


def test_quiz_golden_on_correct_and_representative():
    """Answering correctly + flagging representative marks question as golden."""
    from shikhu.cli import app

    ids = _insert_questions("fake.py", n=1)
    result = runner.invoke(app, ["quiz", "--n", "1"], input="a\ng\ny\n")
    assert result.exit_code == 0
    conn = store._get_conn()
    row = conn.execute("SELECT golden FROM questions WHERE id = ?", (ids[0],)).fetchone()
    conn.close()
    assert row["golden"] == 1


def test_quiz_not_golden_on_wrong_answer():
    """Wrong answer cannot produce a golden question, even if flagged good."""
    from shikhu.cli import app

    ids = _insert_questions("fake.py", n=1)
    result = runner.invoke(app, ["quiz", "--n", "1"], input="b\ng\n")
    assert result.exit_code == 0
    conn = store._get_conn()
    row = conn.execute("SELECT golden FROM questions WHERE id = ?", (ids[0],)).fetchone()
    conn.close()
    assert row["golden"] == 0


def test_golden_coverage_count():
    """Coverage should report golden counts per file."""
    from shikhu.cli import app

    ids = _insert_questions("myfile.py", n=3)
    store.grade_question(ids[0], user_answer="A", correct=True)
    store.grade_question(ids[1], user_answer="A", correct=True)
    store.grade_question(ids[2], user_answer="B", correct=False)
    conn = store._get_conn()
    conn.execute("UPDATE questions SET golden = TRUE WHERE id IN (?, ?)", (ids[0], ids[1]))
    conn.commit()
    conn.close()
    with patch("shikhu.commands.coverage.get_trackable_files", return_value=["myfile.py"]):
        result = runner.invoke(app, ["coverage"])
    assert result.exit_code == 0
    assert "2/3" in result.output
