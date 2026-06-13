"""Attribution: given a user prompt in a transcript, find which tracked file it's about.

Temporal locality: look at the last K tool calls before the prompt. Recent files are strong
signals. Explicit basename mentions in the prompt text itself get a boost.

algo v1.0.
"""

import json
from dataclasses import dataclass
from pathlib import Path

ALGO_VERSION = "v1.0"

# Tool name → input field containing a file path
_TOOL_PATH_FIELDS = {
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "NotebookEdit": "notebook_path",
    "Grep": "path",
    "Glob": "pattern",
}

PROMPT_MENTION_BOOST = 2.0
DEFAULT_THRESHOLD = 0.3


@dataclass
class AttributionResult:
    attributed_file: str | None
    score: float
    runner_up_file: str | None
    runner_up_score: float
    signals: dict
    algo_version: str = ALGO_VERSION


def _extract_tool_files(entry: dict) -> list[str]:
    """Pull file paths from any tool_use entries inside an assistant message."""
    if entry.get("type") != "assistant":
        return []
    content = entry.get("message", {}).get("content")
    if not isinstance(content, list):
        return []
    out = []
    for c in content:
        if c.get("type") != "tool_use":
            continue
        path_field = _TOOL_PATH_FIELDS.get(c.get("name"))
        if not path_field:
            continue
        path = c.get("input", {}).get(path_field)
        if path:
            out.append(path)
    return out


def _normalize(file_path: str) -> str:
    p = file_path
    if p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def _match_tracked(candidate: str, tracked: list[str]) -> str | None:
    """Map a tool-call path to a tracked file. Exact normalized match preferred; basename fallback."""
    cand = _normalize(candidate)
    norm_tracked = {_normalize(t): t for t in tracked}
    if cand in norm_tracked:
        return norm_tracked[cand]
    # absolute path containing the tracked path
    for k, v in norm_tracked.items():
        if cand.endswith("/" + k) or cand.endswith(k):
            return v
    # basename fallback
    base = cand.rsplit("/", 1)[-1]
    for k, v in norm_tracked.items():
        if k.rsplit("/", 1)[-1] == base:
            return v
    return None


def attribute_to_file(
    transcript_path: Path,
    turn_index: int,
    tracked_files: list[str],
    k: int = 5,
    threshold: float = DEFAULT_THRESHOLD,
) -> AttributionResult:
    """Attribute the user prompt at turn_index to a tracked file.

    Walks back at most k assistant messages, scores files by 1/distance, sums repeated touches.
    Adds PROMPT_MENTION_BOOST if the prompt text mentions a tracked basename.
    Returns attributed_file=None if no file clears `threshold`.
    """
    with open(transcript_path) as f:
        entries = [json.loads(line) for line in f if line.strip()]

    prompt_text = ""
    if 0 <= turn_index < len(entries):
        msg = entries[turn_index].get("message", {})
        content = msg.get("content")
        if isinstance(content, str):
            prompt_text = content

    scores: dict[str, float] = {}
    tool_calls_seen = 0
    distance = 0
    for idx in range(turn_index - 1, -1, -1):
        if entries[idx].get("type") != "assistant":
            continue
        distance += 1
        if distance > k:
            break
        for f_path in _extract_tool_files(entries[idx]):
            tool_calls_seen += 1
            match = _match_tracked(f_path, tracked_files)
            if match is None:
                continue
            scores[match] = scores.get(match, 0.0) + (1.0 / distance)

    mention_hit = False
    if prompt_text:
        for t in tracked_files:
            base = _normalize(t).rsplit("/", 1)[-1]
            if base and base in prompt_text:
                scores[t] = scores.get(t, 0.0) + PROMPT_MENTION_BOOST
                mention_hit = True

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[0] if ranked else (None, 0.0)
    runner = ranked[1] if len(ranked) > 1 else (None, 0.0)

    return AttributionResult(
        attributed_file=top[0] if top[1] >= threshold else None,
        score=top[1],
        runner_up_file=runner[0],
        runner_up_score=runner[1],
        signals={
            "window_size": k,
            "tool_calls_seen": tool_calls_seen,
            "prompt_mention_hit": mention_hit,
        },
    )
