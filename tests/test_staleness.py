"""Tests for file hashing and staleness detection."""

from conftest import _insert_questions

import shikhu.store as store


def test_compute_file_hash(tmp_path):
    """compute_file_hash returns consistent SHA-256 for same content."""
    from shikhu.staleness import compute_file_hash

    f = tmp_path / "test.py"
    f.write_text("print('hello')\n")
    h1 = compute_file_hash(str(f))
    h2 = compute_file_hash(str(f))
    assert h1 == h2
    assert len(h1) == 64


def test_py_comment_edit_keeps_hash(tmp_path):
    """Adding/changing a comment in a .py file does not change its hash."""
    from shikhu.staleness import compute_file_hash

    f = tmp_path / "code.py"
    f.write_text("x = 1\ny = 2\n")
    before = compute_file_hash(str(f))
    f.write_text("x = 1  # a comment\n# another line\ny = 2\n")
    assert compute_file_hash(str(f)) == before


def test_py_whitespace_edit_keeps_hash(tmp_path):
    """Reformatting whitespace / blank lines in a .py file does not change its hash."""
    from shikhu.staleness import compute_file_hash

    f = tmp_path / "code.py"
    f.write_text("def f():\n    return 1\n")
    before = compute_file_hash(str(f))
    f.write_text("def f():\n\n    return 1\n\n\n")
    assert compute_file_hash(str(f)) == before


def test_py_docstring_edit_keeps_hash(tmp_path):
    """Adding or editing a docstring in a .py file does not change its hash."""
    from shikhu.staleness import compute_file_hash

    f = tmp_path / "code.py"
    f.write_text("def f():\n    return 1\n")
    before = compute_file_hash(str(f))
    f.write_text('def f():\n    """Now documented."""\n    return 1\n')
    assert compute_file_hash(str(f)) == before


def test_py_code_change_changes_hash(tmp_path):
    """A real code change in a .py file does change its hash."""
    from shikhu.staleness import compute_file_hash

    f = tmp_path / "code.py"
    f.write_text("x = 1\n")
    before = compute_file_hash(str(f))
    f.write_text("x = 2\n")
    assert compute_file_hash(str(f)) != before


def test_non_py_uses_byte_hash(tmp_path):
    """Non-Python files hash by bytes, so a comment edit DOES change the hash."""
    from shikhu.staleness import compute_file_hash

    f = tmp_path / "script.js"
    f.write_text("const x = 1;\n")
    before = compute_file_hash(str(f))
    f.write_text("const x = 1; // comment\n")
    assert compute_file_hash(str(f)) != before


def test_malformed_py_falls_back(tmp_path):
    """A .py file that doesn't parse still returns a hash (byte-hash fallback)."""
    from shikhu.staleness import compute_file_hash

    f = tmp_path / "broken.py"
    f.write_text("def (:\n  not valid python\n")
    h = compute_file_hash(str(f))
    assert h is not None and len(h) == 64


def test_staleness_changed_file(tmp_path, _fresh_db):
    """When file content changes, questions are marked stale."""
    from shikhu.staleness import compute_file_hash, mark_stale_questions

    f = tmp_path / "code.py"
    f.write_text("v1")
    original_hash = compute_file_hash(str(f))

    _insert_questions(str(f), n=2)
    conn = store._get_conn()
    conn.execute(
        "UPDATE files SET content_hash = ? WHERE filepath = ?",
        (original_hash, str(f)),
    )
    conn.commit()
    conn.close()

    f.write_text("v2")
    stale_count = mark_stale_questions()
    assert stale_count == 2


def test_staleness_unchanged_no_stale(tmp_path, _fresh_db):
    """When file content is unchanged, no questions are marked stale."""
    from shikhu.staleness import compute_file_hash, mark_stale_questions

    f = tmp_path / "code.py"
    f.write_text("stable")
    h = compute_file_hash(str(f))

    _insert_questions(str(f), n=2)
    conn = store._get_conn()
    conn.execute(
        "UPDATE files SET content_hash = ? WHERE filepath = ?",
        (h, str(f)),
    )
    conn.commit()
    conn.close()

    stale_count = mark_stale_questions()
    assert stale_count == 0


def test_staleness_null_lines_conservative(tmp_path, _fresh_db):
    """Questions without line ranges are marked stale when file changes."""
    from shikhu.staleness import compute_file_hash, mark_stale_questions

    f = tmp_path / "code.py"
    f.write_text("v1")
    h = compute_file_hash(str(f))

    _insert_questions(str(f), n=1)
    conn = store._get_conn()
    conn.execute(
        "UPDATE files SET content_hash = ? WHERE filepath = ?",
        (h, str(f)),
    )
    conn.commit()
    conn.close()

    f.write_text("v2")
    stale_count = mark_stale_questions()
    assert stale_count == 1


def test_staleness_updates_hash(tmp_path, _fresh_db):
    """After staleness check, stored hash is updated to current."""
    from shikhu.staleness import compute_file_hash, mark_stale_questions

    f = tmp_path / "code.py"
    f.write_text("v1")
    old_hash = compute_file_hash(str(f))

    _insert_questions(str(f), n=1)
    conn = store._get_conn()
    conn.execute(
        "UPDATE files SET content_hash = ? WHERE filepath = ?",
        (old_hash, str(f)),
    )
    conn.commit()
    conn.close()

    f.write_text("v2")
    new_hash = compute_file_hash(str(f))
    mark_stale_questions()

    conn = store._get_conn()
    row = conn.execute("SELECT content_hash FROM files WHERE filepath = ?", (str(f),)).fetchone()
    conn.close()
    assert row["content_hash"] == new_hash


def test_stale_golden_enters_revalidation(tmp_path, _fresh_db):
    """A golden question whose file changes is flagged pending_revalidation, not just stale."""
    from shikhu.staleness import compute_file_hash, mark_stale_questions

    f = tmp_path / "code.py"
    f.write_text("x = 1\n")
    ids = _insert_questions(str(f), n=1)
    conn = store._get_conn()
    conn.execute(
        "UPDATE files SET content_hash = ? WHERE filepath = ?", (compute_file_hash(str(f)), str(f))
    )
    conn.execute("UPDATE questions SET golden = TRUE WHERE id = ?", (ids[0],))
    conn.commit()
    conn.close()

    f.write_text("x = 2\n")
    mark_stale_questions()

    conn = store._get_conn()
    row = conn.execute(
        "SELECT stale, golden, pending_revalidation FROM questions WHERE id = ?", (ids[0],)
    ).fetchone()
    conn.close()
    assert (row["stale"], row["golden"], row["pending_revalidation"]) == (1, 1, 1)


def test_stale_non_golden_no_revalidation(tmp_path, _fresh_db):
    """A non-golden question that goes stale is NOT flagged for re-validation."""
    from shikhu.staleness import compute_file_hash, mark_stale_questions

    f = tmp_path / "code.py"
    f.write_text("x = 1\n")
    ids = _insert_questions(str(f), n=1)
    conn = store._get_conn()
    conn.execute(
        "UPDATE files SET content_hash = ? WHERE filepath = ?", (compute_file_hash(str(f)), str(f))
    )
    conn.commit()
    conn.close()

    f.write_text("x = 2\n")
    mark_stale_questions()

    conn = store._get_conn()
    row = conn.execute(
        "SELECT stale, pending_revalidation FROM questions WHERE id = ?", (ids[0],)
    ).fetchone()
    conn.close()
    assert (row["stale"], row["pending_revalidation"]) == (1, 0)
