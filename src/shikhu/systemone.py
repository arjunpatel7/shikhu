"""Jev client — typed judgments (Noul/Choice/Score) via OpenRouter's System One route.

Jev returns calibrated probabilities instead of text, which is what makes it usable as a
gate: a Noul's probability becomes a tunable threshold rather than a yes/no you have to
take or leave.

Routed through OpenRouter (`/v1/systemone`), so it shares OPENROUTER_API_KEY and the app
attribution already set up for question generation — no second provider, no second key.
The request/response shape is TypeSafe's; OpenRouter adds `id`, `provider`, `usage.cost`.
"""

import os
import time

import requests

from shikhu.openrouter import ERROR_SNIPPET, check_response, headers

SYSTEMONE_URL = "https://openrouter.ai/api/v1/systemone"
DEFAULT_JEV_MODEL = "jev-1.13"

# Jev is fast and meant to sit in a blocking path; a long hang is worse than no answer.
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3

# Running total for this process. Jev costs ~$1.4e-05 per small call, so a runaway loop is
# cheap in absolute terms but still worth a hard stop — spend should never be a surprise.
DEFAULT_BUDGET_USD = 1.00
_spent_usd = 0.0


class BudgetExceeded(RuntimeError):
    """Raised when cumulative spend for this process passes the configured cap."""


def spent_usd() -> float:
    """Cumulative cost of Jev calls made by this process."""
    return _spent_usd


def reset_spend() -> None:
    """Zero the spend counter (tests, and between benchmark runs)."""
    global _spent_usd
    _spent_usd = 0.0


def _budget() -> float:
    raw = os.environ.get("SHIKHU_JEV_BUDGET_USD")
    return float(raw) if raw else DEFAULT_BUDGET_USD


# --- question builders ---
# Thin dict builders rather than model classes: the payload is already JSON-shaped, and
# keeping it plain means no typesafe-sdk dependency for three literal dicts.


def noul(instructions, true_means: str | None = None, false_means: str | None = None) -> dict:
    """A yes/no judgment. Returns the probability the answer is yes.

    Supply true_means/false_means whenever "yes" is open to interpretation — the criteria
    are what stop the model from answering a subtly different question than you asked."""
    q: dict = {"type": "noul", "instructions": instructions}
    criteria = {}
    if true_means is not None:
        criteria["true"] = true_means
    if false_means is not None:
        criteria["false"] = false_means
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions, criteria: dict) -> dict:
    """Pick one option from `criteria` (option -> description, or None). Max 255 options."""
    if len(criteria) > 255:
        raise ValueError(f"Choice accepts at most 255 options, got {len(criteria)}")
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions, levels: list) -> dict:
    """Rate along ordered `levels`, lowest first. Returns a probability-weighted position."""
    return {"type": "score", "instructions": instructions, "criteria": levels}


# --- request ---


def _check_response(response: requests.Response) -> dict:
    """Return the parsed JSON body, raising a readable error on API failure."""
    data = check_response(response, "Jev")
    if "answers" not in data:
        raise RuntimeError(f"Jev response had no answers: {str(data)[:ERROR_SNIPPET]}")
    return data


def _retry_after(response: requests.Response, attempt: int) -> float:
    """Seconds to wait before retrying. Honors retry-after, else exponential backoff."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    return 2.0**attempt


def ask(state, questions: dict, model: str | None = None) -> tuple[dict, dict]:
    """Evaluate `state` against every question in `questions`. Returns (answers, stats).

    All questions see the same state and are answered in parallel by one request, so asking
    several costs barely more than asking one — the state is the bulk of the tokens. Answers
    come back keyed by the same names you passed in.

    `state` may be a string, dict, or list of strings."""
    global _spent_usd
    model = model or os.environ.get("SHIKHU_JEV_MODEL") or DEFAULT_JEV_MODEL

    budget = _budget()
    if _spent_usd >= budget:
        raise BudgetExceeded(
            f"Jev spend for this process is ${_spent_usd:.4f}, at or over the "
            f"${budget:.2f} cap. Raise SHIKHU_JEV_BUDGET_USD to continue."
        )

    payload = {"model": model, "state": state, "questions": questions}
    start = time.time()
    for attempt in range(MAX_RETRIES):
        response = requests.post(
            SYSTEMONE_URL, headers=headers(), json=payload, timeout=REQUEST_TIMEOUT
        )
        if response.status_code != 429 or attempt == MAX_RETRIES - 1:
            break
        time.sleep(_retry_after(response, attempt))

    elapsed = time.time() - start
    data = _check_response(response)

    usage = data.get("usage") or {}
    cost = usage.get("cost") or 0.0
    _spent_usd += cost
    stats = {
        "elapsed": elapsed,
        "model": data.get("model", model),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost": cost,
        "spent_total": _spent_usd,
    }
    return data["answers"], stats
