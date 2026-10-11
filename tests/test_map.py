"""Tests for `shikhu map` and the past-prompt reader behind it. No test hits the network."""

import json

import pytest
from typer.testing import CliRunner

import shikhu.commands.map as map_cmd
import shikhu.inquiry as inquiry
import shikhu.store as store
from shikhu.cli import app
from shikhu.transcripts import PastPrompt, iter_past_prompts, scan

runner = CliRunner()


def _user(content, ts="2026-09-01T00:00:00Z", session="s1", **flags):
    return {
        "type": "user",
        "sessionId": session,
        "timestamp": ts,
        "message": {"content": content},
        **flags,
    }


def _assistant(text=None, read=None):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if read:
        content.append({"type": "tool_use", "name": "Read", "input": {"file_path": read}})
    return {"type": "assistant", "message": {"content": content}}


def _write(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


# --- reading transcripts ---


def test_reader_keeps_real_prompts_and_drops_harness_turns(tmp_path):
    t = tmp_path / "a.jsonl"
    _write(
        t,
        [
            _user("how does the staleness check decide a file changed?"),
            _user(
                [
                    {"type": "text", "text": "why is this cached instead of recomputed?"},
                    {"type": "image"},
                ]
            ),
            _user([{"type": "tool_result", "content": "ok"}]),
            _user("what does the subagent see here exactly?", isSidechain=True),
            _user(
                "This session is being continued from a previous conversation...",
                isCompactSummary=True,
            ),
            _user("<task-notification>done</task-notification>"),
            _user("[Request interrupted by user]"),
            _user("Tool loaded."),
            _user("You are analyzing a code repository for issues.", entrypoint="sdk-py"),
            _user("yes"),
            {"type": "assistant", "message": {"content": "why not?"}},
        ],
    )
    texts = [p.text for p in iter_past_prompts(t)]
    assert texts == [
        "how does the staleness check decide a file changed?",
        "why is this cached instead of recomputed?",
    ]


def test_reader_attaches_the_reply_each_prompt_followed(tmp_path):
    t = tmp_path / "a.jsonl"
    _write(
        t,
        [
            _assistant(text="The retry loop lives in store.py."),
            _user("ok so what does that loop do?"),
            _assistant(read="src/store.py"),
            _user([{"type": "tool_result", "content": "..."}]),
            _assistant(text="It retries on lock errors."),
            _user("now please rename it to retry_writes"),
        ],
    )
    first, second = iter_past_prompts(t)
    assert first.context == "The retry loop lives in store.py."
    assert second.context == "It retries on lock errors."


def test_scan_dedupes_and_keeps_the_earliest_copy(tmp_path):
    _write(
        tmp_path / "a.jsonl",
        [_user("How does   refresh decide?", ts="2026-09-02T00:00:00Z", session="new")],
    )
    _write(
        tmp_path / "b.jsonl",
        [_user("how does refresh decide?", ts="2026-09-01T00:00:00Z", session="old")],
    )
    prompts, n = scan(tmp_path)
    assert n == 2
    assert len(prompts) == 1
    assert prompts[0].session_id == "old"


def test_scan_without_a_transcript_dir(tmp_path):
    assert scan(tmp_path / "missing") == ([], 0)


# --- scoring ---


def test_score_past_prompt_rides_one_request_per_batch(monkeypatch):
    calls = []

    def fake_ask(state, questions, model=None):
        calls.append(set(questions))
        answers = {k: {"noul": 0.9} for k in questions}
        answers["f1"] = {"noul": 0.1}  # below the show floor
        answers["conceptual"] = {"noul": 0.8}
        answers["gate"] = {"noul": 0.95}
        return answers, {"elapsed": 0.1, "cost": 0.0001, "model": "jev"}

    monkeypatch.setattr(inquiry.systemone, "ask", fake_ask)
    result, stats = inquiry.score_past_prompt("why?", {"a.py": "A", "b.py": "B"})
    assert len(calls) == 1
    assert {"conceptual", "gate", "f0", "f1"} == calls[0]
    assert result == {"conceptual": 0.8, "answerable": 0.95, "files": {"a.py": 0.9}}
    assert stats["batches"] == 1


# --- the command ---

FILES = {"big.py": 200, "small.py": 10, "mid.py": 50, "never.py": 300}
ALGO = map_cmd.MAP_ALGO_VERSION

# prompt -> (conceptual, answerable, {file: score})
SCORES = {
    "how does big work under the hood?": (0.9, 0.9, {"big.py": 0.9}),
    "why does big call small like that?": (0.9, 0.9, {"big.py": 0.8, "small.py": 0.7}),
    "what is the point of small's helper?": (0.9, 0.9, {"small.py": 0.9}),
    "explain how mid batches its writes": (0.9, 0.9, {"mid.py": 0.9}),
    "please fix the failing test in big": (0.2, 0.9, {"big.py": 0.9}),  # an instruction
    "what does the react docs say about hooks": (0.8, 0.1, {}),  # not this codebase
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name, lines in FILES.items():
        (tmp_path / name).write_text("x\n" * lines)
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    _write(transcripts / "s.jsonl", [_user(p) for p in SCORES])

    # Every .py in the repo is tracked and summarized, including ones a test adds later.
    def tracked(ext=None):
        return sorted(f.name for f in tmp_path.glob("*.py"))

    monkeypatch.setattr(map_cmd, "get_trackable_files", tracked)
    monkeypatch.setattr(
        map_cmd, "load_summaries", lambda: {f: f"summary of {f}" for f in tracked()}
    )
    calls = []

    def fake_score(text, summaries, context=None):
        calls.append({"text": text, "files": set(summaries), "context": context})
        c, a, files = SCORES[text]
        return {"conceptual": c, "answerable": a, "files": files}, {}

    monkeypatch.setattr(map_cmd, "score_past_prompt", fake_score)
    return {"root": tmp_path, "transcripts": str(transcripts), "calls": calls}


def _map(repo, *args, **kw):
    return runner.invoke(app, ["map", *args, "--transcripts", repo["transcripts"]], **kw)


def test_dry_run_estimates_and_sends_nothing(repo):
    result = _map(repo, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "6 prompt(s) to score" in result.output
    assert "Dry run" in result.output
    assert repo["calls"] == []


def test_declining_the_prompt_sends_nothing(repo):
    result = _map(repo, input="n\n")
    assert result.exit_code == 0, result.output
    assert repo["calls"] == []


def test_map_counts_questions_and_picks_an_easy_start(repo):
    result = _map(repo, "--yes")
    assert result.exit_code == 0, result.output
    out = result.output
    # The instruction and the off-codebase question don't count; the cross-file one counts once.
    assert "4 past questions about this codebase" in out
    assert "Asked about most" in out
    assert "never.py" in out.split("Not asked about yet")[1]
    # big.py leads 2 questions, small.py and mid.py 1 each (a tie, so all three make the cut);
    # small.py is the shortest.
    assert "Start here: small.py" in out


def test_rerun_only_scores_new_prompts(repo):
    _map(repo, "--yes")
    assert len(repo["calls"]) == 6
    result = _map(repo, "--yes")
    assert result.exit_code == 0, result.output
    assert len(repo["calls"]) == 6
    assert "0 new since the last map" in result.output


def test_a_new_scoring_version_rescores_everything(repo):
    _map(repo, "--yes")
    with store._conn() as conn:
        conn.execute("UPDATE past_prompts SET algo_version = 'map-v1/old'")
    result = _map(repo, "--dry-run")
    assert "6 prompt(s) to score" in result.output


def test_unscored_prompts_the_filter_now_rejects_are_dropped(repo):
    # Stored by an older, looser filter, never scored.
    store.add_past_prompts(
        [PastPrompt("You are analyzing a repo, old filter let me in", "s", None, "t")]
    )
    _map(repo, "--dry-run")
    assert store.count_past_prompts(ALGO)["total"] == 6


def test_scored_prompts_survive_a_rescan(repo):
    store.add_past_prompts(
        [PastPrompt("why was this scored before the filter changed?", "s", None, "t")]
    )
    old = store.get_unscored_past_prompts(ALGO)[0]
    store.save_past_prompt_score(old["id"], 0.9, 0.9, {"big.py": 0.9}, ALGO)
    _map(repo, "--dry-run")
    assert store.count_past_prompts(ALGO) == {"total": 7, "scored": 1}


def test_start_here_skips_covered_files(repo):
    _map(repo, "--yes")
    ids = store.insert_questions(
        "small.py",
        [
            {"question_text": f"q{i}", "choices": ["A", "B"], "expected_answer": "A"}
            for i in range(3)
        ],
        prompt_version="v1",
    )
    for qid in ids:
        store.mark_golden(qid)
    result = _map(repo)
    assert "Start here: mid.py" in result.output
    assert "1/4 files fully covered" in result.output


def test_tiny_files_are_neither_scored_nor_mapped(repo):
    (repo["root"] / "tiny.py").write_text("from x import y\n")
    result = _map(repo, "--yes")
    assert all("tiny.py" not in c["files"] for c in repo["calls"])
    assert "tiny.py" not in result.output
    assert "0/4 files fully covered" in result.output


def test_context_reaches_the_scorer(repo):
    _write(
        repo["root"] / "transcripts" / "ctx.jsonl",
        [
            _assistant(text="Here is the retry loop in big.py."),
            _user("so what does that loop do exactly?"),
        ],
    )
    SCORES["so what does that loop do exactly?"] = (0.9, 0.9, {"big.py": 0.9})
    try:
        _map(repo, "--yes")
    finally:
        del SCORES["so what does that loop do exactly?"]
    call = next(c for c in repo["calls"] if c["text"].startswith("so what"))
    assert call["context"] == "Here is the retry loop in big.py."


def test_start_here_goes_by_what_questions_were_mainly_about(repo):
    """Broad questions touch mid.py often but never lead with it. Without lead counts mid.py
    would tie for most-asked and, being shorter than big.py, be picked."""
    broad = [f"how does the whole pipeline fit together, part {i}?" for i in range(3)]
    _write(repo["root"] / "transcripts" / "broad.jsonl", [_user(q) for q in broad])
    for q in broad:
        SCORES[q] = (0.9, 0.9, {"big.py": 0.9, "mid.py": 0.7})
    try:
        result = _map(repo, "--yes")
    finally:
        for q in broad:
            del SCORES[q]
    per_file, lead, total = store.get_past_questions_per_file(0.5, 0.5, 0.6, ALGO)
    assert total == 7
    assert per_file["mid.py"] == 4 and lead.get("mid.py") == 1
    assert lead["big.py"] == 5
    # Ranked by every touch, mid.py (4) would make the top half and win as the shorter file.
    assert "Start here: small.py" in result.output


def test_failed_prompts_stay_unscored_for_the_next_run(repo, monkeypatch):
    def flaky(text, summaries, context=None):
        raise RuntimeError("Jev down")

    monkeypatch.setattr(map_cmd, "score_past_prompt", flaky)
    result = _map(repo, "--yes")
    assert result.exit_code == 0, result.output
    assert "6 failed" in result.output
    assert store.count_past_prompts(ALGO) == {"total": 6, "scored": 0}


def test_no_transcripts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x\n" * 10)
    monkeypatch.setattr(map_cmd, "load_summaries", lambda: {"a.py": "A"})
    monkeypatch.setattr(map_cmd, "get_trackable_files", lambda ext=None: ["a.py"])
    result = runner.invoke(app, ["map", "--transcripts", str(tmp_path / "none")])
    assert result.exit_code == 0, result.output
    assert "No Claude Code transcripts found" in result.output


def test_no_summaries(monkeypatch):
    monkeypatch.setattr(map_cmd, "load_summaries", lambda: {})
    result = runner.invoke(app, ["map"])
    assert result.exit_code == 1
    assert "shikhu summarize" in result.output
