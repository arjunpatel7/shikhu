# Shikhu

[![CI](https://github.com/arjunpatel7/shikhu/actions/workflows/ci.yml/badge.svg)](https://github.com/arjunpatel7/shikhu/actions/workflows/ci.yml)

Shikhu helps you learn the codebases you generate with AI. Think test coverage, but for your brain.

You already ask your coding agent how your code works. Shikhu turns those questions into a measure of what you actually understand: it points you at the files that answer each question, banks the question, and later quizzes you on it. **Knowledge coverage** is the share of your codebase backed by *golden questions* (questions you answered correctly *and* confirmed really test understanding of the file). Three goldens per file = fully covered.

```
you:    how are we storing quizzes rn?
agent:  Two files answer this: store.py (the SQLite layer) and ingest.py (what writes the rows)...
        Was that the right place to be looking?   yes

$ shikhu generate-from-study src/shikhu/store.py     # your question becomes a quiz question
$ shikhu quiz --file src/shikhu/store.py             # answer it, rate it
$ shikhu coverage                                    # store.py: 1/3 golden
```

It's a **command-line tool** plus two skills for your coding agent: **`/shikhu-inquiry`** (answer a question from the codebase and bank it) and **`/shikhu-study`** (a guided walkthrough of one file).

## Getting Started

Prerequisites:
- An **OpenRouter API key** (required). Shikhu calls models through [OpenRouter](https://openrouter.ai/); by default it uses Inception's fast Mercury 2.5 diffusion model for question generation and summarization. Get a key at [openrouter.ai/keys](https://openrouter.ai/keys).
- A **skill-compatible coding agent** like Claude Code, Codex, or Cursor (strongly recommended). `/shikhu-inquiry` turns the questions you already ask your agent into quiz material, and `/shikhu-study` walks you through a file. The CLI loop (`refresh`, `quiz`, `coverage`) also runs entirely in your terminal.

### 1. Install

**To use Shikhu on your own codebase** — install it as a standalone tool, then run it from inside any repo:

```bash
uv tool install shikhu
```

This puts `shikhu` on your `PATH` in an isolated environment (no clash with your project's dependencies). `cd` into any repo and the commands below just work — each repo gets its own `coverage.db`, `.quizignore`, and `.env`. (`pipx install shikhu` or `pip install shikhu` into a virtualenv work too.)

Provide your OpenRouter API key via a `.env` file in the repo you're quizzing (auto-loaded), or export it in your shell:
```
OPENROUTER_API_KEY=your-key-here
```

Get a key at [openrouter.ai/keys](https://openrouter.ai/keys). Shikhu is built and tested against `inception/mercury-2.5`.

> Upgrading from 0.1.x? Shikhu no longer calls the Inception API directly, so `INCEPTION_API_KEY` is ignored — add `OPENROUTER_API_KEY` instead.


### 2. Initialize

```bash
shikhu init
```

This creates the database and a `.quizignore` file (like `.gitignore`, but for quiz generation; add config files, docs and anything else you don't want quizzed). It also installs both skills, `/shikhu-inquiry` and `/shikhu-study`, into your agent's skills directory (pass `--no-skill` to skip, or run `shikhu install-skill` later; `--global` installs for all projects).

### 3. See where you stand

```bash
shikhu refresh      # summarize files and generate questions
shikhu coverage
```

`refresh` scans your tracked files and writes a cached summary per file (these power `/shikhu-inquiry` and `/shikhu-study`) plus a batch of questions. It skips files whose content hash hasn't changed, so re-running after a small edit only touches what changed. `coverage` shows every file as not started, in progress, or fully covered, and lists the files to study next.

### 4. Ask a question

In your agent, ask something about your code, or run `/shikhu-inquiry` directly:

> how does staleness detection work?

The skill finds the files that answer it, leads with the ones that matter and why, then asks one line: *was that the right place to be looking?* Your answer is recorded. Questions that span several files credit each of them, and each question is used to seed a quiz question once.

### 5. Answer it

```bash
shikhu generate-from-study path/to/file.py   # turn your questions into quiz questions
shikhu quiz --file path/to/file.py
```

After each answer you rate the question. A correct answer on a question you rate as good becomes a **golden**.

### 6. Watch coverage rise

```bash
shikhu coverage
```

The headline percentage counts files that have reached 3 goldens, so one answer shows up as that file moving from *not started* to *in progress* (`1/3 golden`). Keep asking and answering and files fill in.

## Coming next: bulk import

Soon you'll be able to import your existing agent transcripts, see coverage straight away from the questions you've already asked, and then practice on them. This isn't built yet.

## Other ways in

- **`/shikhu-study <file>`**: a guided walkthrough of one file. It loads the cached summary, walks the file section by section, and logs every conceptual question you ask so `generate-from-study` can quiz you on the gaps you surfaced.
- **`shikhu quiz`**: takes `--n <int>` (default 5), `--file <path>` and `--since <ref>`. Questions you've re-asked are re-queued, so you see them again.
- **Review your branch before shipping:**

```bash
shikhu refresh --since main            # generate questions only for changed files
shikhu quiz --since main               # quiz only on changed files (re-validations first)
shikhu coverage --since main --check   # exit 1 if a changed file has no fresh golden question
```

`--since` takes any git ref and compares against where your branch split off, plus uncommitted edits. `--check` requires 1 fresh golden per file by default; raise it with `--min 3`. Everything reads your local `coverage.db`, so run it on your machine (for example in a pre-push hook), not in CI.

## Concepts

- **Golden questions.** A question you answered correctly, rated as good, and confirmed tests real understanding of the file. 3 per file = fully covered.
- **Staleness.** When a file changes (detected by SHA-256 hash), its questions are marked stale on the next `refresh`; nothing is deleted. Goldens on a changed file are flagged for **re-validation** and come up first in your next quiz. Answer correctly and they count again; answer wrong and they lose golden status.
- **Cross-file questions.** An inquiry that spans several files credits each of them.
- **Seeds are used once.** A banked question seeds one quiz question, so your quizzes don't repeat themselves.

## All Commands

| Command | What it does |
|---------|-------------|
| `shikhu init` | Set up database, check API keys, create `.quizignore`, install both skills |
| `shikhu install-skill [--global]` | Install the `/shikhu-inquiry` and `/shikhu-study` skills |
| `shikhu refresh [--since REF]` | Staleness check, then regenerate stale questions and summaries |
| `shikhu summarize [--file path.py]` | Parallel model-written summaries (or force one file) |
| `shikhu quiz [--n N] [--file path.py] [--since REF]` | Take a quiz |
| `shikhu coverage [--since REF] [--check] [--min N]` | Coverage report; with `--check`, exit 1 if a file has fewer than N fresh goldens (default 1) |
| `shikhu generate-from-study path.py [--n N]` | Generate quiz questions seeded by your inquiries and study questions for a file (default 3) |
| `shikhu inquiry-packet "question" [--json]` | Find the files that answer a question (used by `/shikhu-inquiry`) |
| `shikhu record-inquiry FILE "question" --confirmed\|--rejected [--also FILE]` | Record an inquiry (used by `/shikhu-inquiry`) |
| `shikhu study-context path.py` | Print a file's cached summary and prior reviews (used by `/shikhu-study`) |
| `shikhu log-review` / `shikhu log-study-question` | Record a study session and its questions (used by `/shikhu-study`) |
| `shikhu clean [--yes]` | Delete the database (asks for confirmation) |

## Privacy & Data

Everything Shikhu knows lives in one local SQLite file, `coverage.db`, in the repo you run it from. Nothing is uploaded anywhere. Two things are worth knowing:

- **File contents are sent to OpenRouter**, which routes them to the provider behind the model in use (by default Inception, for Mercury 2.5), to generate questions and summaries — that's the only data that leaves your machine, under your own API key. Use `.quizignore` to exclude anything you don't want sent. Setting `SHIKHU_MODEL` sends your code to whichever provider serves that model instead.
- **Requests identify shikhu to OpenRouter** by name and repo URL ([app attribution](https://openrouter.ai/docs/app-attribution)), which is what lists shikhu on OpenRouter's public app rankings. Only the app name and URL are sent; nothing about you, your key, or your code.
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
| `OPENROUTER_API_KEY` | Yes | OpenRouter API for question and summary generation |
| `SHIKHU_MODEL` | No | **Experimental, unsupported.** Override the OpenRouter model id (default `inception/mercury-2.5`). The model must support structured outputs; reasoning models may fail with truncated responses. |

## Tech

- Python 3.12+, managed with [uv](https://docs.astral.sh/uv/)
- [OpenRouter](https://openrouter.ai/) + [Mercury 2.5](https://www.inceptionlabs.ai/) (default model) for question generation
- [Typer](https://typer.tiangolo.com/) + [Rich](https://rich.readthedocs.io/) for the CLI
- SQLite for local storage
- SHA-256 file hashing for staleness detection

## Love the repo and have some ideas?
Feel free to open an issue! We aren't accepting PRs at this time.
