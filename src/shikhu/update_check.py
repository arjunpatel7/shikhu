"""Non-blocking PyPI version check with a 24-hour disk cache.

Usage: start_update_check() at command start, print_update_nudge() after output.
Both are safe to call anywhere — all failures are silenced.
"""

import json
import sys
import threading
import urllib.request
from datetime import UTC, datetime
from importlib.metadata import version as _pkg_version
from pathlib import Path

_CACHE_TTL_SECONDS = 86400  # 24 hours
_NETWORK_TIMEOUT = 3  # seconds

_result: str | None = None  # latest version from PyPI, set by background thread
_thread: threading.Thread | None = None


def _cache_file() -> Path:
    """Path.home() can raise in restricted environments — resolve lazily, not at import time."""
    return Path.home() / ".cache" / "shikhu" / "update_check.json"


def _fetch_latest() -> str | None:
    """Hit PyPI JSON API and return the latest version string, or None on failure."""
    try:
        with urllib.request.urlopen(
            "https://pypi.org/pypi/shikhu/json", timeout=_NETWORK_TIMEOUT
        ) as resp:
            return json.loads(resp.read())["info"]["version"]
    except Exception:
        return None


def _load_cache() -> str | None:
    """Return cached latest version if cache is fresh, else None."""
    try:
        data = json.loads(_cache_file().read_text())
        age = (datetime.now(UTC) - datetime.fromisoformat(data["checked_at"])).total_seconds()
        if age < _CACHE_TTL_SECONDS:
            return data["latest"]
    except Exception:
        pass
    return None


def _write_cache(latest: str) -> None:
    try:
        cache_file = _cache_file()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({"latest": latest, "checked_at": datetime.now(UTC).isoformat()})
        )
    except Exception:
        pass


def _check():
    global _result
    cached = _load_cache()
    if cached:
        _result = cached
        return
    latest = _fetch_latest()
    if latest:
        _write_cache(latest)
        _result = latest


def start_update_check() -> None:
    """Kick off the version check in a background thread. Call at command start."""
    global _thread
    # Skip in CI / non-interactive shells so scripts aren't noisy.
    if not sys.stdout.isatty():
        return
    _thread = threading.Thread(target=_check, daemon=True)
    _thread.start()


def print_update_nudge() -> None:
    """Wait briefly for the thread, then print a nudge if a newer version exists."""
    if _thread is None:
        return
    _thread.join(timeout=2)
    if _result is None:
        return
    try:
        current = _pkg_version("shikhu")
        from packaging.version import Version

        if Version(_result) > Version(current):
            print(
                f"\n  A new version of shikhu is available: {current} → {_result}\n"
                f"  Run: uv tool upgrade shikhu",
                file=sys.stderr,
            )
    except Exception:
        pass
