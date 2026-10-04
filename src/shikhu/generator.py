### Quiz generation Code

import os
import random
import time
import tomllib
from pathlib import Path

import requests
from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from shikhu.openrouter import (
    ERROR_SNIPPET,
    check_response,
    headers,
)
from shikhu.store import read_file_lines

# Load .env at import so OPENROUTER_API_KEY is present before any request is made.
# find_dotenv(usecwd=True): load_dotenv() otherwise searches upward from this
# installed package's own file location, not the user's working directory.
load_dotenv(find_dotenv(usecwd=True))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "inception/mercury-2.5"
REQUEST_TIMEOUT = 120  # seconds — one hung connection must not stall a whole refresh

# Auth, app attribution (APP_URL/APP_TITLE/APP_CATEGORIES) and the shared error shape
# now live in `openrouter`, since `systemone` needs the same three things.


def get_model() -> str:
    """Model id sent to OpenRouter. Read per call so .env and test overrides apply."""
    return os.environ.get("SHIKHU_MODEL") or DEFAULT_MODEL


def _check_response(response: requests.Response) -> dict:
    """Return the parsed JSON body, raising a readable error on API failure."""
    data = check_response(response, "OpenRouter")
    choice = (data.get("choices") or [{}])[0]
    if choice.get("error"):
        raise RuntimeError(f"OpenRouter API error: {str(choice['error'])[:ERROR_SNIPPET]}")
    if choice.get("finish_reason") == "length":
        raise RuntimeError("Model response was truncated (hit max_tokens)")
    return data


def _chat(prompt: str, max_tokens: int, response_format: dict | None = None) -> tuple[str, dict]:
    """POST one user prompt to OpenRouter. Returns (message content, stats)."""
    model = get_model()
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if model.startswith("inception/"):
        # Mercury reasons by default and reasoning tokens count against max_tokens,
        # which truncates quiz JSON. Other models may reject "none" (no reasoning
        # support, or reasoning mandatory), so only send it to Inception.
        payload["reasoning"] = {"effort": "none"}
    if response_format is not None:
        payload["response_format"] = response_format
        # Fail clearly if SHIKHU_MODEL points at an endpoint without structured outputs.
        payload["provider"] = {"require_parameters": True}

    start = time.time()
    response = requests.post(
        OPENROUTER_URL,
        headers=headers(),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    elapsed = time.time() - start
    data = _check_response(response)
    content = data["choices"][0]["message"]["content"] or ""
    usage = data.get("usage") or {}
    stats = {
        "elapsed": elapsed,
        "model": data.get("model", model),
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "cost": usage.get("cost"),
    }
    return content, stats


# classes
# extra="forbid" emits additionalProperties: false, which strict structured outputs
# on some providers (e.g. OpenAI) require.
class MCQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    choices: list[str] = Field(min_length=4, max_length=4)
    correct_index: int = Field(ge=0, le=3)


class Quiz(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[MCQuestion]


# --- prompt loading ---

# Prompts ship inside the package so the tool works from any cwd once installed.
PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPTS_FILE = PROMPTS_DIR / "prompts.toml"


def load_prompts() -> dict:
    """Load prompts from the package's bundled TOML."""
    with open(PROMPTS_FILE, "rb") as f:
        return tomllib.load(f)


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
    """Send a prompt with structured output, return parsed Quiz and usage stats."""
    content, stats = _chat(
        prompt,
        max_tokens,
        response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
    )
    return Quiz.model_validate_json(content), stats


# A multiple-choice question whose key is the longest option can be answered by picking the most
# detailed one. Models write the right answer with the most care, so it tends to come out longest
# (it was the longest option in 7 of the first 7 seeded questions we reviewed).
LENGTH_CUE_RATIO = 1.1  # key must be MORE than 10% longer than the longest wrong option

CHOICE_LENGTH_RULE = (
    "Make all four choices the same kind of statement and about the same length and level of "
    "detail. The correct choice must not be the longest or the most specific one: a reader "
    "should not be able to find it by length alone."
)

CHOICE_LENGTH_RETRY = (
    "Your previous attempt made the correct choice noticeably longer than the wrong ones. "
    "Rewrite the wrong choices so each is as long and as detailed as the correct one, while "
    "still being plausible and wrong."
)


def length_cue(q: MCQuestion) -> bool:
    """True if the key is longer than every wrong choice by more than LENGTH_CUE_RATIO."""
    key = len(q.choices[q.correct_index])
    longest_wrong = max(len(c) for i, c in enumerate(q.choices) if i != q.correct_index)
    return key > longest_wrong * LENGTH_CUE_RATIO


def _generate_checked(prompt: str) -> tuple[Quiz, dict]:
    """generate_quiz, retried once if any question's key is the obvious longest choice.

    Keeps whichever attempt has fewer such questions (the first on a tie); anything that still
    has the cue is flagged by _quiz_to_rows so it can be counted. Elapsed time and cost cover
    both attempts."""
    quiz, stats = generate_quiz(prompt)
    cues = sum(length_cue(q) for q in quiz.questions)
    if not cues:
        return quiz, stats
    retry, retry_stats = generate_quiz(f"{prompt}\n\n{CHOICE_LENGTH_RETRY}")
    if sum(length_cue(q) for q in retry.questions) < cues:
        quiz, kept, other = retry, retry_stats, stats
    else:
        kept, other = stats, retry_stats
    kept = dict(kept)
    kept["elapsed"] = (kept.get("elapsed") or 0) + (other.get("elapsed") or 0)
    if kept.get("cost") is not None or other.get("cost") is not None:
        kept["cost"] = (kept.get("cost") or 0) + (other.get("cost") or 0)
    kept["length_cue_retried"] = True
    return quiz, kept


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
        f"{CHOICE_LENGTH_RULE}\n\n"
        f"File: {context['file_path']}\n\n"
        f"{context['code']}"
    )

    return _generate_checked(prompt)


MAX_EXTRA_FILES = 3  # an inquiry spanning more than this is a survey, not a question

CROSS_FILE_RULE = (
    "The developer's questions spanned several of the files below. Where a concept involves how "
    "they work together, write the question so it needs facts from more than one of them."
)


def generate_questions_from_study_seeds(
    file_path: str,
    num_questions: int = 3,
) -> tuple[Quiz, dict, list[int], list[str]] | None:
    """Generate quiz questions seeded by the user's prior /shikhu-study questions for this file.

    When the inquiries behind the seeds spanned more files (record-inquiry --also), those are
    shown too so questions can cross files. Returns (quiz, stats, seed_review_question_ids,
    extra_files) or None if no seeds / file missing."""
    from shikhu.store import get_conceptual_study_questions_for_file

    seeds = get_conceptual_study_questions_for_file(file_path)
    if not seeds:
        return None

    context = build_context_from_file(file_path)
    if context is None:
        return None

    seed_block = "\n".join(f"{i + 1}. {s['question_text']}" for i, s in enumerate(seeds))

    extra: dict[str, str] = {}  # in the order the inquiries named them, capped, files that exist
    for s in seeds:
        for p in s.get("extra_files", []):
            ctx = build_context_from_file(p) if p != file_path and p not in extra else None
            if ctx is not None and len(extra) < MAX_EXTRA_FILES:
                extra[p] = ctx["code"]

    files_block = f"File: {context['file_path']}\n\n{context['code']}" + "".join(
        f"\n\nFile: {p}\n\n{code}" for p, code in extra.items()
    )
    prompt = (
        f"{CONCEPTUAL_QUESTION_DEF}\n\n"
        f"{STUDY_SEED_PROMPT.format(n=num_questions, seed_questions=seed_block)}\n\n"
        + (f"{CROSS_FILE_RULE}\n\n" if extra else "")
        + f"{CHOICE_LENGTH_RULE}\n\n"
        + files_block
    )

    quiz, stats = _generate_checked(prompt)
    return quiz, stats, [s["id"] for s in seeds], list(extra)


def generate_summary(file_path: str, max_tokens: int = 600) -> tuple[str, dict] | None:
    """Produce a prose summary of a file. Returns (summary, stats) or None if file missing."""
    context = build_context_from_file(file_path)
    if context is None:
        return None

    prompt = f"{SUMMARY_PROMPT}\n\nFile: {context['file_path']}\n\n{context['code']}"
    content, stats = _chat(prompt, max_tokens)
    return content.strip(), stats


def _get_unasked_counts() -> dict[str, int]:
    """Return {file_path: count} of unasked non-stale questions per file."""
    from shikhu.store import _get_conn

    conn = _get_conn()
    rows = conn.execute(
        "SELECT l.file_path, COUNT(*) as cnt FROM questions q "
        "JOIN question_links l ON l.question_id = q.id "
        "WHERE q.answered_at IS NULL AND q.stale = FALSE GROUP BY l.file_path"
    ).fetchall()
    conn.close()
    return {row["file_path"]: row["cnt"] for row in rows}


def _quiz_to_rows(quiz: Quiz) -> list[dict]:
    """Convert a Quiz model to the row format insert_questions expects."""
    rows = []
    for q in quiz.questions:
        # Capture the correct answer text before shuffling so the index change doesn't matter.
        # Mercury tends to place the correct answer in position A; shuffling here ensures
        # the displayed letter is random across generations.
        correct_answer = q.choices[q.correct_index]
        choices = list(q.choices)
        random.shuffle(choices)
        rows.append(
            {
                "question_text": q.question,
                "choices": choices,
                "expected_answer": correct_answer,
                "length_cue": length_cue(q),
            }
        )
    return rows
