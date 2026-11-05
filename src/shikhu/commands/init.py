"""shikhu init — set up database and config."""

import os
from pathlib import Path

import typer

from shikhu.commands.install_hook import (
    ask_mode,
    in_git_repo,
    install_hook_file,
    interactive,
    report,
)
from shikhu.commands.install_skill import install_skill_files
from shikhu.commands.utils import console
from shikhu.env import KEY_NAME, load_env, save_key
from shikhu.store import init_db


def init(
    no_skill: bool = typer.Option(
        False,
        "--no-skill",
        help="Skip installing the /shikhu-inquiry and /shikhu-study skills for your agent.",
    ),
):
    """Set up Shikhu: create DB, check API keys, generate .quizignore."""
    load_env()

    console.print()
    console.print("[bold]Shikhu[/bold] — knowledge coverage for your codebase")
    console.print()

    init_db()
    console.print("  [green]>[/green] Database initialized")

    if not os.environ.get(KEY_NAME) and interactive():
        _ask_for_key()

    if not os.environ.get(KEY_NAME):
        console.print(
            "  [yellow]![/yellow] OPENROUTER_API_KEY not set — question generation won't work"
        )
        if os.environ.get("INCEPTION_API_KEY"):
            console.print(
                "    [dim]INCEPTION_API_KEY is no longer used — get an OpenRouter key at https://openrouter.ai/keys[/dim]"
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
            console.print(f"  [green]>[/green] Installed /{dest.parent.name} skill → {dest}")

    _offer_hook()

    console.print()
    console.print(
        "[bold green]Ready.[/bold green] Run [bold]shikhu summarize[/bold] to index your code, then "
        "[bold]shikhu map[/bold] to see where your questions have been."
    )
    console.print()


def _ask_for_key() -> None:
    """Ask for the OpenRouter key once and save it for every repo. Enter skips."""
    console.print(
        "  Shikhu needs an OpenRouter API key to write questions. "
        "Get one at [link]https://openrouter.ai/keys[/link]"
    )
    key = typer.prompt(
        "  Paste it here (hidden; Enter to skip)",
        default="",
        show_default=False,
        hide_input=True,
    ).strip()
    if not key:
        return
    path = save_key(key)
    os.environ[KEY_NAME] = key
    shown = f"~/{path.relative_to(Path.home())}" if path.is_relative_to(Path.home()) else path
    console.print(
        f"  [green]>[/green] Saved your key to {shown} (only you can read it; every repo uses it)"
    )


def _offer_hook():
    """Ask whether to gate pushes on understanding. Only when someone can answer, in a repo."""
    if not interactive() or not in_git_repo():
        console.print(
            "  [dim]>[/dim] To check your understanding before each push: shikhu install-hook"
        )
        return
    mode = ask_mode()
    if mode:
        report(*install_hook_file(mode), mode)


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
