"""Tests for pinning a question to the commit it is still reproducible at.

A pin is only useful if it is true: the file at that commit must be what the question was
written about. So most of these are about the cases that must NOT pin — an unverifiable pin
silently sends a benchmark run to a tree the question was never about.
"""

import subprocess

import pytest

import shikhu.store as store
from shikhu.staleness import compute_file_hash, hash_content, pin_commit


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "b.py").write_text("x = 1\n")
    (tmp_path / "notes.md").write_text("hello\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _head(repo):
    return _git(repo, "rev-parse", "HEAD")


def test_pins_head_when_file_is_unchanged(repo):
    assert pin_commit("a.py", compute_file_hash("a.py")) == _head(repo)


def test_pins_files_in_subdirectories_and_non_python(repo):
    assert pin_commit("pkg/b.py", compute_file_hash("pkg/b.py")) == _head(repo)
    assert pin_commit("notes.md", compute_file_hash("notes.md")) == _head(repo)


def test_no_pin_when_working_copy_was_edited_since_head(repo):
    """Questions written about an uncommitted edit don't exist at HEAD — pinning HEAD would
    send a clean-clone run to code the question was never about."""
    (repo / "a.py").write_text("def f():\n    return 2\n")
    assert pin_commit("a.py", compute_file_hash("a.py")) is None


def test_no_pin_for_untracked_file(repo):
    (repo / "new.py").write_text("y = 1\n")
    assert pin_commit("new.py", compute_file_hash("new.py")) is None


def test_no_pin_without_a_content_hash(repo):
    """Nothing to verify against, so nothing to claim."""
    assert pin_commit("a.py", None) is None
    assert pin_commit("a.py", "") is None


def test_no_pin_outside_a_git_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    assert pin_commit("a.py", compute_file_hash("a.py")) is None


def test_no_pin_in_a_repo_with_no_commits(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    assert pin_commit("a.py", compute_file_hash("a.py")) is None


def test_python_comment_only_edit_still_pins(repo):
    """Staleness hashes Python over its AST, so the pin deliberately agrees with it: a comment
    edit doesn't make the code a different tree, and the question stays reproducible."""
    (repo / "a.py").write_text("# a new comment\ndef f():\n    return 1\n")
    assert pin_commit("a.py", compute_file_hash("a.py")) == _head(repo)


def test_pin_follows_the_commit_that_actually_matches(repo):
    """After the file changes and is committed, the new commit is the pin, not the old one."""
    first = _head(repo)
    (repo / "a.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-q", "-am", "change")
    assert pin_commit("a.py", compute_file_hash("a.py")) == _head(repo) != first


def test_paths_are_relative_to_cwd_like_git_ls_files(repo, monkeypatch):
    """get_trackable_files yields cwd-relative paths, so the pin has to resolve them the same
    way when shikhu is run from a subdirectory."""
    monkeypatch.chdir(repo / "pkg")
    assert pin_commit("b.py", compute_file_hash("b.py")) == _head(repo)


def test_hash_content_matches_compute_file_hash(repo):
    """The pin compares git's copy against a stored hash, so both sides must hash the same."""
    for path in ("a.py", "pkg/b.py", "notes.md"):
        assert hash_content(path, (repo / path).read_bytes()) == compute_file_hash(path)


# --- storage ---


def test_insert_questions_records_the_pin():
    ids = store.insert_questions(
        "a.py",
        [{"question_text": "q", "choices": ["A", "B", "C", "D"], "expected_answer": "A"}],
        commit_sha="abc123",
    )
    conn = store._get_conn()
    row = conn.execute("SELECT commit_sha FROM questions WHERE id = ?", (ids[0],)).fetchone()
    conn.close()
    assert row["commit_sha"] == "abc123"


def test_commit_sha_defaults_to_null_not_head():
    """Never filled in from HEAD blindly — an unpinned question stays visibly unpinned."""
    ids = store.insert_questions(
        "a.py",
        [{"question_text": "q", "choices": ["A", "B", "C", "D"], "expected_answer": "A"}],
    )
    conn = store._get_conn()
    row = conn.execute("SELECT commit_sha FROM questions WHERE id = ?", (ids[0],)).fetchone()
    conn.close()
    assert row["commit_sha"] is None


def test_migration_adds_the_column_to_an_existing_db(tmp_path):
    """Databases created before the pin existed must upgrade in place."""
    import sqlite3

    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY, file_path TEXT)")
    conn.commit()
    conn.close()

    store.DB_PATH = str(old)
    store.init_db()

    conn = sqlite3.connect(old)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    conn.close()
    assert "commit_sha" in cols
