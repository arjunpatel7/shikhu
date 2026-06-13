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
    """Exit with a friendly message if INCEPTION_API_KEY is missing.

    Call at the top of commands that hit the Mercury API, so a misconfigured
    key fails once with instructions instead of once per file."""
    if os.environ.get("INCEPTION_API_KEY"):
        return
    console.print("[red]INCEPTION_API_KEY is not set.[/red]")
    console.print(
        "  Question and summary generation need a Mercury API key. Add "
        "[bold]INCEPTION_API_KEY=...[/bold] to a [bold].env[/bold] file in this repo "
        "(auto-loaded) or export it in your shell."
    )
    console.print("  Get a key at [link]https://www.inceptionlabs.ai/[/link]")
    raise typer.Exit(code=1)


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
