"""Tests for shikhu clean command."""

import os
from unittest.mock import patch

from conftest import runner


def test_clean_deletes_db(_fresh_db):
    """shikhu clean --yes removes the database file."""
    from shikhu.cli import app

    db_path = _fresh_db
    assert os.path.exists(db_path)
    with patch("shikhu.commands.clean.DB_PATH", db_path):
        result = runner.invoke(app, ["clean", "--yes"])
    assert result.exit_code == 0
    assert not os.path.exists(db_path)


def test_clean_aborts_without_confirm(_fresh_db):
    """shikhu clean without --yes prompts and aborts on 'n'."""
    from shikhu.cli import app

    db_path = _fresh_db
    with patch("shikhu.commands.clean.DB_PATH", db_path):
        result = runner.invoke(app, ["clean"], input="n\n")
    assert result.exit_code == 0
    assert os.path.exists(db_path)
