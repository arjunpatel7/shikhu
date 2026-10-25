"""Shared OpenRouter plumbing — auth, app attribution, and error shape.

`generator` (chat completions) and `systemone` (Jev typed judgments) talk to two different
OpenRouter routes but authenticate the same way, identify shikhu the same way, and report
failures the same way. Those three things live here so a change to any of them happens once.

Route-specific validation does not belong here: each caller checks its own response body
(`choices` vs `answers`) after `check_response` has cleared the transport-level failures.
"""

import os

import requests

# OpenRouter app attribution: identifies shikhu on openrouter.ai/apps and model
# leaderboards. Only the app name/URL below is sent; no user or prompt data.
# APP_URL is the app's permanent id — changing it starts a separate app with
# separate stats, so a future paid product gets its own URL rather than reusing this.
APP_URL = "https://github.com/arjunpatel7/shikhu"
APP_TITLE = "shikhu"
APP_CATEGORIES = "programming-app"

ERROR_SNIPPET = 300  # chars of an API error body worth showing; the rest is noise


def api_key() -> str:
    """The OpenRouter key, read per call so .env loading and test overrides apply."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set — add it to a .env file in this repo or export it"
        )
    return key


def headers() -> dict[str, str]:
    """Auth + app-attribution headers sent with every OpenRouter request."""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key()}",
        "HTTP-Referer": APP_URL,
        "X-OpenRouter-Title": APP_TITLE,
        "X-OpenRouter-Categories": APP_CATEGORIES,
    }


def check_response(response: requests.Response, product: str) -> dict:
    """Parse the body, raising a readable error on a failed request.

    `product` names the thing the user was trying to use ("OpenRouter", "Jev") so the
    message points at the right place. Returns the parsed JSON for route-specific checks."""
    if response.status_code != 200:
        raise RuntimeError(
            f"{product} API error (HTTP {response.status_code}): {response.text[:ERROR_SNIPPET]}"
        )
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"{product} API error: {str(data['error'])[:ERROR_SNIPPET]}")
    return data
