"""Past questions `shikhu map` found seed generate-from-study, once, like inquiry seeds."""

from unittest.mock import patch

from conftest import runner

import shikhu.store as store
from shikhu.cli import app
from shikhu.generator import MCQuestion, Quiz
from shikhu.inquiry import MAP_ALGO_VERSION, past_question_bar
from shikhu.transcripts import PastPrompt


def _past(text, files, conceptual=0.9, answerable=0.9, asked_at="2026-09-01T10:00:00.000Z"):
    """Store a scored past prompt; returns its id."""
    store.add_past_prompts([PastPrompt(text, "s", asked_at, "t")])
    pid = next(
        p["id"]
        for p in store.get_unscored_past_prompts(MAP_ALGO_VERSION)
        if p["prompt_text"] == text
    )
    store.save_past_prompt_score(pid, conceptual, answerable, files, MAP_ALGO_VERSION)
    return pid


def _seeds(file_path="a.py", **kw):
    return store.get_past_question_seeds(file_path, **past_question_bar(), **kw)


def _quiz():
    return Quiz(
        questions=[MCQuestion(question="Q?", choices=["A", "B", "C", "D"], correct_index=0)]
    )


def _run(match=None):
    """generate-from-study with the writer and Jev mocked; returns (result, seed batches)."""
    calls = []

    def fake_gen(file_path, num_questions=3, seeds=None):
        calls.append(seeds)
        return _quiz(), {"completion_tokens": 1, "elapsed": 0.1}, [s["id"] for s in seeds], []

    with (
        patch("shikhu.generator.generate_questions_from_study_seeds", fake_gen),
        patch("shikhu.inquiry.match_existing", match or (lambda t, c: {})),
        patch("shikhu.commands.generate_from_study.ingest_recent"),
    ):
        return runner.invoke(app, ["generate-from-study", "a.py"]), calls


# --- which past questions seed a file ---


def test_only_the_lead_file_seeds_and_the_rest_ride_along():
    _past("how does a call b?", {"a.py": 0.9, "b.py": 0.7, "c.py": 0.4})
    (seed,) = _seeds("a.py")
    assert seed["question_text"] == "how does a call b?"
    assert seed["extra_files"] == ["b.py"]  # c.py is below the file bar
    assert _seeds("b.py") == []


def test_prompts_below_the_question_bar_do_not_seed():
    _past("please fix a", {"a.py": 0.9}, conceptual=0.2)
    _past("what do react hooks do", {"a.py": 0.9}, answerable=0.1)
    assert _seeds() == []


def test_a_used_past_question_is_consumed_until_its_question_goes_stale():
    pid = _past("why is a cached?", {"a.py": 0.9})
    (qid,) = store.insert_questions(
        "a.py", [{"question_text": "Q", "choices": ["A", "B"], "expected_answer": "A"}]
    )
    store.mark_past_prompts_used([pid], qid)
    assert _seeds() == []
    assert len(_seeds(unconsumed=False)) == 1
    with store._conn() as conn:
        conn.execute("UPDATE questions SET stale = TRUE WHERE id = ?", (qid,))
    assert [s["id"] for s in _seeds()] == [pid]


def test_the_quiz_can_show_a_past_seed():
    pid = _past("why is a cached?", {"a.py": 0.9})
    assert store.get_seed_texts([pid], "past_prompts") == [{"id": pid, "text": "why is a cached?"}]


# --- generate-from-study ---


def test_generate_from_study_uses_past_questions_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    pid = _past("why is a cached?", {"a.py": 0.9})

    result, calls = _run()
    assert result.exit_code == 0, result.output
    assert [[s["id"] for s in batch] for batch in calls] == [[pid]]
    assert "past question(s) from `shikhu map`" in result.output
    (q,) = store.get_unasked_questions(file_paths=["a.py"])
    assert q["seed_query_source"] == "past_prompts"

    result, calls = _run()
    assert calls == []
    assert "already has a quiz question" in result.output


def test_live_and_past_seeds_are_separate_batches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    rid = store.start_review("a.py")
    store.log_study_question(rid, "how does a work?", was_conceptual=True)
    _past("why is a cached?", {"a.py": 0.9})

    result, calls = _run()
    assert result.exit_code == 0, result.output
    assert [[s["question_text"] for s in batch] for batch in calls] == [
        ["how does a work?"],
        ["why is a cached?"],
    ]
    sources = sorted(
        q["seed_query_source"] for q in store.get_unasked_questions(file_paths=["a.py"])
    )
    assert sources == ["past_prompts", "review_questions"]


def test_a_matching_past_question_requeues_as_of_when_it_was_asked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    (qid,) = store.insert_questions(
        "a.py", [{"question_text": "Q", "choices": ["A", "B"], "expected_answer": "A"}]
    )
    pid = _past("why is a cached?", {"a.py": 0.9}, asked_at="2026-09-01T10:00:00.000Z")

    result, calls = _run(match=lambda t, c: {qid: 0.95})
    assert result.exit_code == 0, result.output
    assert calls == []
    with store._conn() as conn:
        row = conn.execute(
            "SELECT reasked_at, seed_query_ids FROM questions WHERE id = ?", (qid,)
        ).fetchone()
        used = conn.execute(
            "SELECT quiz_question_id FROM past_prompts WHERE id = ?", (pid,)
        ).fetchone()[0]
    assert row["reasked_at"] == "2026-09-01 10:00:00"
    assert used == qid
    assert _seeds() == []


def test_an_older_past_question_never_moves_reasked_backwards():
    (qid,) = store.insert_questions(
        "a.py", [{"question_text": "Q", "choices": ["A", "B"], "expected_answer": "A"}]
    )
    store.requeue_question(qid, [1])  # a live re-ask: now
    store.requeue_question(qid, [7], source="past_prompts", asked_at="2020-01-01T00:00:00Z")
    with store._conn() as conn:
        row = conn.execute(
            "SELECT reasked_at, seed_query_ids, seed_query_source FROM questions WHERE id = ?",
            (qid,),
        ).fetchone()
    assert row["reasked_at"] > "2026"
    # Sources aren't mixed: the past prompt didn't join the live seeds.
    assert row["seed_query_source"] == "review_questions" and row["seed_query_ids"] == "[1]"


def test_no_seeds_from_any_source_points_at_all_three_ways_in(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result, calls = _run()
    assert result.exit_code == 1
    assert "shikhu map" in " ".join(result.output.split())  # Rich wraps at 80 columns
