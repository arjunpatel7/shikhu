"""Unified shikhu CLI — the single entrypoint for shikhu."""

from importlib.metadata import version as _pkg_version

import typer

from shikhu.commands.clean import clean
from shikhu.commands.coverage import coverage
from shikhu.commands.generate_from_study import generate_from_study
from shikhu.commands.init import init
from shikhu.commands.quiz import quiz
from shikhu.commands.refresh import refresh
from shikhu.commands.review_log import log_review, log_study_question
from shikhu.commands.study_context import study_context
from shikhu.commands.summarize import summarize

app = typer.Typer(help="Project Shikhu: knowledge coverage for your codebase.")


def _version_callback(value: bool):
    if value:
        typer.echo(f"shikhu {_pkg_version('shikhu')}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
):
    pass


app.command()(init)
app.command()(quiz)
app.command()(coverage)
app.command()(clean)
app.command()(refresh)
app.command()(summarize)
app.command("study-context")(study_context)
app.command("log-review")(log_review)
app.command("log-study-question")(log_study_question)
app.command("generate-from-study")(generate_from_study)

if __name__ == "__main__":
    app()
