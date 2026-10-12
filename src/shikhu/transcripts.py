"""Past prompts from this repo's Claude Code transcripts — the input to `shikhu map`.

Stricter than `ingest._iter_user_prompts`, which only drops slash commands: here every kept
prompt is sent to Jev, so harness boilerplate would be paid for and could land on the map.
No regex conceptual filter, though — the Jev benchmark showed one ahead of Jev destroys
recall, and Jev itself decides what is a question about this codebase.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from shikhu.ingest import _project_transcripts_dir

MIN_LENGTH = 15

# Turns the harness writes as the user: task notifications, slash-command echoes, command
# stdout, pasted blocks, interruption markers and the like. Tagged turns all open with "<".
_BOILERPLATE_PREFIXES = (
    "<",
    "[Request interrupted",
    "Tool loaded",
    "Caveat:",
    "This session is being continued from a previous conversation",
)


# The assistant's previous reply rides along when a past prompt is scored, so references
# like "that for loop" resolve. Its tail is what the next prompt usually points at.
CONTEXT_CHARS = 800


@dataclass
class PastPrompt:
    text: str
    session_id: str
    asked_at: str | None
    transcript_path: str
    context: str | None = None  # tail of the assistant's reply just before this prompt


def _prompt_text(content) -> str | None:
    """The user's typed text, or None for tool results and anything without text.

    A turn with an attached image or pasted block arrives as a list of content blocks; its
    text blocks are still the user's own words, so they count."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p) or None


def _is_real_prompt(d: dict, text: str) -> bool:
    if len(text) < MIN_LENGTH or text.startswith(_BOILERPLATE_PREFIXES):
        return False
    # Agent SDK runs (entrypoint sdk-py, sdk-ts, sdk-cli) are scripts prompting Claude —
    # review tools, eval harnesses — not the user typing.
    return not str(d.get("entrypoint", "")).startswith("sdk-")


def iter_past_prompts(transcript_path: Path):
    """Yield a PastPrompt for each real prompt the user typed in one transcript, carrying
    the tail of the assistant reply it followed."""
    reply: list[str] = []
    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Sidechain turns are the main agent talking to a subagent, not the user.
            if d.get("isMeta") or d.get("isSidechain"):
                continue
            content = d.get("message", {}).get("content")
            if d.get("type") == "assistant" and isinstance(content, list):
                reply += [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                continue
            if d.get("type") != "user" or d.get("isCompactSummary"):
                continue
            text = _prompt_text(content)
            if text is None:
                continue  # a tool result: the reply is still going
            text = text.strip()
            if _is_real_prompt(d, text):
                yield PastPrompt(
                    text=text,
                    session_id=d.get("sessionId", ""),
                    asked_at=d.get("timestamp"),
                    transcript_path=str(transcript_path),
                    context="\n".join(t for t in reply if t).strip()[-CONTEXT_CHARS:] or None,
                )
            reply = []


def normalize(text: str) -> str:
    """Key for exact-duplicate removal: case and whitespace don't make a new question."""
    return re.sub(r"\s+", " ", text).strip().lower()


def scan(
    transcripts_dir: Path | None = None, cwd: str | None = None
) -> tuple[list[PastPrompt], int]:
    """Every distinct past prompt for this repo, plus the number of transcripts read.

    Resumed and forked sessions copy earlier turns into new transcript files, so the same
    prompt shows up many times; the earliest copy is kept."""
    if transcripts_dir is None:
        transcripts_dir = _project_transcripts_dir(cwd)
    if not transcripts_dir.exists():
        return [], 0
    paths = sorted(transcripts_dir.glob("*.jsonl"))
    earliest: dict[str, PastPrompt] = {}
    for path in paths:
        for p in iter_past_prompts(path):
            key = normalize(p.text)
            kept = earliest.get(key)
            if kept is None or (p.asked_at or "") < (kept.asked_at or ""):
                earliest[key] = p
    return sorted(earliest.values(), key=lambda p: p.asked_at or ""), len(paths)
