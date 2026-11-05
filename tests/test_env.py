"""Tests for where shikhu finds your OpenRouter key, and init saving it once."""

import os
import stat
from unittest.mock import patch

from conftest import runner

from shikhu.env import KEY_NAME, load_env, save_key, user_env_path


def _clear_key(monkeypatch):
    monkeypatch.delenv(KEY_NAME, raising=False)


def test_saved_key_is_private_and_keeps_other_lines(tmp_path, monkeypatch):
    path = user_env_path()
    path.parent.mkdir(parents=True)
    path.write_text(f"OTHER=1\n{KEY_NAME}=old\n")

    save_key("sk-new")

    assert path.read_text() == f"OTHER=1\n{KEY_NAME}=sk-new\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_shell_beats_repo_env_beats_saved_key(tmp_path, monkeypatch):
    _clear_key(monkeypatch)
    save_key("saved")
    monkeypatch.chdir(tmp_path)
    load_env()
    assert os.environ[KEY_NAME] == "saved"

    _clear_key(monkeypatch)
    (tmp_path / ".env").write_text(f"{KEY_NAME}=repo\n")
    load_env()
    assert os.environ[KEY_NAME] == "repo"

    monkeypatch.setenv(KEY_NAME, "shell")
    load_env()
    assert os.environ[KEY_NAME] == "shell"


def test_init_asks_for_the_key_once_and_saves_it(tmp_path, monkeypatch):
    from shikhu.cli import app

    _clear_key(monkeypatch)
    monkeypatch.chdir(tmp_path)
    with patch("shikhu.commands.init.interactive", return_value=True):
        result = runner.invoke(app, ["init", "--no-skill"], input="sk-or-pasted\n")

    assert result.exit_code == 0
    assert "Saved your key" in result.output
    assert "sk-or-pasted" not in result.output  # typing is hidden
    assert f"{KEY_NAME}=sk-or-pasted" in user_env_path().read_text()
    assert not (tmp_path / ".env").exists()  # never written into the repo


def test_init_enter_skips_the_key(tmp_path, monkeypatch):
    from shikhu.cli import app

    _clear_key(monkeypatch)
    monkeypatch.chdir(tmp_path)
    with patch("shikhu.commands.init.interactive", return_value=True):
        result = runner.invoke(app, ["init", "--no-skill"], input="\n")

    assert result.exit_code == 0
    assert "OPENROUTER_API_KEY not set" in result.output
    assert not user_env_path().exists()


def test_init_does_not_ask_when_a_key_is_set(tmp_path, monkeypatch):
    from shikhu.cli import app

    monkeypatch.chdir(tmp_path)
    with patch("shikhu.commands.init.interactive", return_value=True):
        result = runner.invoke(app, ["init", "--no-skill"])

    assert "Paste it here" not in result.output
    assert "API key found" in result.output
