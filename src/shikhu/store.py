"""Shikhu persistence layer — SQLite CRUD on top of `sqlite3`.

Sections, in file order:
    Connection / schema    _conn (CM), _get_conn (legacy), init_db, MIGRATIONS
    Files                  get_or_create_file, read_file_lines, get_current_git_hash
    Questions              insert/get/grade/golden/flag/coverage helpers
    Raw prompts            insert/get/label helpers for transcript-captured prompts
    File summaries         upsert_summary, get_summary, delete_summaries_not_in
    Reviews                start_review, end_review, get_reviews_for_file
    Review questions       log_study_question + readers for /shikhu-study sessions
    Attribution labels     insert/get/set helpers for prompt-to-file attribution
    Seed lookups           get_seed_texts

Internal functions use the `_conn` context manager (auto-commit on success,
rollback on exception, always closes). `_get_conn` is kept for external
modules and tests that still manage the connection by hand.
"""

import json
import os
import sqlite3
import subprocess
from contextlib import contextmanager

DB_PATH = os.path.join(os.getcwd(), "coverage.db")

# Quiz sampling bias: lower weight = higher pick probability (sorts earlier on RANDOM*weight).
SEED_BIAS_RECENT_DAYS = 14
SEED_WEIGHT_RECENT = 1
SEED_WEIGHT_OLDER = 3
SEED_WEIGHT_UNSEEDED = 10

_SEED_BIAS_SQL = (
    f"(ABS(RANDOM()) % 100000) * (CASE "
    f"WHEN seed_query_ids IS NOT NULL AND created_at >= datetime('now', '-{SEED_BIAS_RECENT_DAYS} days') THEN {SEED_WEIGHT_RECENT} "
    f"WHEN seed_query_ids IS NOT NULL THEN {SEED_WEIGHT_OLDER} "
    f"ELSE {SEED_WEIGHT_UNSEEDED} END)"
)

# Columns added after the initial schema. Applied with try/except so older DBs auto-upgrade.
MIGRATIONS = [
    # (table, column, type)
    ("questions", "reject_reason", "TEXT"),
    ("questions", "prompt_version", "TEXT"),
    ("files", "content_hash", "TEXT"),
    ("questions", "golden", "BOOLEAN DEFAULT FALSE"),
    ("raw_prompts", "is_conceptual", "BOOLEAN"),
    ("raw_prompts", "message_index", "INTEGER"),
    ("raw_prompts", "transcript_path", "TEXT"),
    ("questions", "seed_query_ids", "TEXT"),
    ("questions", "seed_query_source", "TEXT"),
    ("questions", "created_at", "TIMESTAMP"),
    ("questions", "pending_revalidation", "BOOLEAN DEFAULT FALSE"),
    ("questions", "presented_at", "TIMESTAMP"),
]

# Column renames for older DBs: (table, old_name, new_name). Applied idempotently
# after MIGRATIONS — RENAME COLUMN preserves existing data, unlike drop+add.
RENAMES = [
    ("questions", "asked_at", "answered_at"),
]


# --- Connection / schema ---


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _conn():
    """Open a connection with FK + Row factory; commit on success, rollback on exception, always close."""
    c = _get_conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT NOT NULL UNIQUE,
                last_git_hash TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER REFERENCES files(id),
                file_path TEXT NOT NULL,
                line_start INTEGER,
                line_end INTEGER,
                question_text TEXT NOT NULL,
                choices TEXT,
                expected_answer TEXT NOT NULL,
                presented_at TIMESTAMP,
                answered_at TIMESTAMP,
                user_answer TEXT,
                graded_correct BOOLEAN,
                stale BOOLEAN DEFAULT FALSE,
                quality_flag TEXT,
                quality_note TEXT,
                reject_reason TEXT,
                prompt_version TEXT
            );

            CREATE TABLE IF NOT EXISTS raw_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
                prompt_text TEXT NOT NULL,
                label TEXT,
                label_note TEXT
            );

            CREATE TABLE IF NOT EXISTS file_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                content_hash TEXT,
                summary_text TEXT NOT NULL,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                prompt_version TEXT
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                transcript_path TEXT,
                agent_summary TEXT,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS review_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id INTEGER REFERENCES reviews(id),
                question_text TEXT NOT NULL,
                was_conceptual BOOLEAN DEFAULT TRUE,
                answered_satisfactorily BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS attribution_labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id INTEGER NOT NULL,
                query_source TEXT NOT NULL,
                attributed_file TEXT,
                score REAL,
                runner_up_file TEXT,
                runner_up_score REAL,
                signals TEXT,
                algo_version TEXT NOT NULL,
                user_label TEXT,
                labeled_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        for table, col, typ in MIGRATIONS:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # column already exists
        for table, old, new in RENAMES:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if old in cols and new not in cols:
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_prompts_session_msg "
            "ON raw_prompts(session_id, message_index)"
        )


# --- Files ---


def read_file_lines(
    file_path: str, line_start: int | None = None, line_end: int | None = None
) -> str | None:
    """Read the actual code from disk, optionally slicing to a line range.
    Returns None if file doesn't exist."""
    if not os.path.isfile(file_path):
        return None
    with open(file_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    if line_start is not None and line_end is not None:
        lines = lines[max(0, line_start - 1) : line_end]
    return "".join(lines)


def get_current_git_hash() -> str | None:
    """Get the current HEAD commit hash."""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    except FileNotFoundError:
        return None


def get_or_create_file(filepath: str) -> int:
    """Ensure a file exists in the files table, return its id."""
    with _conn() as conn:
        row = conn.execute("SELECT id FROM files WHERE filepath = ?", (filepath,)).fetchone()
        if row:
            return row["id"]
        git_hash = get_current_git_hash()
        cursor = conn.execute(
            "INSERT INTO files (filepath, last_git_hash) VALUES (?, ?)",
            (filepath, git_hash),
        )
        return cursor.lastrowid


# --- Questions ---


def insert_questions(
    file_path: str,
    questions: list[dict],
    prompt_version: str | None = None,
    seed_query_ids: list[int] | None = None,
    seed_query_source: str | None = None,
) -> list[int]:
    """Write generated questions to the DB.
    Each question dict should have: question_text, choices (list[str]), expected_answer,
    line_start (optional), line_end (optional).
    Pass seed_query_ids/seed_query_source when these questions were seeded by prior user queries.
    Returns list of inserted question ids."""
    file_id = get_or_create_file(file_path)
    seeds_json = json.dumps(seed_query_ids) if seed_query_ids else None
    ids = []
    with _conn() as conn:
        for q in questions:
            choices_json = json.dumps(q["choices"]) if "choices" in q else None
            cursor = conn.execute(
                """INSERT INTO questions (file_id, file_path, line_start, line_end, question_text, choices, expected_answer, prompt_version, seed_query_ids, seed_query_source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    file_id,
                    file_path,
                    q.get("line_start"),
                    q.get("line_end"),
                    q["question_text"],
                    choices_json,
                    q["expected_answer"],
                    prompt_version,
                    seeds_json,
                    seed_query_source,
                ),
            )
            ids.append(cursor.lastrowid)
    return ids


def get_unlabeled_questions(limit: int = 50) -> list[dict]:
    """Get questions that haven't been quality-labeled yet."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, file_path, question_text, choices, expected_answer, line_start, line_end, prompt_version
            FROM questions
            WHERE quality_flag IS NULL AND stale = FALSE
            ORDER BY file_path, id
            LIMIT ?
        """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_unasked_questions(limit: int = 10, file_path: str | None = None) -> list[dict]:
    """Get unasked questions, optionally filtered to a single file."""
    with _conn() as conn:
        if file_path:
            rows = conn.execute(
                f"""
                SELECT id, file_path, question_text, choices, expected_answer, line_start, line_end, seed_query_ids, seed_query_source
                FROM questions
                WHERE answered_at IS NULL AND stale = FALSE AND file_path = ?
                ORDER BY {_SEED_BIAS_SQL}
                LIMIT ?
            """,
                (file_path, limit),
            ).fetchall()
        else:
            # Pick one biased-random unasked question per file, then biased-shuffle across files and limit
            rows = conn.execute(
                f"""
                SELECT id, file_path, question_text, choices, expected_answer, line_start, line_end, seed_query_ids, seed_query_source
                FROM questions
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id, file_path, ROW_NUMBER() OVER (
                            PARTITION BY file_path ORDER BY {_SEED_BIAS_SQL}
                        ) as rn
                        FROM questions
                        WHERE answered_at IS NULL AND stale = FALSE
                    ) WHERE rn = 1
                )
                ORDER BY {_SEED_BIAS_SQL}
                LIMIT ?
            """,
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def mark_presented(question_id: int):
    """Stamp presented_at when a question is displayed (before the user answers)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE questions SET presented_at = CURRENT_TIMESTAMP WHERE id = ?",
            (question_id,),
        )


def grade_question(question_id: int, user_answer: str, correct: bool):
    """Record the user's answer and grade for a question."""
    with _conn() as conn:
        conn.execute(
            "UPDATE questions SET answered_at = CURRENT_TIMESTAMP, user_answer = ?, graded_correct = ? WHERE id = ?",
            (user_answer, correct, question_id),
        )


def mark_golden(question_id: int):
    """Mark a question as golden (validated + representative)."""
    with _conn() as conn:
        conn.execute("UPDATE questions SET golden = TRUE WHERE id = ?", (question_id,))


def get_golden_counts() -> dict[str, int]:
    """Return {file_path: count} of golden non-stale questions per file."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT file_path, COUNT(*) as cnt FROM questions "
            "WHERE golden = TRUE AND stale = FALSE GROUP BY file_path"
        ).fetchall()
    return {row["file_path"]: row["cnt"] for row in rows}


def get_revalidation_questions(limit: int = 10, file_path: str | None = None) -> list[dict]:
    """Golden questions whose file changed, awaiting re-validation.

    These are surfaced first in the quiz so the user can re-earn (or retire)
    goldens that went stale, instead of losing them silently.
    """
    cols = (
        "id, file_path, question_text, choices, expected_answer, line_start, "
        "line_end, seed_query_ids, seed_query_source, pending_revalidation"
    )
    with _conn() as conn:
        if file_path:
            rows = conn.execute(
                f"SELECT {cols} FROM questions "
                "WHERE pending_revalidation = TRUE AND file_path = ? ORDER BY id LIMIT ?",
                (file_path, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {cols} FROM questions "
                "WHERE pending_revalidation = TRUE ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def reinstate_golden(question_id: int):
    """Re-validated: clear stale + pending flag, keep golden so it counts again."""
    with _conn() as conn:
        conn.execute(
            "UPDATE questions SET stale = FALSE, pending_revalidation = FALSE WHERE id = ?",
            (question_id,),
        )


def discard_revalidation(question_id: int):
    """Failed re-validation: drop golden status and clear the pending flag (stays stale)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE questions SET golden = FALSE, pending_revalidation = FALSE WHERE id = ?",
            (question_id,),
        )


def flag_question(
    question_id: int, quality_flag: str, quality_note: str = "", reject_reason: str = ""
):
    """Flag a question as good or bad with an optional note and rejection reason."""
    with _conn() as conn:
        conn.execute(
            "UPDATE questions SET quality_flag = ?, quality_note = ?, reject_reason = ? WHERE id = ?",
            (quality_flag, quality_note, reject_reason, question_id),
        )


def get_file_coverage() -> list[dict]:
    """Get correct non-stale answer counts per file that has been quizzed."""
    with _conn() as conn:
        rows = conn.execute("""
            SELECT file_path, COUNT(*) as correct
            FROM questions
            WHERE graded_correct = TRUE AND stale = FALSE
            GROUP BY file_path
        """).fetchall()
    return [dict(r) for r in rows]


# --- Raw prompts ---


def insert_raw_prompt(
    prompt_text: str, session_id: str = "", is_conceptual: bool | None = None
) -> int:
    with _conn() as conn:
        cursor = conn.execute(
            "INSERT INTO raw_prompts (prompt_text, session_id, is_conceptual) VALUES (?, ?, ?)",
            (prompt_text, session_id, is_conceptual),
        )
        return cursor.lastrowid


def get_conceptual_prompts(limit: int = 50) -> list[dict]:
    """Return captured prompts the classifier flagged as conceptual, oldest first."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, session_id, prompt_text FROM raw_prompts "
            "WHERE is_conceptual = TRUE ORDER BY timestamp ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_unlabeled_prompts(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, prompt_text FROM raw_prompts WHERE label IS NULL ORDER BY timestamp ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def label_prompt(prompt_id: int, label: str, note: str = ""):
    with _conn() as conn:
        conn.execute(
            "UPDATE raw_prompts SET label = ?, label_note = ? WHERE id = ?",
            (label, note, prompt_id),
        )


# --- File summaries ---


def upsert_summary(
    file_path: str, content_hash: str, summary_text: str, prompt_version: str | None = None
):
    """Insert or replace the cached summary for a file."""
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO file_summaries (file_path, content_hash, summary_text, generated_at, prompt_version)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                content_hash = excluded.content_hash,
                summary_text = excluded.summary_text,
                generated_at = CURRENT_TIMESTAMP,
                prompt_version = excluded.prompt_version
        """,
            (file_path, content_hash, summary_text, prompt_version),
        )


def get_summary(file_path: str) -> dict | None:
    """Return the stored summary row for a file, or None if no summary exists."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT file_path, content_hash, summary_text, generated_at, prompt_version FROM file_summaries WHERE file_path = ?",
            (file_path,),
        ).fetchone()
    return dict(row) if row else None


def delete_summaries_not_in(keep_paths: list[str]) -> int:
    """Drop file_summaries rows whose file_path isn't in keep_paths. Returns row count deleted."""
    with _conn() as conn:
        existing = {r["file_path"] for r in conn.execute("SELECT file_path FROM file_summaries")}
        orphans = existing - set(keep_paths)
        if not orphans:
            return 0
        placeholders = ",".join("?" * len(orphans))
        conn.execute(
            f"DELETE FROM file_summaries WHERE file_path IN ({placeholders})", tuple(orphans)
        )
    return len(orphans)


# --- Reviews ---


def start_review(file_path: str, transcript_path: str | None = None) -> int:
    """Begin a new review session, return its id."""
    with _conn() as conn:
        cursor = conn.execute(
            "INSERT INTO reviews (file_path, transcript_path) VALUES (?, ?)",
            (file_path, transcript_path),
        )
        return cursor.lastrowid


def end_review(
    review_id: int,
    agent_summary: str | None = None,
    notes: str | None = None,
    transcript_path: str | None = None,
):
    """Mark the review as ended and record the subagent's summary + any late-arriving transcript path."""
    with _conn() as conn:
        conn.execute(
            """
            UPDATE reviews
            SET ended_at = CURRENT_TIMESTAMP,
                agent_summary = COALESCE(?, agent_summary),
                notes = COALESCE(?, notes),
                transcript_path = COALESCE(?, transcript_path)
            WHERE id = ?
        """,
            (agent_summary, notes, transcript_path, review_id),
        )


def get_reviews_for_file(file_path: str) -> list[dict]:
    """Return all reviews for a file, most recent first."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, file_path, started_at, ended_at, transcript_path, agent_summary, notes
            FROM reviews
            WHERE file_path = ?
            ORDER BY started_at DESC
        """,
            (file_path,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Review questions ---


def log_study_question(
    review_id: int,
    question_text: str,
    was_conceptual: bool = True,
    answered_satisfactorily: bool | None = None,
) -> int:
    """Record a question the user asked during a study session."""
    with _conn() as conn:
        cursor = conn.execute(
            "INSERT INTO review_questions (review_id, question_text, was_conceptual, answered_satisfactorily) VALUES (?, ?, ?, ?)",
            (review_id, question_text, was_conceptual, answered_satisfactorily),
        )
        return cursor.lastrowid


def get_study_questions(review_id: int) -> list[dict]:
    """Return questions logged against a specific review."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, review_id, question_text, was_conceptual, answered_satisfactorily, created_at
            FROM review_questions
            WHERE review_id = ?
            ORDER BY created_at ASC
        """,
            (review_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conceptual_study_questions_for_file(file_path: str, limit: int = 50) -> list[dict]:
    """Return conceptual questions the user asked across all /shikhu-study sessions for this file, oldest first."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT rq.id, rq.review_id, rq.question_text, rq.answered_satisfactorily, rq.created_at
            FROM review_questions rq
            JOIN reviews r ON rq.review_id = r.id
            WHERE r.file_path = ? AND rq.was_conceptual = TRUE
            ORDER BY rq.created_at ASC
            LIMIT ?
        """,
            (file_path, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Attribution labels ---


def insert_attribution_label(query_id: int, query_source: str, result) -> int:
    """Persist an AttributionResult against a query (raw_prompts or review_questions row)."""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO attribution_labels "
            "(query_id, query_source, attributed_file, score, runner_up_file, runner_up_score, signals, algo_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                query_id,
                query_source,
                result.attributed_file,
                result.score,
                result.runner_up_file,
                result.runner_up_score,
                json.dumps(result.signals),
                result.algo_version,
            ),
        )
        return cur.lastrowid


def get_attribution_label(query_id: int, query_source: str) -> dict | None:
    """Return the most recent attribution label for a query, or None."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM attribution_labels "
            "WHERE query_id = ? AND query_source = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (query_id, query_source),
        ).fetchone()
    return dict(row) if row else None


def set_attribution_user_label(label_id: int, user_label: str) -> None:
    """Record the user's confirm/correct verdict on an attribution."""
    with _conn() as conn:
        conn.execute(
            "UPDATE attribution_labels SET user_label = ?, labeled_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user_label, label_id),
        )


# --- Seed lookups ---


def get_seed_texts(seed_ids: list[int], source: str) -> list[dict]:
    """Fetch the original text of seed queries that drove a seeded question's generation."""
    if not seed_ids:
        return []
    placeholders = ",".join("?" * len(seed_ids))
    with _conn() as conn:
        if source == "review_questions":
            rows = conn.execute(
                f"SELECT id, question_text AS text FROM review_questions WHERE id IN ({placeholders}) ORDER BY id DESC",
                seed_ids,
            ).fetchall()
        elif source == "raw_prompts":
            rows = conn.execute(
                f"SELECT id, prompt_text AS text FROM raw_prompts WHERE id IN ({placeholders}) ORDER BY id DESC",
                seed_ids,
            ).fetchall()
        else:
            rows = []
    return [dict(r) for r in rows]
