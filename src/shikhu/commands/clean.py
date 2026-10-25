"""shikhu clean — delete the coverage database."""

import os

import typer
from rich.prompt import Prompt

from shikhu.commands.utils import console
from shikhu.store import DB_PATH


def clean(yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt.")):
    """Delete the coverage database."""
    db = DB_PATH
    if not os.path.exists(db):
        console.print("[dim]No database found.[/dim]")
        return

    if not yes:
        confirm = Prompt.ask(
            "[bold red]Delete coverage database?[/bold red] This cannot be undone",
            choices=["y", "n"],
            default="n",
        )
        if confirm != "y":
            console.print("Aborted.")
            return

    os.remove(db)
    console.print("[green]Database deleted.[/green]")
