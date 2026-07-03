"""Tests for generator helpers, specifically answer-position shuffling in _quiz_to_rows."""

from shikhu.generator import MCQuestion, Quiz, _quiz_to_rows


def _make_quiz(correct_index: int = 0) -> Quiz:
    """A single-question Quiz with the correct answer at correct_index."""
    choices = ["Alpha", "Beta", "Gamma", "Delta"]
    return Quiz(
        questions=[
            MCQuestion(
                question="What is the correct answer?",
                choices=choices,
                correct_index=correct_index,
            )
        ]
    )


def test_expected_answer_in_choices():
    """After shuffling, expected_answer must still appear in choices."""
    for correct_index in range(4):
        rows = _quiz_to_rows(_make_quiz(correct_index))
        row = rows[0]
        assert row["expected_answer"] in row["choices"]


def test_expected_answer_text_is_correct():
    """expected_answer must match the original answer text, not just any choice."""
    original_choices = ["Alpha", "Beta", "Gamma", "Delta"]
    for correct_index in range(4):
        rows = _quiz_to_rows(_make_quiz(correct_index))
        assert rows[0]["expected_answer"] == original_choices[correct_index]


def test_shuffle_varies_position():
    """The correct answer should not always land in the same position.

    With 4 choices, the probability of landing in position 0 every time across
    40 trials is (1/4)^40 — astronomically unlikely if shuffle is truly random.
    """
    positions = {_quiz_to_rows(_make_quiz(0))[0]["choices"].index("Alpha") for _ in range(40)}
    assert len(positions) > 1, (
        "Correct answer always shuffled to the same position — shuffle broken"
    )


def test_all_choices_preserved():
    """Shuffling must not drop or duplicate any choice."""
    rows = _quiz_to_rows(_make_quiz(0))
    assert sorted(rows[0]["choices"]) == ["Alpha", "Beta", "Delta", "Gamma"]
