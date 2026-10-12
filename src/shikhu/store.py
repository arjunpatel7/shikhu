"""Shikhu persistence layer — SQLite CRUD on top of `sqlite3`.

Sections, in file order:
    Connection / schema    _conn (CM), _get_conn (legacy), init_db, MIGRATIONS
    Files                  get_or_create_file, read_file_lines, get_current_git_hash
    Questions              insert/get/grade/golden/flag/coverage helpers
    Attempts               insert_attempt, get_attempts: who answered what, and how it was graded
    File summaries         upsert_summary, get_summary, delete_summaries_not_in
    Reviews                start_review, end_review, get_reviews_for_file
    Review questions       log_study_question + readers for /shikhu-study sessions
    Attribution labels     insert/get/set helpers for prompt-to-file attribution
    Seed lookups           get_seed_texts
    Past prompts           add/get/save/count helpers behind `shikhu map`

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
    f"WHEN seed_query_ids IS NOT NULL AND COALESCE(reasked_at, created_at) >= datetime('now', '-{SEED_BIAS_RECENT_DAYS} days') THEN {SEED_WEIGHT_RECENT} "
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
    ("questions", "seed_query_ids", "TEXT"),
    ("questions", "seed_query_source", "TEXT"),
    ("questions", "created_at", "TIMESTAMP"),
    ("questions", "pending_revalidation", "BOOLEAN DEFAULT FALSE"),
    ("questions", "presented_at", "TIMESTAMP"),
    ("questions", "model", "TEXT"),
    ("questions", "commit_sha", "TEXT"),
    ("questions", "reasked_at", "TIMESTAMP"),
    ("questions", "length_cue", "BOOLEAN DEFAULT FALSE"),
    ("past_prompts", "quiz_question_id", "INTEGER REFERENCES questions(id)"),
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

            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL REFERENCES questions(id),
                respondent TEXT NOT NULL,
                answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                mode TEXT NOT NULL,
                answer_text TEXT,
                correct BOOLEAN,
                score REAL,
                graded_by TEXT,
                condition TEXT,
                run_id TEXT,
                meta TEXT,
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id);
            CREATE INDEX IF NOT EXISTS idx_attempts_run ON attempts(run_id);

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

            -- Files a question needs beyond its lead one (questions.file_path). Only the extras
            -- live here; question_links is the union, so every per-file query counts a
            -- cross-file question under each file it touches.
            CREATE TABLE IF NOT EXISTS question_files (
                question_id INTEGER NOT NULL REFERENCES questions(id),
                file_path TEXT NOT NULL,
                PRIMARY KEY (question_id, file_path)
            );
            CREATE INDEX IF NOT EXISTS idx_question_files_path ON question_files(file_path);

            -- Every file an inquiry spanned, beyond the one its review is filed under.
            CREATE TABLE IF NOT EXISTS review_files (
                review_id INTEGER NOT NULL REFERENCES reviews(id),
                file_path TEXT NOT NULL,
                PRIMARY KEY (review_id, file_path)
            );

            -- Prompts from past Claude Code transcripts, for `shikhu map`. One row per
            -- distinct prompt; scores stay NULL until Jev has judged it. These are imported:
            -- nobody confirmed them, so they never count as confirmed inquiries.
            CREATE TABLE IF NOT EXISTS past_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text_key TEXT NOT NULL UNIQUE,
                prompt_text TEXT NOT NULL,
                session_id TEXT,
                transcript_path TEXT,
                asked_at TIMESTAMP,
                context TEXT,
                conceptual REAL,
                answerable REAL,
                algo_version TEXT,
                scored_at TIMESTAMP,
                -- The quiz question this past question seeded or was matched to. Past
                -- questions are used once, like inquiry seeds; a stale question frees it.
                quiz_question_id INTEGER REFERENCES questions(id)
            );

            -- Every file scored at or above the show floor for a past prompt. Raw scores,
            -- so the map's thresholds can move without re-scoring.
            CREATE TABLE IF NOT EXISTS past_prompt_files (
                prompt_id INTEGER NOT NULL REFERENCES past_prompts(id),
                file_path TEXT NOT NULL,
                score REAL NOT NULL,
                PRIMARY KEY (prompt_id, file_path)
            );
            CREATE INDEX IF NOT EXISTS idx_past_prompt_files_path ON past_prompt_files(file_path);

            CREATE VIEW IF NOT EXISTS question_links AS
                SELECT id AS question_id, file_path FROM questions
                UNION
                SELECT question_id, file_path FROM question_files;
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
        _backfill_human_attempts(conn)


def _backfill_human_attempts(conn: sqlite3.Connection) -> None:
    """Carry each already-answered question over as one human attempt.

    The questions table only ever held the LATEST answer, so this is the whole history that
    exists. Idempotent — init_db runs on every command — because it skips any question that
    already has a human attempt, which also means a later re-answer (which writes its own
    attempt) never gets a stale backfill row alongside it.

    Skips quietly if the questions table predates the answer columns: init_db runs on every
    command, so anything that raised here would break shikhu for that user entirely."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    if not {"answered_at", "user_answer", "graded_correct"} <= have:
        return
    conn.execute("""
        INSERT INTO attempts (question_id, respondent, answered_at, mode, answer_text,
                              correct, graded_by, run_id)
        SELECT q.id, 'human', q.answered_at, 'choice', q.user_answer,
               q.graded_correct, 'key', 'backfill'
        FROM questions q
        WHERE q.answered_at IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM attempts a WHERE a.question_id = q.id AND a.respondent = 'human'
          )
    """)


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
    model: str | None = None,
    content_hash: str | None = None,
    commit_sha: str | None = None,
    extra_files: dict[str, str | None] | None = None,
) -> list[int]:
    """Write generated questions to the DB.
    Each question dict should have: question_text, choices (list[str]), expected_answer,
    line_start (optional), line_end (optional).
    Pass seed_query_ids/seed_query_source when these questions were seeded by prior user queries.
    Pass model to record which model generated them.
    Pass content_hash (staleness.compute_file_hash of the content the questions were generated
    from) to set the file's staleness baseline; callers should run mark_stale_questions first
    so older questions are staled against the previous baseline before it moves.
    Pass commit_sha (staleness.pin_commit) to record the commit at which the file still matches
    that content, so the question can be reproduced there after the working copy moves on. Leave
    it None unless verified — it is never filled in from HEAD blindly.
    Pass extra_files ({path: content_hash}) when the questions need other files too: each is
    linked to every question, so per-file counts and staleness cover all of them. A linked
    file's staleness baseline is set from its hash only if it has none — an existing baseline
    is never moved, or edits since it was recorded would stop staling that file's questions.
    Returns list of inserted question ids."""
    extra_files = {p: h for p, h in (extra_files or {}).items() if p != file_path}
    extra_ids = {p: get_or_create_file(p) for p in extra_files}
    file_id = get_or_create_file(file_path)
    seeds_json = json.dumps(seed_query_ids) if seed_query_ids else None
    ids = []
    with _conn() as conn:
        if content_hash is not None:
            conn.execute(
                "UPDATE files SET content_hash = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?",
                (content_hash, file_id),
            )
        for p, h in extra_files.items():
            if h is not None:
                conn.execute(
                    "UPDATE files SET content_hash = ?, last_updated = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND content_hash IS NULL",
                    (h, extra_ids[p]),
                )
        for q in questions:
            choices_json = json.dumps(q["choices"]) if "choices" in q else None
            cursor = conn.execute(
                """INSERT INTO questions (file_id, file_path, line_start, line_end, question_text, choices, expected_answer, prompt_version, seed_query_ids, seed_query_source, model, commit_sha, length_cue, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
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
                    model,
                    commit_sha,
                    bool(q.get("length_cue")),
                ),
            )
            ids.append(cursor.lastrowid)
            for p in extra_files:
                conn.execute(
                    "INSERT OR IGNORE INTO question_files (question_id, file_path) VALUES (?, ?)",
                    (cursor.lastrowid, p),
                )
    return ids


def get_question_files(question_ids: list[int]) -> dict[int, list[str]]:
    """{question_id: every file it touches, lead file first} — for showing cross-file questions."""
    if not question_ids:
        return {}
    marks = ",".join("?" * len(question_ids))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT q.id, q.file_path AS lead, f.file_path AS extra FROM questions q "
            f"LEFT JOIN question_files f ON f.question_id = q.id WHERE q.id IN ({marks}) "
            f"ORDER BY f.file_path",
            tuple(question_ids),
        ).fetchall()
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r["id"], [r["lead"]])
        if r["extra"]:
            out[r["id"]].append(r["extra"])
    return out


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


def _paths_filter(file_paths: list[str] | None) -> tuple[str, tuple]:
    """SQL fragment + params restricting to file_paths. None means no restriction."""
    if file_paths is None:
        return "", ()
    if not file_paths:
        return " AND 0", ()  # explicit empty scope matches nothing
    marks = ",".join("?" * len(file_paths))
    # via question_links, so a cross-file question is in scope when ANY of its files is
    return (
        f" AND id IN (SELECT question_id FROM question_links WHERE file_path IN ({marks}))",
        tuple(file_paths),
    )


def get_unasked_questions(limit: int = 10, file_paths: list[str] | None = None) -> list[dict]:
    """Get unasked questions, optionally restricted to file_paths.

    A single file returns its questions in biased order; otherwise one biased-random
    question per file is picked, then biased-shuffled across files and limited."""
    cols = "id, file_path, question_text, choices, expected_answer, line_start, line_end, seed_query_ids, seed_query_source"
    scope, params = _paths_filter(file_paths)
    with _conn() as conn:
        if file_paths is not None and len(file_paths) == 1:
            rows = conn.execute(
                f"""
                SELECT {cols}
                FROM questions
                WHERE answered_at IS NULL AND stale = FALSE{scope}
                ORDER BY {_SEED_BIAS_SQL}
                LIMIT ?
            """,
                (*params, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT {cols}
                FROM questions
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id, file_path, ROW_NUMBER() OVER (
                            PARTITION BY file_path ORDER BY {_SEED_BIAS_SQL}
                        ) as rn
                        FROM questions
                        WHERE answered_at IS NULL AND stale = FALSE{scope}
                    ) WHERE rn = 1
                )
                ORDER BY {_SEED_BIAS_SQL}
                LIMIT ?
            """,
                (*params, limit),
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
    """Record the user's answer and grade for a question.

    Updates the question's latest-answer columns (what coverage reads) AND appends a human
    attempt, because those columns are overwritten by every re-answer and keep no history.
    Both happen in one transaction: a second connection would block on this one's write lock."""
    with _conn() as conn:
        conn.execute(
            "UPDATE questions SET answered_at = CURRENT_TIMESTAMP, user_answer = ?, graded_correct = ? WHERE id = ?",
            (user_answer, correct, question_id),
        )
        _insert_attempt(
            conn,
            question_id,
            "human",
            "choice",
            answer_text=user_answer,
            correct=correct,
            graded_by="key",
        )


def mark_golden(question_id: int):
    """Mark a question as golden (validated + representative)."""
    with _conn() as conn:
        conn.execute("UPDATE questions SET golden = TRUE WHERE id = ?", (question_id,))


def get_golden_counts() -> dict[str, int]:
    """Return {file_path: count} of golden non-stale questions per file."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT l.file_path, COUNT(*) as cnt FROM questions q "
            "JOIN question_links l ON l.question_id = q.id "
            "WHERE q.golden = TRUE AND q.stale = FALSE GROUP BY l.file_path"
        ).fetchall()
    return {row["file_path"]: row["cnt"] for row in rows}


def get_revalidation_questions(limit: int = 10, file_paths: list[str] | None = None) -> list[dict]:
    """Golden questions whose file changed, awaiting re-validation.

    These are surfaced first in the quiz so the user can re-earn (or retire)
    goldens that went stale, instead of losing them silently.
    """
    cols = (
        "id, file_path, question_text, choices, expected_answer, line_start, "
        "line_end, seed_query_ids, seed_query_source, pending_revalidation"
    )
    scope, params = _paths_filter(file_paths)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {cols} FROM questions "
            f"WHERE pending_revalidation = TRUE{scope} ORDER BY id LIMIT ?",
            (*params, limit),
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


# --- Attempts ---
#
# One row per answer, from anyone. `respondent` is "human" or a harness id, so a person and an
# agent answering the same question never share a slot (questions.graded_correct is a single
# latest-answer cell and would let an agent overwrite the user's result, moving their coverage).
# Coverage still reads the questions table; attempts is the record, not yet the source of truth.


def _insert_attempt(
    conn: sqlite3.Connection,
    question_id: int,
    respondent: str,
    mode: str,
    answer_text: str | None = None,
    correct: bool | None = None,
    score: float | None = None,
    graded_by: str | None = None,
    condition: str | None = None,
    run_id: str | None = None,
    meta: dict | None = None,
    error: str | None = None,
) -> int:
    cursor = conn.execute(
        """INSERT INTO attempts (question_id, respondent, mode, answer_text, correct, score,
                                 graded_by, condition, run_id, meta, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            question_id,
            respondent,
            mode,
            answer_text,
            correct,
            score,
            graded_by,
            condition,
            run_id,
            json.dumps(meta) if meta else None,
            error,
        ),
    )
    return cursor.lastrowid


def insert_attempt(
    question_id: int,
    respondent: str,
    mode: str,
    answer_text: str | None = None,
    correct: bool | None = None,
    score: float | None = None,
    graded_by: str | None = None,
    condition: str | None = None,
    run_id: str | None = None,
    meta: dict | None = None,
    error: str | None = None,
) -> int:
    """Record one answer. Returns the attempt id.

    respondent  "human", or a harness id such as "claude-code/haiku-4.5".
    mode        "choice" (picked an option; graded against the key) or "open" (free text).
    score       the grader's probability for open answers; None for choice.
    graded_by   "key" for choice, or the grader id for open answers.
    condition   what the respondent could see, e.g. "no-code", "file", "repo".
    run_id      groups the attempts of one benchmark run.
    meta        free-form run stats (tokens, cost, tool calls, seconds) stored as JSON.
    error       set when the respondent failed to produce an answer; such an attempt is wrong
                but should be reported separately from a wrong answer."""
    with _conn() as conn:
        return _insert_attempt(
            conn,
            question_id,
            respondent,
            mode,
            answer_text,
            correct,
            score,
            graded_by,
            condition,
            run_id,
            meta,
            error,
        )


def get_attempts(
    question_id: int | None = None,
    respondent: str | None = None,
    run_id: str | None = None,
) -> list[dict]:
    """Attempts, oldest first, optionally narrowed by question, respondent or run."""
    clauses, args = [], []
    for column, value in (
        ("question_id", question_id),
        ("respondent", respondent),
        ("run_id", run_id),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            args.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM attempts {where} ORDER BY answered_at, id", args
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d["meta"]) if d["meta"] else None
        out.append(d)
    return out


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


def add_review_files(review_id: int, file_paths: list[str]) -> None:
    """Record the files an inquiry spanned beyond the review's own file."""
    with _conn() as conn:
        for p in file_paths:
            conn.execute(
                "INSERT OR IGNORE INTO review_files (review_id, file_path) VALUES (?, ?)",
                (review_id, p),
            )


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


def _consumed_seed_ids(conn: sqlite3.Connection) -> set[int]:
    """Seeds that already have a quiz question. Stale questions don't count: once the code a
    question was written about has changed, its seed should produce a fresh one."""
    rows = conn.execute(
        "SELECT seed_query_ids FROM questions "
        "WHERE seed_query_source = 'review_questions' AND seed_query_ids IS NOT NULL "
        "AND stale = FALSE"
    ).fetchall()
    return {i for r in rows for i in json.loads(r["seed_query_ids"])}


def get_conceptual_study_questions_for_file(
    file_path: str, limit: int = 50, unconsumed: bool = False
) -> list[dict]:
    """Return conceptual questions the user asked across all /shikhu-study sessions for this file, oldest first.

    unconsumed=True drops seeds that already have a non-stale quiz question, so generating again
    does not write near-duplicates of questions you already have."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT rq.id, rq.review_id, rq.question_text, rq.answered_satisfactorily, rq.created_at
            FROM review_questions rq
            JOIN reviews r ON rq.review_id = r.id
            WHERE r.file_path = ? AND rq.was_conceptual = TRUE
              -- Explicitly unsatisfactory questions are poor seeds: for a /shikhu-study
              -- question the answer was inadequate, and for a detected inquiry the user
              -- said this file was the wrong place. NULL means "not judged" and still counts.
              AND (rq.answered_satisfactorily IS NULL OR rq.answered_satisfactorily != 0)
            ORDER BY rq.created_at ASC
            LIMIT ?
        """,
            (file_path, -1 if unconsumed else limit),  # -1: no limit, applied after filtering
        ).fetchall()
        seeds = [dict(r) for r in rows]
        if unconsumed:
            consumed = _consumed_seed_ids(conn)
            seeds = [s for s in seeds if s["id"] not in consumed][:limit]
        for s in seeds:  # other files the same inquiry spanned
            s["extra_files"] = [
                r["file_path"]
                for r in conn.execute(
                    "SELECT file_path FROM review_files WHERE review_id = ? ORDER BY file_path",
                    (s["review_id"],),
                )
            ]
    return seeds


def get_requeueable_questions(file_path: str) -> list[dict]:
    """Questions for this file (lead or linked) that are fresh and still unanswered: the ones a
    re-asked question can be matched to, because answering them is still ahead of the user."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT q.id, q.question_text, q.expected_answer FROM questions q
               JOIN question_links l ON l.question_id = q.id
               WHERE l.file_path = ? AND q.stale = FALSE AND q.answered_at IS NULL
               ORDER BY q.id""",
            (file_path,),
        ).fetchall()
    return [dict(r) for r in rows]


def requeue_question(
    question_id: int,
    seed_ids: list[int],
    source: str = "review_questions",
    asked_at: str | None = None,
) -> None:
    """Record that the user asked about this question's concept again.

    A new inquiry joins the question's seeds when they come from the same table, so it counts
    as consumed; seeds from different tables are never mixed. `reasked_at` moves the question
    into the recent-seed window so quizzes favor it. It is when the question was asked: now
    for a live inquiry, the transcript time for a past one, so an old question doesn't jump
    the queue. Asking again is not credit: it earns nothing until the question is answered."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT seed_query_ids, seed_query_source FROM questions WHERE id = ?", (question_id,)
        ).fetchone()
        if row is None:
            return
        when = "COALESCE(datetime(?), CURRENT_TIMESTAMP)"
        # Never move reasked_at backwards: an older past question can't undo a newer re-ask.
        reasked = f"MAX(COALESCE(reasked_at, ''), {when})"
        if row["seed_query_source"] in (None, source):
            seeds = json.loads(row["seed_query_ids"]) if row["seed_query_ids"] else []
            conn.execute(
                f"UPDATE questions SET seed_query_ids = ?, seed_query_source = ?, "
                f"reasked_at = {reasked} WHERE id = ?",
                (json.dumps(sorted({*seeds, *seed_ids})), source, asked_at, question_id),
            )
        else:  # seeded from something else: still favor it, just don't mix seed sources
            conn.execute(
                f"UPDATE questions SET reasked_at = {reasked} WHERE id = ?", (asked_at, question_id)
            )


# --- Attribution labels ---


def insert_attribution_label(query_id: int, query_source: str, result) -> int:
    """Persist an AttributionResult against a query (e.g. a review_questions row)."""
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
        elif source == "past_prompts":
            rows = conn.execute(
                f"SELECT id, prompt_text AS text FROM past_prompts WHERE id IN ({placeholders}) ORDER BY id DESC",
                seed_ids,
            ).fetchall()
        else:
            rows = []
    return [dict(r) for r in rows]


# --- Past prompts ---


def add_past_prompts(prompts) -> int:
    """Insert past prompts not already stored (keyed on normalized text). Returns how many
    were new. Scores on existing rows are left alone; their context is refreshed, since it
    comes free from the transcript.

    Unscored rows missing from this scan are dropped, so a stricter transcript filter takes
    effect without paying for prompts it now rejects. Scored rows stay: they were paid for."""
    from shikhu.transcripts import normalize

    keys = {normalize(p.text) for p in prompts}
    added = 0
    with _conn() as conn:
        stale = [
            r["id"]
            for r in conn.execute("SELECT id, text_key FROM past_prompts WHERE scored_at IS NULL")
            if r["text_key"] not in keys
        ]
        conn.executemany("DELETE FROM past_prompts WHERE id = ?", [(i,) for i in stale])
        for p in prompts:
            key = normalize(p.text)
            cur = conn.execute(
                "INSERT OR IGNORE INTO past_prompts "
                "(text_key, prompt_text, session_id, transcript_path, asked_at, context) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, p.text, p.session_id, p.transcript_path, p.asked_at, p.context),
            )
            added += cur.rowcount
            if not cur.rowcount:
                conn.execute(
                    "UPDATE past_prompts SET context = ? WHERE text_key = ?", (p.context, key)
                )
    return added


def get_unscored_past_prompts(algo_version: str) -> list[dict]:
    """Past prompts Jev hasn't judged under `algo_version` yet, oldest first. A change to how
    prompts are scored makes every earlier score count as unscored."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, prompt_text, context FROM past_prompts "
            "WHERE scored_at IS NULL OR algo_version IS NOT ? ORDER BY asked_at, id",
            (algo_version,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_past_prompt_score(
    prompt_id: int, conceptual: float, answerable: float, files: dict[str, float], algo_version: str
) -> None:
    """Store Jev's judgments for one past prompt."""
    with _conn() as conn:
        conn.execute(
            "UPDATE past_prompts SET conceptual = ?, answerable = ?, algo_version = ?, "
            "scored_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conceptual, answerable, algo_version, prompt_id),
        )
        conn.execute("DELETE FROM past_prompt_files WHERE prompt_id = ?", (prompt_id,))
        conn.executemany(
            "INSERT INTO past_prompt_files (prompt_id, file_path, score) VALUES (?, ?, ?)",
            [(prompt_id, path, score) for path, score in files.items()],
        )


def count_past_prompts(algo_version: str) -> dict:
    """{'total', 'scored'} past prompts stored; 'scored' means scored under `algo_version`."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN scored_at IS NOT NULL AND algo_version = ? THEN 1 ELSE 0 END) AS scored "
            "FROM past_prompts",
            (algo_version,),
        ).fetchone()
    return {"total": row["total"], "scored": row["scored"] or 0}


def get_past_questions_per_file(
    min_conceptual: float, min_answerable: float, min_file: float, algo_version: str
) -> tuple[dict[str, int], dict[str, int], int]:
    """Past questions per file: (every file credited, the file each question led with, total).

    A scored prompt is a question when Jev judged it conceptual and answerable from this
    repo. It credits every file scored at or above `min_file`, so a cross-file question
    counts under each file it touched. The lead counts give each question only to its
    top-scored file: a broad "how does it all fit together" touches many files without
    being about any one of them."""
    sql = """
        WITH credited AS (
            SELECT pf.prompt_id, pf.file_path,
                   ROW_NUMBER() OVER (
                       PARTITION BY pf.prompt_id ORDER BY pf.score DESC, pf.file_path
                   ) AS rank
            FROM past_prompt_files pf JOIN past_prompts p ON p.id = pf.prompt_id
            WHERE p.algo_version = :algo AND p.conceptual >= :c AND p.answerable >= :a
              AND pf.score >= :f
        )
    """
    params = {"algo": algo_version, "c": min_conceptual, "a": min_answerable, "f": min_file}
    with _conn() as conn:
        per_file = conn.execute(
            sql + "SELECT file_path, COUNT(*) AS n FROM credited GROUP BY file_path", params
        ).fetchall()
        lead = conn.execute(
            sql + "SELECT file_path, COUNT(*) AS n FROM credited WHERE rank = 1 GROUP BY file_path",
            params,
        ).fetchall()
        total = conn.execute(
            sql + "SELECT COUNT(DISTINCT prompt_id) FROM credited", params
        ).fetchone()[0]
    return (
        {r["file_path"]: r["n"] for r in per_file},
        {r["file_path"]: r["n"] for r in lead},
        total,
    )


def get_past_question_seeds(
    file_path: str,
    min_conceptual: float,
    min_answerable: float,
    min_file: float,
    algo_version: str,
    unconsumed: bool = True,
) -> list[dict]:
    """Past questions this file led with, oldest first, shaped like inquiry seeds.

    Only the lead file seeds from a past question, so a broad question doesn't seed every file
    it touched; the other files it scored on come along as `extra_files` for cross-file
    questions. unconsumed=True drops ones already behind a fresh quiz question."""
    with _conn() as conn:
        rows = conn.execute(
            """
            WITH credited AS (
                SELECT pf.prompt_id, pf.file_path, pf.score,
                       ROW_NUMBER() OVER (
                           PARTITION BY pf.prompt_id ORDER BY pf.score DESC, pf.file_path
                       ) AS rank
                FROM past_prompt_files pf JOIN past_prompts p ON p.id = pf.prompt_id
                WHERE p.algo_version = :algo AND p.conceptual >= :c AND p.answerable >= :a
                  AND pf.score >= :f
            )
            SELECT p.id, p.prompt_text AS question_text, p.asked_at, p.quiz_question_id,
                   (SELECT q.stale FROM questions q WHERE q.id = p.quiz_question_id) AS used_stale
            FROM credited c JOIN past_prompts p ON p.id = c.prompt_id
            WHERE c.file_path = :path AND c.rank = 1
            ORDER BY p.asked_at, p.id
            """,
            {
                "algo": algo_version,
                "c": min_conceptual,
                "a": min_answerable,
                "f": min_file,
                "path": file_path,
            },
        ).fetchall()
        seeds = []
        for r in rows:
            # A stale question was about code that has since changed, so its seed is free again.
            if unconsumed and r["quiz_question_id"] is not None and not r["used_stale"]:
                continue
            extra = conn.execute(
                "SELECT file_path FROM past_prompt_files WHERE prompt_id = ? AND file_path != ? "
                "AND score >= ? ORDER BY score DESC, file_path",
                (r["id"], file_path, min_file),
            ).fetchall()
            seeds.append(
                {
                    "id": r["id"],
                    "question_text": r["question_text"],
                    "asked_at": r["asked_at"],
                    "extra_files": [e["file_path"] for e in extra],
                }
            )
    return seeds


def mark_past_prompts_used(prompt_ids: list[int], question_id: int) -> None:
    """Record the quiz question these past questions seeded or were matched to."""
    with _conn() as conn:
        conn.executemany(
            "UPDATE past_prompts SET quiz_question_id = ? WHERE id = ?",
            [(question_id, pid) for pid in prompt_ids],
        )
