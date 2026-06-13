"""Tests for the temporal-locality attribution algorithm."""

import json

from shikhu.attribution import attribute_to_file


def _write(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def _assistant_tool(tool_name, **inputs):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": tool_name, "input": inputs}],
        },
    }


def test_attribute_to_recent_tool_call(tmp_path):
    """A Read right before the prompt attributes the prompt to that file."""
    t = tmp_path / "t.jsonl"
    _write(
        t,
        [
            _assistant_tool("Read", file_path="src/shikhu/refresh.py"),
            _user("why does this re-hash every file?"),
        ],
    )
    r = attribute_to_file(
        t, turn_index=1, tracked_files=["src/shikhu/refresh.py", "src/shikhu/store.py"]
    )
    assert r.attributed_file == "src/shikhu/refresh.py"
    assert r.score > 0


def test_recency_beats_distance(tmp_path):
    """Most recent file wins via 1/distance scoring."""
    t = tmp_path / "t.jsonl"
    _write(
        t,
        [
            _assistant_tool("Read", file_path="src/shikhu/store.py"),  # distance 3
            _assistant_tool("Read", file_path="src/shikhu/store.py"),  # distance 2
            _assistant_tool("Read", file_path="src/shikhu/refresh.py"),  # distance 1
            _user("how does this work?"),
        ],
    )
    r = attribute_to_file(
        t, turn_index=3, tracked_files=["src/shikhu/refresh.py", "src/shikhu/store.py"]
    )
    # refresh.py is more recent (1/1 = 1.0); store.py has 1/3 + 1/2 = 0.83. refresh.py wins.
    assert r.attributed_file == "src/shikhu/refresh.py"
    assert r.runner_up_file == "src/shikhu/store.py"


def test_prompt_mention_boosts(tmp_path):
    """Explicit basename mention in the prompt overrides recency."""
    t = tmp_path / "t.jsonl"
    _write(
        t,
        [
            _assistant_tool("Read", file_path="src/shikhu/refresh.py"),
            _user("why does store.py join on file_path?"),  # mentions store.py
        ],
    )
    r = attribute_to_file(
        t, turn_index=1, tracked_files=["src/shikhu/refresh.py", "src/shikhu/store.py"]
    )
    assert r.attributed_file == "src/shikhu/store.py"
    assert r.signals["prompt_mention_hit"] is True


def test_no_signal_returns_none(tmp_path):
    """No recent tool calls + no mention = no attribution."""
    t = tmp_path / "t.jsonl"
    _write(
        t,
        [
            _user("just thinking out loud"),
        ],
    )
    r = attribute_to_file(t, turn_index=0, tracked_files=["src/shikhu/refresh.py"])
    assert r.attributed_file is None
    assert r.signals["tool_calls_seen"] == 0


def test_untracked_files_ignored(tmp_path):
    """Tool calls to files outside the tracked set don't contribute."""
    t = tmp_path / "t.jsonl"
    _write(
        t,
        [
            _assistant_tool("Read", file_path="/etc/passwd"),
            _user("why?"),
        ],
    )
    r = attribute_to_file(t, turn_index=1, tracked_files=["src/shikhu/refresh.py"])
    assert r.attributed_file is None


def test_attribution_label_round_trip(tmp_path):
    """Persist an AttributionResult and read it back."""
    import shikhu.store as store
    from shikhu.attribution import ALGO_VERSION, AttributionResult

    result = AttributionResult(
        attributed_file="src/shikhu/refresh.py",
        score=1.5,
        runner_up_file="src/shikhu/store.py",
        runner_up_score=0.5,
        signals={"window_size": 5, "tool_calls_seen": 3, "prompt_mention_hit": False},
    )
    label_id = store.insert_attribution_label(
        query_id=42, query_source="raw_prompts", result=result
    )
    fetched = store.get_attribution_label(query_id=42, query_source="raw_prompts")

    assert fetched["attributed_file"] == "src/shikhu/refresh.py"
    assert fetched["score"] == 1.5
    assert fetched["algo_version"] == ALGO_VERSION
    assert fetched["user_label"] is None

    store.set_attribution_user_label(label_id, "confirmed")
    after = store.get_attribution_label(query_id=42, query_source="raw_prompts")
    assert after["user_label"] == "confirmed"
    assert after["labeled_at"] is not None
