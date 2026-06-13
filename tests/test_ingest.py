"""Tests for the lazy transcript ingester."""

import json

from shikhu.ingest import ingest_recent


def _write_transcript(path, entries):
    """Write a list of dicts as JSONL."""
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _user_string(session_id, text, is_meta=False):
    return {
        "type": "user",
        "isMeta": is_meta,
        "sessionId": session_id,
        "message": {"role": "user", "content": text},
    }


def _user_tool_result(session_id):
    return {
        "type": "user",
        "sessionId": session_id,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "x", "content": "..."}],
        },
    }


def test_ingester_picks_conceptual_prompts_only(tmp_path):
    """Real user prompts get classified; non-conceptual ones are dropped."""
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    _write_transcript(
        transcripts / "s1.jsonl",
        [
            _user_string("s1", "<command-message>study</command-message>"),  # slash command, skip
            _user_string("s1", "why does refresh re-hash every file?"),  # conceptual ✓
            _user_string("s1", "run the tests"),  # not conceptual
            _user_tool_result("s1"),  # tool result, skip
            _user_string("s1", "how does the staleness logic work?"),  # conceptual ✓
        ],
    )

    stats = ingest_recent(transcripts_dir=transcripts)

    assert stats["transcripts"] == 1
    assert stats["inserted"] == 2
    assert stats["skipped_non_conceptual"] >= 1  # at least "run the tests" got classified out


def test_ingester_is_idempotent(tmp_path):
    """Re-running over the same transcript inserts nothing the second time."""
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    _write_transcript(
        transcripts / "s1.jsonl",
        [
            _user_string("s1", "why does refresh re-hash every file?"),
        ],
    )

    first = ingest_recent(transcripts_dir=transcripts)
    second = ingest_recent(transcripts_dir=transcripts)

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["skipped_duplicate"] == 1


def test_ingester_handles_missing_directory(tmp_path):
    """No transcripts dir = clean no-op, no errors."""
    missing = tmp_path / "does-not-exist"
    stats = ingest_recent(transcripts_dir=missing)
    assert stats["transcripts"] == 0
    assert stats["inserted"] == 0
