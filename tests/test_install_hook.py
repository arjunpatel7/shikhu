"""Tests for the push gate: installing the hook, and what it does on a push."""

import os
import stat
import subprocess
from unittest.mock import patch

from conftest import runner

from shikhu.commands.install_hook import HOOK_MARKER, render_hook


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path, monkeypatch):
    """A repo with main and a branch off it, as cwd."""
    _git("init", "-q", "-b", "main", cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)
    _git("checkout", "-qb", "feature", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _install(*args):
    from shikhu.cli import app

    return runner.invoke(app, ["install-hook", *args])


def test_hook_calls_this_shikhu_by_full_path():
    with patch("shikhu.commands.install_hook.shutil.which", return_value="/opt/my bin/shikhu"):
        assert "run_shikhu() { '/opt/my bin/shikhu' \"$@\"; }" in render_hook()


def test_installs_a_fresh_hook_and_saves_the_mode(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    result = _install("--mode", "block")

    hook = repo / ".git" / "hooks" / "pre-push"
    assert result.exit_code == 0, result.output
    assert HOOK_MARKER in hook.read_text()
    assert hook.stat().st_mode & stat.S_IXUSR
    mode = subprocess.run(["git", "config", "shikhu.gate.mode"], capture_output=True, text=True)
    assert mode.stdout.strip() == "block"


def test_never_replaces_another_tools_hook(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    theirs = repo / ".git" / "hooks" / "pre-push"
    theirs.write_text("#!/bin/sh\necho entire\n")

    result = _install("--mode", "warn")

    assert theirs.read_text() == "#!/bin/sh\necho entire\n"
    assert HOOK_MARKER in (repo / ".git" / "hooks" / "shikhu-pre-push").read_text()
    assert "Add this line" in result.output


def test_reinstalling_updates_our_own_hook(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _install("--mode", "warn")
    _install("--mode", "block")
    assert not (repo / ".git" / "hooks" / "shikhu-pre-push").exists()


def test_a_hooks_path_manager_gets_the_side_file(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _git("config", "core.hooksPath", ".husky/_", cwd=repo)
    result = _install("--mode", "warn")
    assert (repo / ".git" / "hooks" / "shikhu-pre-push").exists()
    assert "Add this line" in result.output


def test_outside_a_repo_says_so(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _install("--mode", "warn")
    assert result.exit_code == 1
    assert "Not a git repository" in result.output


def test_init_offers_the_hook_and_skip_installs_nothing(tmp_path, monkeypatch):
    from shikhu.cli import app

    repo = _repo(tmp_path, monkeypatch)
    with patch("shikhu.commands.init.interactive", return_value=True):
        result = runner.invoke(app, ["init", "--no-skill"], input="s\n")
    assert result.exit_code == 0, result.output
    assert "before each git push" in result.output
    assert not (repo / ".git" / "hooks" / "pre-push").exists()


def _push_with_gate(repo, mode, check_exit):
    """Run the installed hook as git would, with a fake shikhu whose check exits `check_exit`."""
    fake = repo / "fake-shikhu"
    fake.write_text(
        "#!/bin/sh\n"
        'case "$*" in *--help*|*--version*) exit 0 ;; esac\n'
        f'echo "checked: $*"; exit {check_exit}\n'
    )
    fake.chmod(0o755)
    _git("config", "shikhu.gate.mode", mode, cwd=repo)
    env = {**os.environ, "SHIKHU_CMD": str(fake)}
    return subprocess.run(
        [str(repo / ".git" / "hooks" / "pre-push"), "origin", "url"],
        cwd=repo,
        input="refs/heads/feature abc123 refs/heads/feature 000\n",
        capture_output=True,
        text=True,
        env=env,
    )


def test_warn_pushes_anyway_and_tells_an_agent_to_ask_for_a_quiz(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _install("--mode", "warn")
    result = _push_with_gate(repo, "warn", check_exit=1)
    assert result.returncode == 0
    assert "checked: coverage --since main --check --min 1" in result.stderr
    assert "pushed anyway" in result.stderr
    assert "AI coding agents" in result.stderr
    assert "shikhu quiz --since main" in result.stderr


def test_block_stops_the_push_until_quizzed(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _install("--mode", "block")
    blocked = _push_with_gate(repo, "block", check_exit=1)
    assert blocked.returncode == 1
    assert "push blocked" in blocked.stderr
    assert "don't skip this check" in blocked.stderr

    assert _push_with_gate(repo, "block", check_exit=0).returncode == 0


def test_block_fails_closed_when_shikhu_crashes(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _install("--mode", "block")
    assert _push_with_gate(repo, "block", check_exit=2).returncode == 1
