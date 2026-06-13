"""Tests for shikhu init command."""

import os
from unittest.mock import patch

from conftest import runner

import shikhu.store as store


def test_init_creates_db():
    """shikhu init creates all expected tables."""
    from shikhu.cli import app

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    conn = store._get_conn()
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    expected = {
        "files",
        "questions",
        "raw_prompts",
        "file_summaries",
        "reviews",
        "review_questions",
    }
    assert expected.issubset(tables)


def test_init_idempotent():
    """Running shikhu init twice does not crash."""
    from shikhu.cli import app

    result1 = runner.invoke(app, ["init"])
    result2 = runner.invoke(app, ["init"])
    assert result1.exit_code == 0
    assert result2.exit_code == 0


def test_init_gitignores_coverage_db(tmp_path, monkeypatch):
    """init adds coverage.db to .gitignore (it holds captured prompts — never commit it)."""
    from shikhu.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("*.pyc\n")

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    content = (tmp_path / ".gitignore").read_text()
    assert "coverage.db" in content
    assert "*.pyc" in content  # existing entries preserved

    # idempotent: a second init doesn't duplicate the entry
    runner.invoke(app, ["init"])
    assert (tmp_path / ".gitignore").read_text().count("coverage.db") == 1


def test_init_warns_missing_api_key():
    """shikhu init warns when INCEPTION_API_KEY is not set."""
    from shikhu.cli import app

    with patch("shikhu.commands.init.load_dotenv"), patch.dict(os.environ, {}, clear=True):
        result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "INCEPTION_API_KEY" in result.output
