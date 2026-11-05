"""When no trackable files are found, the message says why and how to fix it."""

import subprocess

from conftest import runner

from shikhu.cli import app
from shikhu.commands.utils import no_trackable_files_hint


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_outside_a_git_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "isn't a git repository" in no_trackable_files_hint()


def test_files_not_added_yet(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    hint = no_trackable_files_hint()
    assert "1 file(s) like a.py exist but aren't committed or staged" in hint
    assert "git add" in hint


def test_everything_excluded_by_quizignore(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / ".quizignore").write_text("*.py\n")
    _git(tmp_path, "add", "a.py")
    monkeypatch.chdir(tmp_path)
    assert "excludes all of them" in no_trackable_files_hint()


def test_no_files_with_these_extensions(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    (tmp_path / "main.go").write_text("package main\n")
    _git(tmp_path, "add", "main.go")
    monkeypatch.chdir(tmp_path)
    hint = no_trackable_files_hint(".py,.js")
    assert "no code yet in .py, .js" in hint
    assert "--extensions" in hint


def test_coverage_prints_the_hint(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["coverage"])
    assert "aren't committed or staged" in result.output


def test_shikhus_own_skill_files_dont_count_as_your_code(tmp_path, monkeypatch):
    """A brand-new repo right after `shikhu init`: only the skills it installed are on disk."""
    _git(tmp_path, "init", "-q")
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    hint = no_trackable_files_hint(".py,.md")
    assert "SKILL.md" not in hint
    assert "no code yet" in hint

    (tmp_path / "main.py").write_text("print('hi')\n")
    assert "like main.py exist" in no_trackable_files_hint(".py,.md")


def test_empty_files_are_not_tracked_and_the_hint_says_so(tmp_path, monkeypatch):
    from shikhu.commands.utils import get_trackable_files

    _git(tmp_path, "init", "-q")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("\n")
    _git(tmp_path, "add", ".")
    monkeypatch.chdir(tmp_path)
    assert get_trackable_files(".py") == []
    assert "are empty" in no_trackable_files_hint(".py")

    (tmp_path / "pkg" / "core.py").write_text("x = 1\n")
    _git(tmp_path, "add", ".")
    assert get_trackable_files(".py") == ["pkg/core.py"]
