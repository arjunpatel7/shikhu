"""Tests for shikhu coverage command and .quizignore."""

from unittest.mock import patch

from conftest import _insert_questions, runner

import shikhu.store as store


def test_coverage_shows_files():
    """shikhu coverage lists files with correct answer counts."""
    from shikhu.cli import app

    ids = _insert_questions("myfile.py", n=2)
    store.grade_question(ids[0], user_answer="A", correct=True)
    store.grade_question(ids[1], user_answer="A", correct=True)
    with patch("shikhu.commands.coverage.get_trackable_files", return_value=["myfile.py"]):
        result = runner.invoke(app, ["coverage"])
    assert result.exit_code == 0
    assert "myfile.py" in result.output


def test_coverage_excludes_stale():
    """Stale golden questions do not count toward coverage."""
    from shikhu.cli import app

    ids = _insert_questions("myfile.py", n=2)
    store.grade_question(ids[0], user_answer="A", correct=True)
    store.grade_question(ids[1], user_answer="A", correct=True)
    conn = store._get_conn()
    conn.execute("UPDATE questions SET golden = TRUE WHERE id IN (?, ?)", (ids[0], ids[1]))
    conn.execute("UPDATE questions SET stale = TRUE WHERE id = ?", (ids[0],))
    conn.commit()
    conn.close()
    with patch("shikhu.commands.coverage.get_trackable_files", return_value=["myfile.py"]):
        result = runner.invoke(app, ["coverage"])
    assert result.exit_code == 0
    assert "1/3" in result.output


def test_quizignore_respected(tmp_path):
    """Files matching .quizignore patterns are excluded from trackable list."""
    from shikhu.commands.utils import get_trackable_files as _get_trackable_files

    quizignore = tmp_path / ".quizignore"
    quizignore.write_text("secret.py\n*.lock\n")
    fake_files = "app.py\nsecret.py\npackage.lock\nutils.py"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = fake_files
        files = _get_trackable_files(
            extensions=".py,.lock",
            quizignore_path=quizignore,
        )
    assert "app.py" in files
    assert "utils.py" in files
    assert "secret.py" not in files
    assert "package.lock" not in files
