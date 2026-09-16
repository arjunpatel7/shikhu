"""Shared utilities for CLI commands."""

import fnmatch
import os
import subprocess
from pathlib import Path

import typer
from rich.console import Console

console = Console()

COVERED_THRESHOLD = 3

# Extensions tracked for question generation and coverage.
DEFAULT_EXTENSIONS = ".py,.js,.ts,.jsx,.tsx,.html,.css"
# Summaries also cover docs — kept as a superset of DEFAULT_EXTENSIONS so
# refresh's orphan pruning never deletes summaries that `shikhu summarize` created.
SUMMARY_EXTENSIONS = DEFAULT_EXTENSIONS + ",.md"


def ensure_api_key() -> None:
    """Exit with a friendly message if OPENROUTER_API_KEY is missing.

    Call at the top of commands that hit the OpenRouter API, so a misconfigured
    key fails once with instructions instead of once per file."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return
    console.print("[red]OPENROUTER_API_KEY is not set.[/red]")
    if os.environ.get("INCEPTION_API_KEY"):
        console.print(
            "  Shikhu now uses OpenRouter instead of the Inception API directly, so "
            "[bold]INCEPTION_API_KEY[/bold] is no longer read."
        )
    console.print(
        "  Question and summary generation need an OpenRouter API key. Add "
        "[bold]OPENROUTER_API_KEY=...[/bold] to a [bold].env[/bold] file in this repo "
        "(auto-loaded) or export it in your shell."
    )
    console.print("  Get a key at [link]https://openrouter.ai/keys[/link]")
    raise typer.Exit(code=1)


def _git_lines(*args: str) -> list[str]:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def changed_files(base: str, extensions: str = DEFAULT_EXTENSIONS) -> list[str]:
    """Trackable files changed on this branch since `base`, plus uncommitted edits.

    Uses the merge base (`base...HEAD`), so commits that landed on `base` after
    branching don't count. Deleted files are excluded. Exits with an error if
    `base` isn't a valid commit."""
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        console.print(f"[red]Unknown git ref for --since:[/red] [bold]{base}[/bold]")
        raise typer.Exit(code=2)

    changed = set(_git_lines("diff", "--name-only", "--diff-filter=d", f"{base}...HEAD"))
    changed |= set(_git_lines("diff", "--name-only", "--diff-filter=d", "HEAD"))
    return [f for f in get_trackable_files(extensions) if f in changed]


def get_trackable_files(
    extensions: str = DEFAULT_EXTENSIONS,
    quizignore_path: Path | None = None,
) -> list[str]:
    """Return repo files filtered by extensions and .quizignore."""
    result = subprocess.run(["git", "ls-files"], capture_output=True, text=True)
    if result.returncode != 0:
        return []

    ext_set = set(extensions.split(","))
    all_files = [
        f
        for f in result.stdout.strip().split("\n")
        if f and any(f.endswith(ext) for ext in ext_set)
    ]

    ignore_path = quizignore_path or Path(".quizignore")
    if ignore_path.exists():
        patterns = [
            line.strip()
            for line in ignore_path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        def _ignored(f, patterns):
            for p in patterns:
                if fnmatch.fnmatch(f, p):
                    return True
                if fnmatch.fnmatch(os.path.basename(f), p):
                    return True
                if f.startswith(p.rstrip("/") + "/"):
                    return True
            return False

        all_files = [f for f in all_files if not _ignored(f, patterns)]

    return all_files
