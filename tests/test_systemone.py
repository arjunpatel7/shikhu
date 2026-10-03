"""Tests for the Jev (System One) client. No test hits the network."""

from unittest.mock import MagicMock, patch

import pytest

from shikhu import systemone


@pytest.fixture(autouse=True)
def _zero_spend():
    """Spend is module-level state; don't let one test's cost leak into the next."""
    systemone.reset_spend()
    yield
    systemone.reset_spend()


def _response(status=200, answers=None, cost=1.4e-05, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.text = "error body"
    resp.headers = headers or {}
    resp.json.return_value = {
        "model": "typesafe/jev-1.13-20260917",
        "provider": "TypeSafe",
        "answers": answers if answers is not None else {"q": {"type": "noul", "noul": 0.97}},
        "usage": {"input_tokens": 337, "output_tokens": 22, "cost": cost},
    }
    return resp


# --- question builders ---


def test_noul_omits_empty_criteria():
    """No criteria means no criteria key — an empty dict would be a meaningless rubric."""
    assert systemone.noul("Is it conceptual?") == {
        "type": "noul",
        "instructions": "Is it conceptual?",
    }


def test_noul_with_criteria():
    q = systemone.noul("Is it conceptual?", "asks why", "issues a command")
    assert q["criteria"] == {"true": "asks why", "false": "issues a command"}


def test_choice_rejects_over_255_options():
    """The API caps Choice at 255 options; fail here rather than with a remote 400."""
    with pytest.raises(ValueError, match="255"):
        systemone.choice("pick", {str(i): None for i in range(256)})


def test_score_levels_are_ordered_criteria():
    assert systemone.score("how relevant", ["not", "somewhat", "very"])["criteria"] == [
        "not",
        "somewhat",
        "very",
    ]


# --- request shape ---


def test_request_shape():
    """Jev calls carry the state, the questions, the default model, and app attribution."""
    with patch("shikhu.systemone.requests.post", return_value=_response()) as post:
        answers, stats = systemone.ask("some prompt", {"q": systemone.noul("Is it conceptual?")})

    assert post.call_args.args[0] == "https://openrouter.ai/api/v1/systemone"
    headers = post.call_args.kwargs["headers"]
    payload = post.call_args.kwargs["json"]
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["HTTP-Referer"] == "https://github.com/arjunpatel7/shikhu"
    assert headers["X-OpenRouter-Title"] == "shikhu"
    assert payload["model"] == "jev-1.13"
    assert payload["state"] == "some prompt"
    assert payload["questions"]["q"]["type"] == "noul"
    assert answers["q"]["noul"] == 0.97
    assert stats["input_tokens"] == 337


def test_many_questions_ride_one_request():
    """Independent judgments share a state, so they must not fan out into N requests."""
    questions = {
        "conceptual": systemone.noul("Is it conceptual?"),
        "about_code": systemone.noul("Is it about this codebase?"),
        "depth": systemone.score("How deep?", ["shallow", "deep"]),
    }
    with patch("shikhu.systemone.requests.post", return_value=_response()) as post:
        systemone.ask("prompt", questions)

    assert post.call_count == 1
    assert set(post.call_args.kwargs["json"]["questions"]) == set(questions)


def test_model_override(monkeypatch):
    monkeypatch.setenv("SHIKHU_JEV_MODEL", "~typesafe/jev-latest")
    with patch("shikhu.systemone.requests.post", return_value=_response()) as post:
        systemone.ask("prompt", {"q": systemone.noul("x")})
    assert post.call_args.kwargs["json"]["model"] == "~typesafe/jev-latest"


def test_state_can_be_structured():
    """State may be a dict so each part is named — the docs' recommended default."""
    state = {"question": "why hashing?", "candidates": ["a.py", "b.py"]}
    with patch("shikhu.systemone.requests.post", return_value=_response()) as post:
        systemone.ask(state, {"q": systemone.noul("x")})
    assert post.call_args.kwargs["json"]["state"] == state


# --- errors and retries ---


def test_http_error_is_readable():
    with patch("shikhu.systemone.requests.post", return_value=_response(status=402)):
        with pytest.raises(RuntimeError, match="Jev API error \\(HTTP 402\\)"):
            systemone.ask("prompt", {"q": systemone.noul("x")})


def test_missing_answers_is_reported():
    """A 200 without answers is a protocol failure, not an empty result to hand back."""
    resp = _response()
    resp.json.return_value = {"model": "jev", "usage": {}}
    with patch("shikhu.systemone.requests.post", return_value=resp):
        with pytest.raises(RuntimeError, match="no answers"):
            systemone.ask("prompt", {"q": systemone.noul("x")})


def test_rate_limit_retries_then_succeeds():
    limited = _response(status=429, headers={"retry-after": "0"})
    with patch("shikhu.systemone.requests.post", side_effect=[limited, _response()]) as post:
        with patch("shikhu.systemone.time.sleep") as sleep:
            answers, _ = systemone.ask("prompt", {"q": systemone.noul("x")})
    assert post.call_count == 2
    assert sleep.call_args.args[0] == 0.0  # honored retry-after, not the backoff
    assert answers["q"]["noul"] == 0.97


def test_rate_limit_gives_up_after_max_retries():
    limited = _response(status=429, headers={"retry-after": "0"})
    with patch("shikhu.systemone.requests.post", return_value=limited) as post:
        with patch("shikhu.systemone.time.sleep"):
            with pytest.raises(RuntimeError, match="HTTP 429"):
                systemone.ask("prompt", {"q": systemone.noul("x")})
    assert post.call_count == systemone.MAX_RETRIES


def test_backoff_when_no_retry_after_header():
    limited = _response(status=429)
    with patch("shikhu.systemone.requests.post", side_effect=[limited, _response()]):
        with patch("shikhu.systemone.time.sleep") as sleep:
            systemone.ask("prompt", {"q": systemone.noul("x")})
    assert sleep.call_args.args[0] == 1.0  # 2 ** 0


# --- budget guard ---


def test_spend_accumulates():
    with patch("shikhu.systemone.requests.post", return_value=_response(cost=0.25)):
        systemone.ask("prompt", {"q": systemone.noul("x")})
        _, stats = systemone.ask("prompt", {"q": systemone.noul("x")})
    assert systemone.spent_usd() == pytest.approx(0.5)
    assert stats["spent_total"] == pytest.approx(0.5)


def test_budget_stops_a_runaway_loop(monkeypatch):
    """The cap is checked before the request, so passing it costs nothing further."""
    monkeypatch.setenv("SHIKHU_JEV_BUDGET_USD", "0.10")
    with patch("shikhu.systemone.requests.post", return_value=_response(cost=0.10)) as post:
        systemone.ask("prompt", {"q": systemone.noul("x")})
        with pytest.raises(systemone.BudgetExceeded, match="0.10"):
            systemone.ask("prompt", {"q": systemone.noul("x")})
    assert post.call_count == 1
