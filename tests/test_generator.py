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


# --- OpenRouter request path ---

import json  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from shikhu import generator  # noqa: E402

_QUIZ_JSON = json.dumps(
    {"questions": [{"question": "Why?", "choices": ["a", "b", "c", "d"], "correct_index": 2}]}
)


def _response(content=_QUIZ_JSON, status=200, finish_reason="stop", model="inception/mercury-2.5"):
    resp = MagicMock()
    resp.status_code = status
    resp.text = "error body"
    resp.json.return_value = {
        "model": model,
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001},
    }
    return resp


def test_quiz_request_shape(monkeypatch):
    """Quiz calls hit OpenRouter with the default model, bearer auth, and strict structured output."""
    monkeypatch.delenv("SHIKHU_MODEL", raising=False)
    with patch("shikhu.generator.requests.post", return_value=_response()) as post:
        quiz, stats = generator.generate_quiz("prompt")

    url = post.call_args.args[0]
    headers = post.call_args.kwargs["headers"]
    payload = post.call_args.kwargs["json"]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer test-key"
    assert payload["model"] == "inception/mercury-2.5"
    assert payload["messages"] == [{"role": "user", "content": "prompt"}]
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["provider"] == {"require_parameters": True}
    assert "reasoning_effort" not in payload
    # Mercury's reasoning would eat max_tokens and truncate the JSON.
    assert payload["reasoning"] == {"effort": "none"}
    assert quiz.questions[0].correct_index == 2
    assert stats["model"] == "inception/mercury-2.5"
    assert stats["completion_tokens"] == 5


def test_attribution_headers(monkeypatch):
    """Every call carries app attribution, and nothing about the user or their code."""
    monkeypatch.delenv("SHIKHU_MODEL", raising=False)
    with patch("shikhu.generator.requests.post", return_value=_response()) as post:
        generator.generate_quiz("prompt")

    headers = post.call_args.kwargs["headers"]
    assert headers["HTTP-Referer"] == "https://github.com/arjunpatel7/shikhu"
    assert headers["X-OpenRouter-Title"] == "shikhu"
    assert headers["X-OpenRouter-Categories"] == "programming-app"
    # The app id is what OpenRouter keys stats on; a change splits them silently.
    assert generator.APP_URL == "https://github.com/arjunpatel7/shikhu"


def test_summary_request_has_no_response_format(tmp_path):
    """Summaries are plain text: no response_format and no provider constraint."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    with patch(
        "shikhu.generator.requests.post", return_value=_response(content="  A summary.  ")
    ) as post:
        summary, _ = generator.generate_summary(str(f))

    payload = post.call_args.kwargs["json"]
    assert "response_format" not in payload
    assert "provider" not in payload
    assert summary == "A summary."


def test_shikhu_model_override(monkeypatch):
    """SHIKHU_MODEL is read per call and sent as the model id, without Mercury's reasoning override."""
    monkeypatch.setenv("SHIKHU_MODEL", "openai/gpt-5-mini")
    with patch("shikhu.generator.requests.post", return_value=_response()) as post:
        generator.generate_quiz("prompt")
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "openai/gpt-5-mini"
    # Non-Inception models may reject effort=none (no reasoning, or reasoning mandatory).
    assert "reasoning" not in payload


def test_quiz_schema_is_strict_compatible():
    """Every object in the schema forbids extra keys, as OpenAI strict structured outputs require."""
    schema = generator.RESPONSE_SCHEMA["schema"]
    objects = [schema, *schema.get("$defs", {}).values()]
    assert all(o.get("additionalProperties") is False for o in objects)


def test_http_error_is_readable():
    with patch("shikhu.generator.requests.post", return_value=_response(status=402)):
        with pytest.raises(RuntimeError, match="OpenRouter API error \\(HTTP 402\\)"):
            generator.generate_quiz("prompt")


def test_truncated_response_is_reported():
    """finish_reason=length raises a clear error instead of a JSON validation failure."""
    with patch("shikhu.generator.requests.post", return_value=_response(finish_reason="length")):
        with pytest.raises(RuntimeError, match="truncated"):
            generator.generate_quiz("prompt")


def test_error_inside_200_is_reported():
    resp = _response()
    resp.json.return_value = {"error": {"code": 502, "message": "upstream down"}}
    with patch("shikhu.generator.requests.post", return_value=resp):
        with pytest.raises(RuntimeError, match="upstream down"):
            generator.generate_quiz("prompt")


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        generator.generate_quiz("prompt")
