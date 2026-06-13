"""Shared fixtures for shikhu tests."""

import pytest
from typer.testing import CliRunner

import shikhu.store as store

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Give every test its own empty DB."""
    db = str(tmp_path / "test.db")
    store.DB_PATH = db
    store.init_db()
    yield db


@pytest.fixture(autouse=True)
def _dummy_api_key(monkeypatch):
    """Satisfy ensure_api_key() in generation commands — no test ever hits the network."""
    monkeypatch.setenv("INCEPTION_API_KEY", "test-key")


def _insert_questions(file_path="fake.py", n=3, stale=False):
    """Insert n dummy questions, return their DB ids."""
    questions = [
        {
            "question_text": f"Q{i}: What does {file_path} do?",
            "choices": ["A", "B", "C", "D"],
            "expected_answer": "A",
        }
        for i in range(n)
    ]
    ids = store.insert_questions(file_path, questions, prompt_version="v1")
    if stale:
        conn = store._get_conn()
        for qid in ids:
            conn.execute("UPDATE questions SET stale = TRUE WHERE id = ?", (qid,))
        conn.commit()
        conn.close()
    return ids
