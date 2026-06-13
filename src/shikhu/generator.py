### Quiz generation Code

import os
import shutil
import time
import tomllib
from pathlib import Path

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from shikhu.store import read_file_lines

# Load .env at import so INCEPTION_API_KEY is present before any request is made.
load_dotenv()

REQUEST_TIMEOUT = 120  # seconds — one hung connection must not stall a whole refresh


def _api_key() -> str:
    key = os.environ.get("INCEPTION_API_KEY")
    if not key:
        raise RuntimeError(
            "INCEPTION_API_KEY is not set — add it to a .env file in this repo or export it"
        )
    return key


def _check_response(response: requests.Response) -> dict:
    """Return the parsed JSON body, raising a readable error on API failure."""
    if response.status_code != 200:
        raise RuntimeError(
            f"Mercury API error (HTTP {response.status_code}): {response.text[:300]}"
        )
    return response.json()


# classes
class MCQuestion(BaseModel):
    question: str
    choices: list[str] = Field(min_length=4, max_length=4)
    correct_index: int = Field(ge=0, le=3)


class Quiz(BaseModel):
    questions: list[MCQuestion]


# --- prompt loading ---

# Prompts ship inside the package so the tool works from any cwd once installed.
PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPTS_FILE = PROMPTS_DIR / "prompts.toml"
ARCHIVE_DIR = PROMPTS_DIR / "archive"


def load_prompts() -> dict:
    """Load prompts from TOML. Auto-archives when version changes (dev only)."""
    with open(PROMPTS_FILE, "rb") as f:
        prompts = tomllib.load(f)

    # auto-archive: check if this version already has an archive
    version = prompts["version"]
    archive_path = ARCHIVE_DIR / f"prompts_{version}.toml"
    if not archive_path.exists():
        try:
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROMPTS_FILE, archive_path)
        except OSError:
            pass  # installed read-only (site-packages) — archiving is a dev convenience

    return prompts


_prompts = load_prompts()

PROMPT_VERSION = _prompts["version"]
CONCEPTUAL_QUESTION_DEF = _prompts["conceptual_definition"]["text"].strip()
FILE_PROMPT = _prompts["file_prompt"]["text"].strip()
STUDY_SEED_PROMPT = _prompts["study_seed_prompt"]["text"].strip()
SUMMARY_PROMPT = _prompts["summary_prompt"]["text"].strip()

RESPONSE_SCHEMA = {
    "name": "Quiz",
    "strict": True,
    "schema": Quiz.model_json_schema(),
}


# request wrapper
def generate_quiz(prompt: str, max_tokens: int = 2000) -> tuple[Quiz, dict]:
    """Send a prompt to Mercury with structured output, return parsed Quiz and usage stats."""
    start = time.time()
    response = requests.post(
        "https://api.inceptionlabs.ai/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_api_key()}",
        },
        json={
            "model": "mercury-2",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": RESPONSE_SCHEMA,
            },
            "max_tokens": max_tokens,
            "reasoning_effort": "instant",
        },
        timeout=REQUEST_TIMEOUT,
    )
    elapsed = time.time() - start
    data = _check_response(response)
    content = data["choices"][0]["message"]["content"]
    quiz = Quiz.model_validate_json(content)
    usage = data["usage"]
    stats = {
        "elapsed": elapsed,
        "completion_tokens": usage["completion_tokens"],
        "prompt_tokens": usage["prompt_tokens"],
        "tokens_per_sec": usage["completion_tokens"] / elapsed,
    }
    return quiz, stats


# context builders
def build_context_from_file(file_path: str) -> dict | None:
    """Read an entire file from disk, return structured context for prompt construction."""
    code = read_file_lines(file_path)
    if code is None:
        return None
    return {
        "file_path": file_path,
        "code": code,
    }


# question generation
def generate_question_from_file(file_path, num_questions: int = 5) -> tuple[Quiz, dict] | None:
    context = build_context_from_file(file_path)
    if context is None:
        return None

    prompt = (
        f"{CONCEPTUAL_QUESTION_DEF}\n\n"
        f"{FILE_PROMPT.format(n=num_questions)}\n\n"
        f"File: {context['file_path']}\n\n"
        f"{context['code']}"
    )

    return generate_quiz(prompt)


def generate_questions_from_study_seeds(
    file_path: str,
    num_questions: int = 3,
) -> tuple[Quiz, dict, list[int]] | None:
    """Generate quiz questions seeded by the user's prior /study questions for this file.

    Returns (quiz, stats, seed_review_question_ids) or None if no seeds / file missing."""
    from shikhu.store import get_conceptual_study_questions_for_file

    seeds = get_conceptual_study_questions_for_file(file_path)
    if not seeds:
        return None

    context = build_context_from_file(file_path)
    if context is None:
        return None

    seed_block = "\n".join(f"{i + 1}. {s['question_text']}" for i, s in enumerate(seeds))

    prompt = (
        f"{CONCEPTUAL_QUESTION_DEF}\n\n"
        f"{STUDY_SEED_PROMPT.format(n=num_questions, seed_questions=seed_block)}\n\n"
        f"File: {context['file_path']}\n\n"
        f"{context['code']}"
    )

    quiz, stats = generate_quiz(prompt)
    return quiz, stats, [s["id"] for s in seeds]


def generate_summary(file_path: str, max_tokens: int = 600) -> tuple[str, dict] | None:
    """Call Mercury to produce a prose summary of a file. Returns (summary, stats) or None if file missing."""
    context = build_context_from_file(file_path)
    if context is None:
        return None

    prompt = f"{SUMMARY_PROMPT}\n\nFile: {context['file_path']}\n\n{context['code']}"

    start = time.time()
    response = requests.post(
        "https://api.inceptionlabs.ai/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_api_key()}",
        },
        json={
            "model": "mercury-2",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "reasoning_effort": "instant",
        },
        timeout=REQUEST_TIMEOUT,
    )
    elapsed = time.time() - start
    data = _check_response(response)
    content = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage", {})
    stats = {
        "elapsed": elapsed,
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
    }
    return content, stats


def _get_unasked_counts() -> dict[str, int]:
    """Return {file_path: count} of unasked non-stale questions per file."""
    from shikhu.store import _get_conn

    conn = _get_conn()
    rows = conn.execute(
        "SELECT file_path, COUNT(*) as cnt FROM questions "
        "WHERE answered_at IS NULL AND stale = FALSE GROUP BY file_path"
    ).fetchall()
    conn.close()
    return {row["file_path"]: row["cnt"] for row in rows}


def _quiz_to_rows(quiz: Quiz) -> list[dict]:
    """Convert a Quiz model to the row format insert_questions expects."""
    return [
        {
            "question_text": q.question,
            "choices": q.choices,
            "expected_answer": q.choices[q.correct_index],
        }
        for q in quiz.questions
    ]
