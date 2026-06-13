"""Tests for shikhu refresh command."""

from unittest.mock import patch

from conftest import runner


def test_refresh_runs_staleness_and_reports():
    """shikhu refresh runs staleness check and prints a summary."""
    from shikhu.cli import app

    with (
        patch("shikhu.commands.refresh.mark_stale_questions", return_value=3) as mock_stale,
        patch("shikhu.commands.refresh.get_trackable_files", return_value=[]),
    ):
        result = runner.invoke(app, ["refresh"])
    assert result.exit_code == 0
    mock_stale.assert_called_once()
    assert "3" in result.output


def test_refresh_summary_phase_includes_md():
    """The summary phase must track .md (like `shikhu summarize`), or refresh's
    orphan pruning would delete every doc summary that summarize created."""
    from shikhu.cli import app

    with (
        patch("shikhu.commands.refresh.mark_stale_questions", return_value=0),
        patch("shikhu.commands.refresh.get_trackable_files", return_value=[]) as mock_files,
    ):
        result = runner.invoke(app, ["refresh"])
    assert result.exit_code == 0
    summary_phase_extensions = mock_files.call_args_list[0].args[0]
    assert ".md" in summary_phase_extensions


def test_refresh_requires_api_key(monkeypatch):
    """A missing INCEPTION_API_KEY fails once with instructions, not per file."""
    from shikhu.cli import app

    monkeypatch.delenv("INCEPTION_API_KEY", raising=False)
    with patch("shikhu.commands.refresh.load_dotenv"):
        result = runner.invoke(app, ["refresh"])
    assert result.exit_code == 1
    assert "INCEPTION_API_KEY" in result.output
