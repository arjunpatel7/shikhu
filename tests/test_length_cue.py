"""The longest-choice check: a key that is the obvious longest option can be found by length.

Detection is a pure function of the choices; generation retries once when it fires and keeps
the better attempt; whatever still has the cue is flagged on the stored question.
"""

from unittest.mock import patch

import pytest

import shikhu.store as store
from shikhu import generator
from shikhu.generator import MCQuestion, Quiz, _quiz_to_rows, length_cue


def _q(choices, correct_index=0):
    return MCQuestion(question="Why?", choices=choices, correct_index=correct_index)


def _quiz(*qs):
    return Quiz(questions=list(qs))


CUED = _q(["a very detailed and specific correct answer here", "wrong one", "wrong two", "wrong 3"])
EVEN = _q(["correct answer one", "wrong answer two!", "wrong answer 333", "wrong answer 444"])


@pytest.mark.parametrize(
    ("choices", "index", "expected"),
    [
        (["x" * 50, "y" * 20, "z" * 20, "w" * 20], 0, True),
        (["x" * 20, "y" * 20, "z" * 20, "w" * 20], 0, False),  # all equal
        (["x" * 20, "y" * 50, "z" * 20, "w" * 20], 0, False),  # a wrong option is longest
        (["x" * 21, "y" * 20, "z" * 20, "w" * 20], 0, False),  # longest, but only by 5%
        (["x" * 23, "y" * 20, "z" * 20, "w" * 20], 0, True),  # 15% over the longest wrong one
        (["y" * 20, "z" * 20, "x" * 60, "w" * 20], 2, True),  # works for any key position
    ],
)
def test_length_cue_detection(choices, index, expected):
    assert length_cue(_q(choices, index)) is expected


def test_rows_flag_only_the_cued_questions_and_keep_the_answer():
    rows = _quiz_to_rows(_quiz(CUED, EVEN))
    assert [r["length_cue"] for r in rows] == [True, False]
    assert rows[0]["expected_answer"] == CUED.choices[0]
    assert sorted(rows[0]["choices"]) == sorted(CUED.choices)  # shuffled, nothing lost


def _gen(*results):
    """A generate_quiz stand-in returning each result in turn, recording its prompts."""
    calls = []

    def fake(prompt):
        calls.append(prompt)
        quiz, stats = results[len(calls) - 1]
        return quiz, stats

    return fake, calls


def test_a_clean_first_attempt_is_not_retried():
    fake, calls = _gen((_quiz(EVEN), {"elapsed": 1.0}))
    with patch.object(generator, "generate_quiz", fake):
        quiz, stats = generator._generate_checked("prompt")
    assert len(calls) == 1 and "length_cue_retried" not in stats
    assert quiz.questions == [EVEN]


def test_a_cued_attempt_is_retried_and_the_better_one_kept():
    fake, calls = _gen(
        (_quiz(CUED), {"elapsed": 1.0, "cost": 0.001}),
        (_quiz(EVEN), {"elapsed": 2.0, "cost": 0.002}),
    )
    with patch.object(generator, "generate_quiz", fake):
        quiz, stats = generator._generate_checked("prompt")
    assert len(calls) == 2 and generator.CHOICE_LENGTH_RETRY in calls[1]
    assert quiz.questions == [EVEN]
    assert stats["length_cue_retried"] is True
    assert stats["elapsed"] == 3.0 and stats["cost"] == pytest.approx(0.003)


def test_when_the_retry_is_no_better_the_first_attempt_is_kept():
    fake, calls = _gen((_quiz(CUED), {"elapsed": 1.0}), (_quiz(CUED), {"elapsed": 1.0}))
    with patch.object(generator, "generate_quiz", fake):
        quiz, stats = generator._generate_checked("prompt")
    assert len(calls) == 2 and quiz.questions == [CUED]
    assert [r["length_cue"] for r in _quiz_to_rows(quiz)] == [True]  # still flagged for counting


def test_a_retry_with_fewer_cues_wins_even_if_not_clean():
    two = _quiz(CUED, CUED)
    one = _quiz(CUED, EVEN)
    fake, _ = _gen((two, {}), (one, {}))
    with patch.object(generator, "generate_quiz", fake):
        quiz, _ = generator._generate_checked("prompt")
    assert quiz is one


def test_the_length_rule_is_in_both_generation_prompts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("X = 1\n")
    seen = []

    def capture(prompt):
        seen.append(prompt)
        return _quiz(EVEN), {}

    rid = store.start_review("a.py")
    store.log_study_question(rid, "how does a work?", was_conceptual=True)
    with patch.object(generator, "generate_quiz", capture):
        generator.generate_question_from_file("a.py")
        generator.generate_questions_from_study_seeds("a.py")
    assert len(seen) == 2 and all(generator.CHOICE_LENGTH_RULE in p for p in seen)


def test_the_flag_is_stored_with_the_question():
    rows = _quiz_to_rows(_quiz(CUED, EVEN))
    ids = store.insert_questions("a.py", rows)
    conn = store._get_conn()
    got = [r["length_cue"] for r in conn.execute("SELECT length_cue FROM questions ORDER BY id")]
    conn.close()
    assert len(ids) == 2 and [bool(x) for x in got] == [True, False]


def test_old_style_rows_without_the_key_store_false():
    (qid,) = store.insert_questions(
        "a.py", [{"question_text": "q", "choices": ["A", "B", "C", "D"], "expected_answer": "A"}]
    )
    conn = store._get_conn()
    row = conn.execute("SELECT length_cue FROM questions WHERE id = ?", (qid,)).fetchone()
    conn.close()
    assert not row["length_cue"]
