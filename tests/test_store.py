"""Tests for store round-trip operations."""

import json

from conftest import _insert_questions

import shikhu.store as store


def test_insert_and_retrieve_questions():
    """Insert questions and retrieve them as unasked, one per file."""
    _insert_questions("file_a.py", n=2)
    _insert_questions("file_b.py", n=2)
    _insert_questions("file_c.py", n=2)
    unasked = store.get_unasked_questions(limit=10)
    # Should get one question per file (3 files)
    assert len(unasked) == 3
    files = {q["file_path"] for q in unasked}
    assert files == {"file_a.py", "file_b.py", "file_c.py"}
    parsed_choices = json.loads(unasked[0]["choices"])
    assert parsed_choices == ["A", "B", "C", "D"]


def test_summary_upsert_replaces_row():
    """Upsert writes once, then replaces in-place on the same file_path."""
    store.upsert_summary("a.py", "hash1", "first summary", prompt_version="v1")
    row = store.get_summary("a.py")
    assert row["summary_text"] == "first summary"
    assert row["content_hash"] == "hash1"

    store.upsert_summary("a.py", "hash2", "second summary", prompt_version="v2")
    row = store.get_summary("a.py")
    assert row["summary_text"] == "second summary"
    assert row["content_hash"] == "hash2"
    assert row["prompt_version"] == "v2"

    assert store.get_summary("never-touched.py") is None


def test_raw_prompt_classification_round_trip():
    """insert_raw_prompt persists is_conceptual; get_conceptual_prompts returns only TRUE rows."""
    store.insert_raw_prompt("why does mercury return JSON?", session_id="s1", is_conceptual=True)
    store.insert_raw_prompt("run the tests", session_id="s1", is_conceptual=False)
    store.insert_raw_prompt("ambient capture without classifier", session_id="s2")

    conceptual = store.get_conceptual_prompts()
    assert len(conceptual) == 1
    assert conceptual[0]["prompt_text"] == "why does mercury return JSON?"


def test_review_lifecycle():
    """Start a review, log a question against it, end it with a summary."""
    rid = store.start_review("store.py", transcript_path="/tmp/fake.jsonl")
    assert isinstance(rid, int)

    store.log_study_question(rid, "Why SQLite?", was_conceptual=True, answered_satisfactorily=True)
    store.log_study_question(rid, "What command runs tests?", was_conceptual=False)

    questions = store.get_study_questions(rid)
    assert len(questions) == 2
    assert questions[0]["question_text"] == "Why SQLite?"
    assert questions[0]["was_conceptual"] == 1

    store.end_review(rid, agent_summary="User grasps schema, shaky on migrations.")
    reviews = store.get_reviews_for_file("store.py")
    assert len(reviews) == 1
    assert reviews[0]["ended_at"] is not None
    assert reviews[0]["agent_summary"] == "User grasps schema, shaky on migrations."
    assert reviews[0]["transcript_path"] == "/tmp/fake.jsonl"


def test_insert_questions_with_seed_links():
    """Seed metadata round-trips through insert_questions → get_unasked_questions."""
    import json

    rid = store.start_review("seeded.py")
    seed_a = store.log_study_question(rid, "Why X?", was_conceptual=True)
    seed_b = store.log_study_question(rid, "Why Y?", was_conceptual=True)
    store.end_review(rid)

    store.insert_questions(
        "seeded.py",
        [
            {
                "question_text": "Which design choice supports X?",
                "choices": ["A", "B", "C", "D"],
                "expected_answer": "A",
            }
        ],
        prompt_version="v1",
        seed_query_ids=[seed_a, seed_b],
        seed_query_source="review_questions",
    )

    rows = store.get_unasked_questions(limit=10, file_path="seeded.py")
    assert len(rows) == 1
    assert json.loads(rows[0]["seed_query_ids"]) == [seed_a, seed_b]
    assert rows[0]["seed_query_source"] == "review_questions"

    seeds = store.get_seed_texts([seed_a, seed_b], "review_questions")
    assert {s["text"] for s in seeds} == {"Why X?", "Why Y?"}


def test_seeded_recent_questions_sampled_more_often():
    """Quiz sampling biases toward seeded-recent questions over unseeded ones.

    Pool: 1 seeded-recent + 9 unseeded for the same file. Uniform random would pick
    the seeded one ~10/100 trials; the bias weights (1 vs 4) should land it >25/100.
    Threshold is loose enough that a correct implementation virtually never flakes."""
    rid = store.start_review("biased.py")
    seed = store.log_study_question(rid, "Why X?", was_conceptual=True)
    store.end_review(rid)

    store.insert_questions(
        "biased.py",
        [{"question_text": "seeded Q", "choices": ["A"], "expected_answer": "A"}],
        seed_query_ids=[seed],
        seed_query_source="review_questions",
    )
    _insert_questions("biased.py", n=9)

    seeded_picks = sum(
        1
        for _ in range(100)
        if store.get_unasked_questions(limit=1, file_path="biased.py")[0]["seed_query_ids"]
    )
    assert seeded_picks >= 25, f"expected bias toward seeded, got {seeded_picks}/100"


def test_get_conceptual_study_questions_for_file():
    """Pull conceptual /study questions across all reviews of a single file."""
    rid_a1 = store.start_review("a.py")
    store.log_study_question(rid_a1, "Why X?", was_conceptual=True)
    store.log_study_question(rid_a1, "What command runs X?", was_conceptual=False)
    store.end_review(rid_a1)

    rid_a2 = store.start_review("a.py")
    store.log_study_question(rid_a2, "How does Y interact with X?", was_conceptual=True)
    store.end_review(rid_a2)

    rid_b = store.start_review("b.py")
    store.log_study_question(rid_b, "Why does B do Z?", was_conceptual=True)
    store.end_review(rid_b)

    a_questions = store.get_conceptual_study_questions_for_file("a.py")
    assert [q["question_text"] for q in a_questions] == [
        "Why X?",
        "How does Y interact with X?",
    ]

    assert store.get_conceptual_study_questions_for_file("never-touched.py") == []
