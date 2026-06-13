"""shikhu init — set up database and config."""

import os
from pathlib import Path

from dotenv import load_dotenv

from shikhu.commands.utils import console
from shikhu.store import init_db


def init():
    """Set up Shikhu: create DB, check API keys, generate .quizignore."""
    load_dotenv()

    console.print()
    console.print("[bold]Project Shikhu[/bold] — knowledge coverage for your codebase")
    console.print()

    init_db()
    console.print("  [green]>[/green] Database initialized")

    if not os.environ.get("INCEPTION_API_KEY"):
        console.print(
            "  [yellow]![/yellow] INCEPTION_API_KEY not set — question generation won't work"
        )
    else:
        console.print("  [green]>[/green] API key found")

    quizignore = Path(".quizignore")
    if not quizignore.exists():
        quizignore.write_text("# Files to skip during quiz generation\n*.lock\n.claude/\n")
        console.print("  [green]>[/green] Created .quizignore")
    else:
        console.print("  [dim]>[/dim] .quizignore already exists")

    _ensure_gitignored()

    console.print()
    console.print(
        "[bold green]Ready.[/bold green] Run [bold]shikhu refresh[/bold] to generate questions, then [bold]shikhu quiz[/bold] to start."
    )
    console.print()


def _ensure_gitignored():
    """Keep coverage.db (and .env) out of the user's git history.

    coverage.db contains prompts harvested from local Claude Code transcripts,
    so committing it would leak private conversation text."""
    gitignore = Path(".gitignore")
    content = gitignore.read_text() if gitignore.exists() else ""

    if "coverage.db" not in content:
        prefix = "" if not content or content.endswith("\n") else "\n"
        with gitignore.open("a") as f:
            f.write(f"{prefix}\n# shikhu local database (contains captured prompts)\ncoverage.db\n")
        console.print("  [green]>[/green] Added coverage.db to .gitignore")

    if Path(".env").exists() and ".env" not in content:
        console.print(
            "  [yellow]![/yellow] .env exists but isn't in .gitignore — add it so your API key stays out of git"
        )
