"""Lazy transcript ingester — harvest conceptual user-turn prompts from Claude Code transcripts.

Triggered at the start of consumer commands (quiz, refresh, generate-from-study, study-context).
Idempotent: dedupes on (session_id, message_index) via a UNIQUE index, so re-running is safe and cheap.

Replaces the older UserPromptSubmit-hook approach — same data, but no live hook fragility.
"""

import json
import os
import re
import sqlite3
from pathlib import Path

from shikhu.classifier import classify
from shikhu.store import _get_conn


def _encoded_cwd(cwd: str) -> str:
    """Mirror Claude Code's project-directory encoding: '/', '_', '.' all become '-'."""
    return re.sub(r"[/_.]", "-", cwd)


def _project_transcripts_dir(cwd: str | None = None) -> Path:
    cwd = cwd or os.getcwd()
    return Path.home() / ".claude" / "projects" / _encoded_cwd(cwd)


def _iter_user_prompts(transcript_path: Path):
    """Yield (message_index, session_id, prompt_text) for real user prompts.

    Skips: meta messages, tool_result entries, slash-command invocations, malformed lines.
    """
    with open(transcript_path) as f:
        for i, line in enumerate(f):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "user" or d.get("isMeta"):
                continue
            content = d.get("message", {}).get("content")
            if not isinstance(content, str):
                continue  # tool_result entries have list content
            text = content.strip()
            if not text or text.startswith("<command-message>"):
                continue
            yield i, d.get("sessionId", ""), text


def ingest_recent(transcripts_dir: Path | None = None, cwd: str | None = None) -> dict:
    """Walk all transcripts for the current project and insert conceptual prompts.

    Idempotent. Returns counts: {'transcripts', 'inserted', 'skipped_non_conceptual', 'skipped_duplicate'}.

    Pass `transcripts_dir` to override auto-discovery (used by tests).
    """
    if transcripts_dir is None:
        transcripts_dir = _project_transcripts_dir(cwd)
    stats = {"transcripts": 0, "inserted": 0, "skipped_non_conceptual": 0, "skipped_duplicate": 0}
    if not transcripts_dir.exists():
        return stats

    conn = _get_conn()
    try:
        for path in transcripts_dir.glob("*.jsonl"):
            stats["transcripts"] += 1
            for msg_idx, session_id, text in _iter_user_prompts(path):
                if classify(text) != "conceptual":
                    stats["skipped_non_conceptual"] += 1
                    continue
                try:
                    cur = conn.execute(
                        "INSERT INTO raw_prompts "
                        "(prompt_text, session_id, message_index, transcript_path, is_conceptual) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (text, session_id, msg_idx, str(path), True),
                    )
                    if cur.rowcount > 0:
                        stats["inserted"] += 1
                except sqlite3.IntegrityError:
                    stats["skipped_duplicate"] += 1
        conn.commit()
    finally:
        conn.close()
    return stats
