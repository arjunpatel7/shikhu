"""Smoke test: every registered shikhu subcommand's --help works.

Importing `shikhu.cli` pulls in every command module, so this catches import-
time breaks (missing deps, signature errors) the rest of the suite can't see.
"""

import pytest
from typer.testing import CliRunner

from shikhu.cli import app

runner = CliRunner()


def _command_names() -> list[str]:
    return [cmd.name or cmd.callback.__name__.replace("_", "-") for cmd in app.registered_commands]


def test_root_help_works():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


@pytest.mark.parametrize("command", _command_names())
def test_command_help_works(command: str):
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.output
