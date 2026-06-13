"""Tests for golden re-validation (#52)."""

from conftest import _insert_questions

import shikhu.store as store


def _make_pending_golden(file_path="code.py"):
    """Insert a question and force it into the pending-revalidation state."""
    ids = _insert_questions(file_path, n=1)
    conn = store._get_conn()
    conn.execute(
        "UPDATE questions SET golden = TRUE, stale = TRUE, pending_revalidation = TRUE, "
        "answered_at = CURRENT_TIMESTAMP WHERE id = ?",
        (ids[0],),
    )
    conn.commit()
    conn.close()
    return ids[0]


def test_get_revalidation_questions_returns_pending(_fresh_db):
    """get_revalidation_questions surfaces pending-revalidation goldens."""
    qid = _make_pending_golden("code.py")
    revs = store.get_revalidation_questions()
    assert [r["id"] for r in revs] == [qid]


def test_reinstate_golden_recounts_in_coverage(_fresh_db):
    """reinstate_golden clears stale + pending flag and keeps golden, so it counts again."""
    qid = _make_pending_golden("code.py")
    assert store.get_golden_counts() == {}  # pending reval doesn't count

    store.reinstate_golden(qid)

    assert store.get_golden_counts() == {"code.py": 1}
    conn = store._get_conn()
    row = conn.execute(
        "SELECT stale, golden, pending_revalidation FROM questions WHERE id = ?", (qid,)
    ).fetchone()
    conn.close()
    assert (row["stale"], row["golden"], row["pending_revalidation"]) == (0, 1, 0)


def test_discard_revalidation_drops_golden(_fresh_db):
    """discard_revalidation removes golden status and clears the pending flag (stays stale)."""
    qid = _make_pending_golden("code.py")

    store.discard_revalidation(qid)

    conn = store._get_conn()
    row = conn.execute(
        "SELECT stale, golden, pending_revalidation FROM questions WHERE id = ?", (qid,)
    ).fetchone()
    conn.close()
    assert (row["stale"], row["golden"], row["pending_revalidation"]) == (1, 0, 0)
    assert store.get_golden_counts() == {}
