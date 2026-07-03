"""shikhu init — set up database and config."""

import os
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv

from shikhu.commands.install_skill import SKILL_NAME, install_skill_files
from shikhu.commands.utils import console
from shikhu.store import init_db


def init(
    no_skill: bool = typer.Option(
        False, "--no-skill", help="Skip installing the /shikhu-study skill for your agent."
    ),
):
    """Set up Shikhu: create DB, check API keys, generate .quizignore."""
    # find_dotenv(usecwd=True): load_dotenv() otherwise searches upward from this
    # installed package's own file location, not the user's working directory.
    load_dotenv(find_dotenv(usecwd=True))

    console.print()
    console.print("[bold]Shikhu[/bold] — knowledge coverage for your codebase")
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

    if not no_skill:
        for dest in install_skill_files(Path.cwd()):
            console.print(f"  [green]>[/green] Installed /{SKILL_NAME} skill → {dest}")

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
