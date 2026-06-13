# Project Shikhu

[![CI](https://github.com/arjunpatel7/shikhu/actions/workflows/ci.yml/badge.svg)](https://github.com/arjunpatel7/shikhu/actions/workflows/ci.yml)

Project Shikhu helps you learn your codebases that you generate with AI.

Use shikhu to study your codebase, track your understanding, and increase your understanding of your code. Shikhu does this by learning about your files, generating quizzes for you to take, and even by helping you study the code after you make it.

Conceptual understanding is measured by **knowledge coverage** — the share of your codebase backed by *golden questions* (questions you answered correctly *and* confirmed actually test understanding of the file). Three goldens per file = fully covered.

Think test coverage, but for your brain!

## Getting Started

Prerequisites:
- An **Inception API key** (required). This gives you access to fast, text-diffusion models for question generation and summarization. You can get one for free at [Inception Labs](https://platform.inceptionlabs.ai/).
- A **skill-compatible coding agent** like Claude Code, Codex, or Cursor (strongly recommended). The core loop — `refresh`, `quiz`, `coverage` — runs entirely in your terminal, but Shikhu really shines when paired with the `/study` skill (step 5): it turns "I don't get this file" into a guided walkthrough *and* feeds your weak spots back into future quizzes.

### 1. Install

**To use Shikhu on your own codebase** — install it as a standalone tool, then run it from inside any repo:

```bash
uv tool install shikhu
```

This puts `shikhu` on your `PATH` in an isolated environment (no clash with your project's dependencies). `cd` into any repo and the commands below just work — each repo gets its own `coverage.db`, `.quizignore`, and `.env`. (`pipx install shikhu` or `pip install shikhu` into a virtualenv work too.)

Provide your Inception API key via a `.env` file in the repo you're quizzing (auto-loaded), or export it in your shell:
```
INCEPTION_API_KEY=your-key-here
```

Get a key at [Inception Labs](https://platform.inceptionlabs.ai/).


### 2. Initialize

```bash
shikhu init
```

This creates a database and a `.quizignore` file (like `.gitignore` but for quiz generation). Add files to the latter to avoid getting quizzed on them, like config or docfiles.

### 3. Generate questions

Next, you're ready to batch some questions. Run the following command:

```bash
shikhu refresh
```

Shikhu scans your tracked files, checks for stale questions, and generates new ones using the Mercury API. Files matching `.quizignore` patterns are skipped.

Under the hood, `refresh` runs two passes in parallel: **summaries** (a cached Mercury summary per file, used to seed question generation and to feed `/study`) and **questions** (one batch per file). Both skip files whose content hash and prompt version haven't changed, so re-running `refresh` after a small edit only regenerates the files that actually changed. If you only want to refresh summaries, run `shikhu summarize`; tune parallelism with `--summary-workers` (default 8).

### 4. Take a quiz

After working on your code base, it's time to take a quiz!

(document any args here too)

```bash
shikhu quiz
```

You'll get multiple-choice questions about your code. After each answer you can rate the question quality — good questions that you answer correctly can become **golden questions**. These are eventually used as measures of knowledge coverage. You'll have **3 golden questions** per file, and passing all of them represents basic understanding of the file.

`shikhu quiz` has a couple of flags worth knowing:
- `--n <int>` — number of questions in the round (default 5)
- `--file <path>` — quiz only on one file (handy after editing it)

**What happens to old questions?** When you edit a file, Shikhu detects the change (via content hash) and marks that file's questions stale on the next `refresh`. Stale questions stick around in the database — nothing is deleted — and `refresh` generates a fresh batch on top.

Golden questions get special treatment. If a file changes after you mastered it, those goldens are flagged for **re-validation**: they come up first in your next quiz so you can prove the change didn't break your understanding. Answer correctly and they go back to golden + fresh; answer wrong and they lose golden status (your coverage updates accordingly).

### 5. Drill weak spots with `/study` (optional)

When a quiz surfaces a file you don't really understand, use the project's `/study` skill (inside your agent) to walk through it. The skill loads a cached summary, takes you through the file, and **logs every conceptual question you ask** to the database.

Then turn those questions into quiz questions that test the same concepts:

```bash
shikhu generate-from-study path/to/file.py
shikhu quiz --file path/to/file.py
```

This reinforces exactly the gaps you surfaced during study — not generic file-level questions.

### 6. Check your coverage

Wanna know how well you know your codebase? Run this commmand

```bash
shikhu coverage
```

Shows which files you've mastered and which need work. Each file needs 3 golden questions to be fully covered.

## All Commands

| Command | What it does |
|---------|-------------|
| `shikhu init` | Set up database, check API keys, create `.quizignore` |
| `shikhu quiz` | Take a quiz (default 5 questions) |
| `shikhu quiz --n 10` | Quiz with 10 questions |
| `shikhu quiz --file path.py` | Quiz on a single file |
| `shikhu refresh` | Staleness check + regenerate stale questions and summaries |
| `shikhu summarize` | Parallel Mercury summaries for every tracked file |
| `shikhu summarize --file path.py` | Force-regenerate summary for one file |
| `shikhu generate-from-study path.py` | Generate quiz Qs seeded by your prior `/study` questions for that file |
| `shikhu coverage` | Print knowledge-coverage report |
| `shikhu clean` | Delete the database (asks for confirmation) |
| `shikhu clean --yes` | Delete without confirmation |


## How It Works

1. **Question generation** — Mercury (Inception Labs) reads your source files and generates conceptual multiple-choice questions about design decisions, architecture, and how things work.

2. **Quizzing** — Answer questions in the terminal. Rate question quality. Correct answers on good questions become **golden** — validated proof you understand that file.

3. **Coverage tracking** — Each file targets 3 golden questions. Coverage = how many files have reached that bar.

4. **Staleness** — When code changes (detected via SHA-256 hashing), related questions are marked stale so your coverage stays honest.

5. **Study-driven generation** — `/study` captures the conceptual questions you actually ask while learning a file. `shikhu generate-from-study` turns those into quiz questions, so the next quiz tests the gaps you surfaced — not just whatever Mercury picks from the file.

## Golden Questions

A golden question is one you:
- Answered correctly
- Rated as good quality
- Confirmed it tests real understanding of the file

3 golden questions per file = fully covered. Golden questions go stale when the underlying code changes, keeping coverage honest over time.

## Privacy & Data

Everything Shikhu knows lives in one local SQLite file, `coverage.db`, in the repo you run it from. Nothing is uploaded anywhere. Two things are worth knowing:

- **File contents are sent to the Mercury API** (Inception Labs) to generate questions and summaries — that's the only data that leaves your machine, under your own API key. Use `.quizignore` to exclude anything you don't want sent.
- **Shikhu reads your local Claude Code transcripts** for the current project (`~/.claude/projects/...`) to find conceptual questions you've asked, and stores them in `coverage.db` to seed better quiz questions. These prompts never leave your machine — but it's one more reason `coverage.db` must stay out of git. `shikhu init` adds it to your `.gitignore` automatically.

## Configuration

### `.quizignore`

Controls which files are skipped during generation:
```
# Skip these
*.lock
.claude/
tests/
deprecated/
```

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `INCEPTION_API_KEY` | Yes | Mercury API for question generation |

## Tech

- Python 3.12+, managed with [uv](https://docs.astral.sh/uv/)
- [Mercury](https://www.inceptionlabs.ai/) for question generation
- [Typer](https://typer.tiangolo.com/) + [Rich](https://rich.readthedocs.io/) for the CLI
- SQLite for local storage
- SHA-256 file hashing for staleness detection

## Love the repo and have some ideas?
Feel free to open an issue! We aren't accepting PRs at this time.
