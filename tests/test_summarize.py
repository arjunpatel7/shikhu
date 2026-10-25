"""Tests for shikhu summarize."""

from unittest.mock import patch

from conftest import runner

import shikhu.store as store
from shikhu.cli import app


def test_summarize_single_file_populates_db(tmp_path):
    """--file path calls the generator, stores the result, and shows 'generated' in the output."""
    target = tmp_path / "hello.py"
    target.write_text("def hello():\n    return 'world'\n")

    with patch(
        "shikhu.commands.summarize.generate_summary",
        return_value=("Prose summary of hello.py.", {}),
    ):
        result = runner.invoke(app, ["summarize", "--file", str(target)])

    assert result.exit_code == 0, result.output
    assert "1 generated" in result.output

    row = store.get_summary(str(target))
    assert row is not None
    assert row["summary_text"] == "Prose summary of hello.py."
    assert row["content_hash"] is not None


def test_summarize_skips_fresh(tmp_path, monkeypatch):
    """When a summary already matches the current content_hash, summarize does not regenerate."""
    target = tmp_path / "cached.py"
    target.write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)

    from shikhu.generator import PROMPT_VERSION
    from shikhu.staleness import compute_file_hash

    store.upsert_summary(
        str(target), compute_file_hash(str(target)), "cached summary", prompt_version=PROMPT_VERSION
    )

    # Bulk mode should skip this file (fresh) and not call the generator.
    call_count = {"n": 0}

    def _fake(_):
        call_count["n"] += 1
        return ("new summary", {})

    with (
        patch("shikhu.commands.summarize.get_trackable_files", return_value=[str(target)]),
        patch("shikhu.commands.summarize.generate_summary", side_effect=_fake),
    ):
        result = runner.invoke(app, ["summarize"])

    assert result.exit_code == 0, result.output
    assert call_count["n"] == 0
    assert "already fresh" in result.output


def test_summarize_missing_file_suggests_basename_match(tmp_path):
    """--file X where X doesn't exist but its basename matches a tracked file prints 'Did you mean: <path>?'."""
    tracked = "src/shikhu/classifier.py"

    with patch("shikhu.commands.summarize.get_trackable_files", return_value=[tracked]):
        result = runner.invoke(app, ["summarize", "--file", "classifier.py"])

    assert result.exit_code == 1
    assert "File not found" in result.output
    assert "Did you mean" in result.output
    assert tracked in result.output
