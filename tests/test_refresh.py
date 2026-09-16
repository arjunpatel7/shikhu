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
    """A missing OPENROUTER_API_KEY fails once with instructions, not per file."""
    from shikhu.cli import app

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with patch("shikhu.commands.refresh.load_dotenv"):
        result = runner.invoke(app, ["refresh"])
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY" in result.output


def _fake_quiz():
    from shikhu.generator import MCQuestion, Quiz

    q = MCQuestion(question="Why?", choices=["a", "b", "c", "d"], correct_index=0)
    return Quiz(questions=[q, q, q]), {"model": "test-model"}


def test_refresh_generated_questions_go_stale_after_edit(tmp_path):
    """End to end through refresh (no hand-seeded hash): generate, edit the file, refresh again."""
    from shikhu.cli import app
    from shikhu.staleness import compute_file_hash
    from shikhu.store import _get_conn

    f = tmp_path / "code.py"
    f.write_text("x = 1\n")
    path = str(f)

    def _refresh():
        with (
            patch("shikhu.commands.refresh.ingest_recent"),
            patch("shikhu.commands.refresh.get_trackable_files", return_value=[path]),
            patch("shikhu.commands.refresh._summarize_one", return_value=(path, "fresh", None)),
            patch("shikhu.generator.generate_question_from_file", return_value=_fake_quiz()),
        ):
            result = runner.invoke(app, ["refresh"])
        assert result.exit_code == 0, result.output

    _refresh()
    conn = _get_conn()
    first_ids = [r["id"] for r in conn.execute("SELECT id FROM questions").fetchall()]
    baseline = conn.execute("SELECT content_hash FROM files WHERE filepath = ?", (path,)).fetchone()
    conn.close()
    assert len(first_ids) == 3
    assert baseline["content_hash"] == compute_file_hash(path)

    f.write_text("x = 2\n")
    _refresh()

    conn = _get_conn()
    rows = {r["id"]: r["stale"] for r in conn.execute("SELECT id, stale FROM questions")}
    conn.close()
    assert all(rows[i] == 1 for i in first_ids)  # old batch staled by the edit
    assert [s for i, s in rows.items() if i not in first_ids] == [0, 0, 0]  # fresh batch
