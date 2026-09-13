"""Tests for the PR-slice helpers: changed_files, --since, and coverage --check/--min."""

import subprocess
from unittest.mock import patch

import pytest
from conftest import _insert_questions, runner

import shikhu.store as store
from shikhu.cli import app


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """main has keep.py, edit.py, gone.py; branch `feature` is checked out."""
    _git(tmp_path, "init", "-q", "-b", "main")
    for name in ("keep.py", "edit.py", "gone.py", "wip.py"):
        (tmp_path / name).write_text("x = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_changed_files_committed_uncommitted_and_deleted(repo):
    from shikhu.commands.utils import changed_files

    (repo / "edit.py").write_text("x = 2\n")
    (repo / "new.py").write_text("y = 1\n")
    _git(repo, "add", "edit.py", "new.py")
    _git(repo, "rm", "-q", "gone.py")
    _git(repo, "commit", "-q", "-m", "feature work")
    (repo / "wip.py").write_text("x = 3\n")  # uncommitted edit
    (repo / "untracked.py").write_text("z = 1\n")  # never added

    assert sorted(changed_files("main")) == ["edit.py", "new.py", "wip.py"]


def test_changed_files_ignores_commits_on_base_after_branching(repo):
    from shikhu.commands.utils import changed_files

    _git(repo, "checkout", "-q", "main")
    (repo / "keep.py").write_text("x = 9\n")
    _git(repo, "commit", "-q", "-am", "main moved on")
    _git(repo, "checkout", "-q", "feature")

    assert changed_files("main") == []


def test_changed_files_bad_ref_exits(repo):
    result = runner.invoke(app, ["coverage", "--since", "no-such-branch"])
    assert result.exit_code == 2
    assert "Unknown git ref" in result.output


def test_scoped_queries():
    _insert_questions("a.py", n=2)
    _insert_questions("b.py", n=2)
    _insert_questions("c.py", n=2)

    assert store.get_unasked_questions(limit=10, file_paths=[]) == []
    two = store.get_unasked_questions(limit=10, file_paths=["a.py", "b.py"])
    assert sorted(q["file_path"] for q in two) == ["a.py", "b.py"]  # one per file
    assert len(store.get_unasked_questions(limit=10, file_paths=["a.py"])) == 2


def test_revalidation_scoped():
    ids_a = _insert_questions("a.py", n=1)
    ids_b = _insert_questions("b.py", n=1)
    conn = store._get_conn()
    conn.execute(
        "UPDATE questions SET golden = TRUE, stale = TRUE, pending_revalidation = TRUE "
        "WHERE id IN (?, ?)",
        (ids_a[0], ids_b[0]),
    )
    conn.commit()
    conn.close()

    rows = store.get_revalidation_questions(file_paths=["b.py"])
    assert [r["id"] for r in rows] == ids_b


def _make_golden(file_path):
    [qid] = _insert_questions(file_path, n=1)
    conn = store._get_conn()
    conn.execute("UPDATE questions SET golden = TRUE WHERE id = ?", (qid,))
    conn.commit()
    conn.close()


def test_coverage_check_fails_then_passes():
    """--check defaults to a bar of 1 golden and exits 1 until every changed file meets it."""
    with patch("shikhu.commands.coverage.changed_files", return_value=["a.py", "b.py"]):
        _make_golden("a.py")
        result = runner.invoke(app, ["coverage", "--since", "main", "--check"])
        assert result.exit_code == 1
        assert "1 file(s) below 1" in result.output

        _make_golden("b.py")
        result = runner.invoke(app, ["coverage", "--since", "main", "--check"])
        assert result.exit_code == 0, result.output


def test_coverage_check_respects_min():
    _make_golden("a.py")
    with patch("shikhu.commands.coverage.get_trackable_files", return_value=["a.py"]):
        assert runner.invoke(app, ["coverage", "--check"]).exit_code == 0
        assert runner.invoke(app, ["coverage", "--check", "--min", "2"]).exit_code == 1


def test_coverage_since_no_changes_passes_check():
    with patch("shikhu.commands.coverage.changed_files", return_value=[]):
        result = runner.invoke(app, ["coverage", "--since", "main", "--check"])
    assert result.exit_code == 0
    assert "No changed trackable files" in result.output


def test_quiz_since_only_asks_changed_files():
    _insert_questions("a.py", n=1)
    _insert_questions("b.py", n=1)
    with patch("shikhu.commands.quiz.changed_files", return_value=["b.py"]):
        result = runner.invoke(app, ["quiz", "--since", "main", "--n", "5"], input="a\ns\n")
    assert result.exit_code == 0, result.output
    assert "b.py" in result.output
    assert "a.py" not in result.output


def test_quiz_since_empty_suggests_scoped_refresh():
    _insert_questions("a.py", n=1)
    with patch("shikhu.commands.quiz.changed_files", return_value=["b.py"]):
        result = runner.invoke(app, ["quiz", "--since", "main"])
    assert result.exit_code == 0
    assert "shikhu refresh --since main" in " ".join(result.output.split())


def test_quiz_file_and_since_conflict():
    result = runner.invoke(app, ["quiz", "--file", "a.py", "--since", "main"])
    assert result.exit_code == 2


def test_quiz_runs_staleness_first(tmp_path):
    """An edit since the last refresh stales goldens at quiz time, so they come up for re-validation."""
    from shikhu.staleness import compute_file_hash

    f = tmp_path / "code.py"
    f.write_text("x = 1\n")
    q = [{"question_text": "Q", "choices": ["A", "B", "C", "D"], "expected_answer": "A"}]
    [qid] = store.insert_questions(str(f), q, content_hash=compute_file_hash(str(f)))
    conn = store._get_conn()
    conn.execute("UPDATE questions SET golden = TRUE WHERE id = ?", (qid,))
    conn.commit()
    conn.close()

    f.write_text("x = 2\n")
    result = runner.invoke(app, ["quiz", "--n", "1"], input="a\ny\n")
    assert result.exit_code == 0, result.output
    assert "Golden re-validated" in result.output


def test_refresh_since_limits_generation(tmp_path):
    from shikhu.generator import MCQuestion, Quiz

    changed, other = str(tmp_path / "changed.py"), str(tmp_path / "other.py")
    for p in (changed, other):
        open(p, "w").write("x = 1\n")
    q = MCQuestion(question="Why?", choices=["a", "b", "c", "d"], correct_index=0)

    with (
        patch("shikhu.commands.refresh.ingest_recent"),
        patch("shikhu.commands.refresh.get_trackable_files", return_value=[changed, other]),
        patch("shikhu.commands.refresh.changed_files", return_value=[changed]),
        patch(
            "shikhu.commands.refresh._summarize_one", side_effect=lambda p, f: (p, "fresh", None)
        ) as summ,
        patch(
            "shikhu.generator.generate_question_from_file",
            return_value=(Quiz(questions=[q]), {"model": "m"}),
        ) as gen,
    ):
        result = runner.invoke(app, ["refresh", "--since", "main"])

    assert result.exit_code == 0, result.output
    assert [c.args[0] for c in gen.call_args_list] == [changed]
    assert [c.args[0] for c in summ.call_args_list] == [changed]
