"""Tests for the Jev answer grader. Jev is mocked: these check what we send and what we do
with the answer, not whether Jev is right (that is measured separately, with real calls)."""

from unittest.mock import patch

from shikhu import grade


def _jev(p):
    return ({"matches": {"noul": p}}, {"cost": 0.0})


def test_sends_question_reference_and_candidate_as_state():
    with patch("shikhu.grade.systemone.ask", return_value=_jev(0.9)) as ask:
        grade.grade("why x?", "because y", "since y")
    state = ask.call_args.kwargs["state"]
    assert state == {
        "question": "why x?",
        "reference_answer": "because y",
        "candidate_answer": "since y",
    }


def test_all_three_pieces_go_to_jev_in_one_request():
    with patch("shikhu.grade.systemone.ask", return_value=_jev(0.9)) as ask:
        grade.grade("q", "r", "c")
    assert ask.call_count == 1
    assert list(ask.call_args.kwargs["questions"]) == ["matches"]


def test_returns_the_probability():
    with patch("shikhu.grade.systemone.ask", return_value=_jev(0.37)):
        assert grade.grade("q", "r", "c") == 0.37


def test_empty_answer_scores_zero_without_calling_jev():
    """A respondent that produced nothing costs nothing to grade and is simply wrong."""
    for candidate in ("", "   ", "\n"):
        with patch("shikhu.grade.systemone.ask") as ask:
            assert grade.grade("q", "r", candidate) == 0.0
        ask.assert_not_called()


def test_accept_threshold_is_inclusive():
    assert grade.is_correct(0.5) is True
    assert grade.is_correct(0.4999) is False
    assert grade.is_correct(1.0) is True
    assert grade.is_correct(0.0) is False


def test_grader_id_names_the_model_and_wording_version():
    """Stored on every graded attempt so scores from different graders are never conflated."""
    assert grade.GRADER_ID.endswith("/match-v1")
    assert "jev" in grade.GRADER_ID
