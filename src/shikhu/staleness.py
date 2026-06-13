"""Staleness detection via file content hashing.

Detects changes by hashing file contents. Python files are hashed over their
parsed AST (with docstrings stripped), so comment, whitespace, and docstring
edits don't invalidate questions — only code changes do. Every other file type
(and any .py file that fails to parse) falls back to a SHA-256 byte hash.
When a file's content changes, all non-stale questions for that file
are marked stale. No git dependency required.
"""

import ast
import hashlib

from shikhu.store import _get_conn


def _strip_docstrings(tree: ast.AST) -> None:
    """Remove docstring statements in place from modules, functions, and classes.

    Comments and formatting are never in the AST, but docstrings are, so a
    docstring edit would otherwise change the hash. Drop the leading string
    expression from each scope that can carry one.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:]


def _ast_hash(source: str) -> str:
    """SHA-256 over the parsed AST (docstrings stripped, no line/col attributes).

    Raises SyntaxError/ValueError if the source is not valid Python.
    """
    tree = ast.parse(source)
    _strip_docstrings(tree)
    return hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def compute_file_hash(filepath: str) -> str | None:
    """Content hash of a file. Returns None if the file is missing.

    Python files are hashed over their AST so comment/whitespace/docstring
    edits don't count as changes. Everything else — and any .py file that
    fails to parse — uses a SHA-256 byte hash.
    """
    try:
        with open(filepath, "rb") as f:
            content = f.read()
    except FileNotFoundError:
        return None

    if filepath.endswith(".py"):
        try:
            return _ast_hash(content.decode("utf-8"))
        except (SyntaxError, ValueError, UnicodeDecodeError):
            pass  # malformed Python — fall back to the byte hash below

    return hashlib.sha256(content).hexdigest()


def mark_stale_questions() -> int:
    """Check all files with questions and mark questions stale if content changed.

    For each file that has non-stale questions and a stored content_hash:
    1. Compute the current hash of the file on disk.
    2. If the hash matches, skip (nothing changed).
    3. If the hash differs (or file is missing), mark ALL non-stale questions stale.
    4. Update the stored content_hash to the current value.

    Returns the number of questions marked stale.
    """
    conn = _get_conn()

    files = conn.execute("""
        SELECT f.id, f.filepath, f.content_hash
        FROM files f
        WHERE f.content_hash IS NOT NULL
        AND EXISTS (SELECT 1 FROM questions q WHERE q.file_id = f.id AND q.stale = FALSE)
    """).fetchall()

    stale_count = 0

    for file in files:
        current_hash = compute_file_hash(file["filepath"])

        if current_hash == file["content_hash"]:
            continue

        # File changed (or was deleted) — mark all non-stale questions stale.
        # Golden ones also enter pending re-validation so they can be re-earned
        # rather than silently dropping out of coverage (see store.reinstate_golden).
        result = conn.execute(
            "UPDATE questions SET stale = TRUE, "
            "pending_revalidation = CASE WHEN golden THEN TRUE ELSE pending_revalidation END "
            "WHERE file_id = ? AND stale = FALSE",
            (file["id"],),
        )
        stale_count += result.rowcount

        # Update stored hash (None if file was deleted)
        conn.execute(
            "UPDATE files SET content_hash = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?",
            (current_hash, file["id"]),
        )

    conn.commit()
    conn.close()
    return stale_count


if __name__ == "__main__":
    from shikhu.store import init_db

    init_db()
    count = mark_stale_questions()
    if count:
        print(f"Marked {count} question(s) as stale.")
    else:
        print("No questions marked stale — everything is up to date.")
