"""Cross-file questions: one question linked to every file it needs.

A question keeps its lead file on questions.file_path; the others live in question_files, and
the question_links view is their union. These tests cover the three things that must hold: per-file
queries count a cross-file question under each file, staleness follows ANY linked file, and an
existing file's staleness baseline is never moved by a question that merely touches it.
"""

from unittest.mock import patch

import pytest
from conftest import _insert_questions, runner

import shikhu.store as store
from shikhu.cli import app
from shikhu.generator import MCQuestion, Quiz
from shikhu.staleness import compute_file_hash, mark_stale_questions


def _q(text="Why do a and b hand off?"):
    return [{"question_text": text, "choices": ["A", "B", "C", "D"], "expected_answer": "A"}]


def _files(tmp_path, monkeypatch, **named):
    monkeypatch.chdir(tmp_path)
    for name, body in named.items():
        (tmp_path / f"{name}.py").write_text(body)


def _stale(qid):
    conn = store._get_conn()
    row = conn.execute("SELECT stale FROM questions WHERE id = ?", (qid,)).fetchone()
    conn.close()
    return bool(row["stale"])


def _baseline(path):
    conn = store._get_conn()
    row = conn.execute("SELECT content_hash FROM files WHERE filepath = ?", (path,)).fetchone()
    conn.close()
    return row["content_hash"]


# --- linking ---


def test_extra_files_are_linked_with_the_lead_first():
    (qid,) = store.insert_questions("a.py", _q(), extra_files={"c.py": None, "b.py": None})
    assert store.get_question_files([qid]) == {qid: ["a.py", "b.py", "c.py"]}


def test_single_file_question_links_only_its_own_file():
    (qid,) = _insert_questions("a.py", n=1)
    assert store.get_question_files([qid]) == {qid: ["a.py"]}
    assert store.get_question_files([]) == {}


def test_the_lead_file_is_never_linked_to_itself_twice():
    (qid,) = store.insert_questions("a.py", _q(), extra_files={"a.py": None, "b.py": None})
    assert store.get_question_files([qid]) == {qid: ["a.py", "b.py"]}


# --- per-file queries count it under each file ---


def test_golden_count_credits_every_linked_file():
    (qid,) = store.insert_questions("a.py", _q(), extra_files={"b.py": None})
    store.mark_golden(qid)
    assert store.get_golden_counts() == {"a.py": 1, "b.py": 1}


def test_scope_includes_a_cross_file_question_via_any_of_its_files():
    (cross,) = store.insert_questions("a.py", _q(), extra_files={"b.py": None})
    (other,) = _insert_questions("z.py", n=1)
    in_b = [q["id"] for q in store.get_unasked_questions(file_paths=["b.py"])]
    assert in_b == [cross]
    both = {q["id"] for q in store.get_unasked_questions(file_paths=["b.py", "z.py"])}
    assert both == {cross, other}
    assert store.get_unasked_questions(file_paths=["nothing.py"]) == []


def test_unasked_counts_for_refresh_include_cross_file_questions():
    from shikhu.generator import _get_unasked_counts

    store.insert_questions("a.py", _q(), extra_files={"b.py": None})
    assert _get_unasked_counts() == {"a.py": 1, "b.py": 1}


# --- staleness follows any linked file ---


def test_editing_any_linked_file_stales_the_question(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch, a="x = 1\n", b="y = 2\n", z="w = 0\n")
    (cross,) = store.insert_questions(
        "a.py",
        _q(),
        content_hash=compute_file_hash("a.py"),
        extra_files={"b.py": compute_file_hash("b.py")},
    )
    (unrelated,) = store.insert_questions("z.py", _q("z?"), content_hash=compute_file_hash("z.py"))
    assert mark_stale_questions() == 0

    (tmp_path / "b.py").write_text("y = 3\n")  # a semantic change to the OTHER file only
    assert mark_stale_questions() == 1
    assert _stale(cross) and not _stale(unrelated)


def test_editing_the_lead_file_still_stales_it(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch, a="x = 1\n", b="y = 2\n")
    (qid,) = store.insert_questions(
        "a.py",
        _q(),
        content_hash=compute_file_hash("a.py"),
        extra_files={"b.py": compute_file_hash("b.py")},
    )
    (tmp_path / "a.py").write_text("x = 5\n")
    mark_stale_questions()
    assert _stale(qid)


def test_a_linked_file_with_no_baseline_gets_one(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch, a="x = 1\n", b="y = 2\n")
    h = compute_file_hash("b.py")
    store.insert_questions("a.py", _q(), extra_files={"b.py": h})
    assert _baseline("b.py") == h


def test_an_existing_baseline_is_not_moved_by_a_cross_file_question(tmp_path, monkeypatch):
    """b.py was baselined earlier and has been edited since. A question that merely touches it
    must not move that baseline, or b.py's own older questions would never go stale."""
    _files(tmp_path, monkeypatch, a="x = 1\n", b="y = 2\n")
    (old,) = store.insert_questions("b.py", _q("old"), content_hash=compute_file_hash("b.py"))
    original = _baseline("b.py")
    (tmp_path / "b.py").write_text("y = 99\n")

    store.insert_questions(
        "a.py",
        _q(),
        content_hash=compute_file_hash("a.py"),
        extra_files={"b.py": compute_file_hash("b.py")},
    )
    assert _baseline("b.py") == original
    mark_stale_questions()
    assert _stale(old)


# --- the inquiry loop records every file ---


def test_record_inquiry_also_files_become_seed_files():
    result = runner.invoke(
        app,
        ["record-inquiry", "a.py", "how do a and b work together?", "--confirmed",
         "--also", "b.py", "--also", "c.py", "--also", "a.py"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    (seed,) = store.get_conceptual_study_questions_for_file("a.py")
    assert seed["extra_files"] == ["b.py", "c.py"]  # the lead file is not repeated


def test_record_inquiry_without_also_has_no_extra_files():
    runner.invoke(app, ["record-inquiry", "a.py", "how does a work?", "--confirmed"])
    (seed,) = store.get_conceptual_study_questions_for_file("a.py")
    assert seed["extra_files"] == []


# --- generation shows the extra files and stores the links ---


def _seed_inquiry(lead, text, also):
    rid = store.start_review(lead)
    store.add_review_files(rid, also)
    store.log_study_question(rid, text, was_conceptual=True, answered_satisfactorily=True)


def _fake_quiz():
    return Quiz(
        questions=[MCQuestion(question="Why?", choices=["w", "x", "y", "z"], correct_index=0)]
    )


def test_generation_prompt_carries_every_file_and_the_cross_file_rule(tmp_path, monkeypatch):
    from shikhu import generator

    _files(tmp_path, monkeypatch, a="LEAD = 1\n", b="SECOND = 2\n")
    _seed_inquiry("a.py", "how do a and b hand off?", ["b.py", "missing.py"])
    seen = {}

    def fake(prompt):
        seen["prompt"] = prompt
        return _fake_quiz(), {}

    with patch.object(generator, "generate_quiz", fake):
        quiz, stats, seed_ids, extra = generator.generate_questions_from_study_seeds("a.py")
    assert extra == ["b.py"]  # a file that does not exist is skipped, not an error
    assert "LEAD = 1" in seen["prompt"] and "SECOND = 2" in seen["prompt"]
    assert generator.CROSS_FILE_RULE in seen["prompt"]


def test_generation_without_extra_files_is_unchanged(tmp_path, monkeypatch):
    from shikhu import generator

    _files(tmp_path, monkeypatch, a="LEAD = 1\n")
    _seed_inquiry("a.py", "how does a work?", [])
    seen = {}
    with patch.object(
        generator, "generate_quiz", lambda p: (seen.setdefault("p", p), (_fake_quiz(), {}))[1]
    ):
        _, _, _, extra = generator.generate_questions_from_study_seeds("a.py")
    assert extra == []
    assert generator.CROSS_FILE_RULE not in seen["p"]


def test_extra_files_are_capped(tmp_path, monkeypatch):
    from shikhu import generator

    names = {f"f{i}": f"V{i} = {i}\n" for i in range(6)}
    _files(tmp_path, monkeypatch, a="LEAD = 1\n", **names)
    _seed_inquiry("a.py", "survey question", [f"f{i}.py" for i in range(6)])
    with patch.object(generator, "generate_quiz", lambda p: (_fake_quiz(), {})):
        *_, extra = generator.generate_questions_from_study_seeds("a.py")
    assert len(extra) == generator.MAX_EXTRA_FILES


@pytest.mark.parametrize(
    ("pins", "expected"),
    [(["sha1", "sha1"], "sha1"), (["sha1", None], None), (["sha1", "sha2"], None)],
)
def test_commit_pin_only_when_every_file_matches_one_commit(tmp_path, monkeypatch, pins, expected):
    _files(tmp_path, monkeypatch, a="LEAD = 1\n", b="SECOND = 2\n")
    _seed_inquiry("a.py", "how do a and b hand off?", ["b.py"])
    it = iter(pins)
    with (
        patch(
            "shikhu.generator.generate_questions_from_study_seeds",
            lambda *a, **k: (_fake_quiz(), {"completion_tokens": 1, "elapsed": 0.1}, [1], ["b.py"]),
        ),
        patch("shikhu.commands.generate_from_study.pin_commit", lambda p, h: next(it)),
        patch("shikhu.commands.generate_from_study.ingest_recent"),
    ):
        result = runner.invoke(app, ["generate-from-study", "a.py"])
    assert result.exit_code == 0, result.output
    conn = store._get_conn()
    row = conn.execute("SELECT id, commit_sha FROM questions").fetchone()
    conn.close()
    assert row["commit_sha"] == expected
    assert store.get_question_files([row["id"]]) == {row["id"]: ["a.py", "b.py"]}
