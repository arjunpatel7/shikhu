"""Tests for question timing columns: answered_at (renamed from asked_at) and presented_at."""

import sqlite3

from conftest import _insert_questions

import shikhu.store as store


def test_grade_question_records_answered_at():
    """Grading a question stamps answered_at (the column formerly misnamed asked_at)."""
    ids = _insert_questions("fake.py", n=1)
    store.grade_question(ids[0], user_answer="A", correct=True)
    conn = store._get_conn()
    row = conn.execute("SELECT answered_at FROM questions WHERE id = ?", (ids[0],)).fetchone()
    conn.close()
    assert row["answered_at"] is not None


def test_mark_presented_records_presented_at():
    """mark_presented stamps presented_at so time-to-answer (answered_at - presented_at) is derivable."""
    ids = _insert_questions("fake.py", n=1)
    store.mark_presented(ids[0])
    conn = store._get_conn()
    row = conn.execute("SELECT presented_at FROM questions WHERE id = ?", (ids[0],)).fetchone()
    conn.close()
    assert row["presented_at"] is not None


def test_init_db_migrates_legacy_asked_at(tmp_path):
    """An existing DB with asked_at is renamed to answered_at (data preserved) and gains presented_at."""
    legacy = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(legacy)
    conn.executescript(
        "CREATE TABLE questions ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " file_path TEXT NOT NULL,"
        " question_text TEXT NOT NULL,"
        " expected_answer TEXT NOT NULL,"
        " asked_at TIMESTAMP);"
    )
    conn.execute(
        "INSERT INTO questions (file_path, question_text, expected_answer, asked_at) "
        "VALUES ('x.py', 'q', 'A', '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    store.DB_PATH = legacy
    store.init_db()

    conn = sqlite3.connect(legacy)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    row = conn.execute("SELECT answered_at FROM questions").fetchone()
    conn.close()
    assert "answered_at" in cols
    assert "asked_at" not in cols  # renamed, not duplicated
    assert "presented_at" in cols
    assert row[0] == "2026-01-01 00:00:00"  # value carried through the rename
