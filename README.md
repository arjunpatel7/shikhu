# Shikhu

[![CI](https://github.com/arjunpatel7/shikhu/actions/workflows/ci.yml/badge.svg)](https://github.com/arjunpatel7/shikhu/actions/workflows/ci.yml)

Shikhu helps you learn your codebases that you generate with AI.

It's a **command-line tool** plus a companion **`/shikhu-study` skill** for your coding agent: the CLI generates quizzes from your code and tracks your understanding, and the skill tutors you through the files you want to learn. A second skill, **`/shikhu-inquiry`**, answers questions you ask about your code and banks them as quiz seeds.

Use shikhu to study your codebase, track your understanding, and increase your understanding of your code. Shikhu does this by learning about your files, generating quizzes for you to take, and even by helping you study the code after you make it.

Conceptual understanding is measured by **knowledge coverage** — the share of your codebase backed by *golden questions* (questions you answered correctly *and* confirmed actually test understanding of the file). Three goldens per file = fully covered.

Think test coverage, but for your brain!

## Getting Started

Prerequisites:
- An **OpenRouter API key** (required). Shikhu calls models through [OpenRouter](https://openrouter.ai/); by default it uses Inception's fast Mercury 2.5 diffusion model for question generation and summarization. Get a key at [openrouter.ai/keys](https://openrouter.ai/keys).
- A **skill-compatible coding agent** like Claude Code, Codex, or Cursor (strongly recommended). The core loop — `refresh`, `quiz`, `coverage` — runs entirely in your terminal, but Shikhu really shines when paired with its skills (installed in step 2): `/shikhu-inquiry` banks the questions you ask about your code, and `/shikhu-study` turns "I don't get this file" into a guided walkthrough, *and* both feed your weak spots back into future quizzes.

### 1. Install

```bash
uv tool install shikhu
```

This puts `shikhu` on your `PATH` in an isolated environment (no clash with your project's dependencies). `cd` into any repo and the commands below just work — each repo gets its own `coverage.db`, `.quizignore`, and `.env`. (`pipx install shikhu` or `pip install shikhu` into a virtualenv work too.)

Provide your OpenRouter API key via a `.env` file in the repo (auto-loaded), or export it in your shell:

```
OPENROUTER_API_KEY=your-key-here
```

> Upgrading from 0.1.x? Shikhu no longer calls the Inception API directly, so `INCEPTION_API_KEY` is ignored — add `OPENROUTER_API_KEY` instead.

### 2. Initialize (this installs the skills)

```bash
shikhu init
```

This creates the local database (`coverage.db`, added to your `.gitignore`), a `.quizignore` file (like `.gitignore`, but for quiz generation; list config or doc files you don't want quizzed on), and **installs both skills, `/shikhu-inquiry` and `/shikhu-study`, into your agent's skills directory**. Restart your agent if the skills don't show up right away.

Skills go into `.claude/skills/` (Claude Code) and/or `.agents/skills/` (Codex, Cursor, OpenCode and others), whichever already exist in the repo, or both if neither does. A few related options:
- `shikhu init --no-skill`: set up without installing the skills.
- `shikhu install-skill`: install them later, or re-install after upgrading Shikhu so the skills match the CLI.
- `shikhu install-skill --global`: install into your home directory so they're available in every project.

### 3. Let Shikhu index your code

```bash
shikhu summarize
```

This writes a cached, model-written summary for each tracked file. The skills and `shikhu map` use those summaries to work out which files a question is about. It makes one model call per file, and it only re-runs for files whose contents changed.

### 4. See where your questions have been

```bash
shikhu map
```

If you've been using Claude Code in this repo, you've probably already asked it plenty about your code. `shikhu map` reads those past sessions, picks out the questions you asked about this codebase, and shows which files they were about next to your coverage:

```
Read 65 transcripts: 255 distinct past prompts, 255 new since the last map.
255 prompt(s) to score. Each is sent with your 25 file summaries to Jev through OpenRouter: about $0.11 on your key.
Score them now? [y/N]: y
> Scored 255 prompt(s) for $0.0877.

Your question map
  11 past questions about this codebase (from 255 past prompts)
  Coverage: 0/25 files fully covered (0%)

Asked about most
  file                   questions  golden
  src/app/store.py               4     0/3
  src/app/cli.py                 3     0/3
  src/app/generator.py           2     0/3

Not asked about yet (13 files, largest first — worth a look later)
  src/app/staleness.py  (166 lines)
  src/app/systemone.py  (149 lines)

Start here: src/app/store.py
  3 of your questions were mainly about it, it has 0/3 golden, and it's 88 lines. Ask about it with /shikhu-inquiry.
  Or quiz on what you already asked: shikhu generate-from-study src/app/store.py, then shikhu quiz --file src/app/store.py.
```

*(Illustrative output; your file names and counts will differ.)* A few things to know:
- **Scoring is the one paid step.** Each past prompt goes to Jev, a fast judgment model, through OpenRouter on your key. The map shows the estimate and asks first; `--dry-run` stops before sending anything. The first run scores your whole history, and later runs only score new prompts.
- **"Start here" is meant to be easy.** It picks the shortest file you've asked about a lot that isn't covered yet. The files you've never asked about are probably the hardest ones, so they're listed for later rather than suggested first.
- **What you already asked can become a quiz.** The questions the map found count as seeds for `shikhu generate-from-study`, just like ones you ask through `/shikhu-inquiry` (step 6), so you can skip straight to a quiz on them.
- **It only reads Claude Code sessions** run from this directory. If you use another agent, `shikhu coverage` shows your starting point instead.

### 5. Ask a question about your code

Start with the file the map picked. In your agent, just ask, or invoke the skill explicitly:

```
/shikhu-inquiry how are we storing quizzes?
```

The agent finds the files that answer your question, reads them, and explains the answer with the files up front and `path:line` citations. It ends with one line, *"Was that the right place to be looking?"*, and your reply banks the question against those files. Questions that span several files are credited to each of them.

Casual questions mid-task ("what's the pre-push hook?", "why do I gotta do that venv thing?") often trigger the skill on their own. Asking doesn't earn coverage by itself. It just gives Shikhu a question worth quizzing you on.

### 6. Quiz yourself on the answer

```bash
shikhu generate-from-study src/app/store.py
shikhu quiz --file src/app/store.py
```

`generate-from-study` turns the questions you asked into multiple-choice questions that test those same concepts (default 3; change it with `--n`). `quiz` then asks them in your terminal. After each answer you can rate the question:

- Answer **correctly**, rate it **good** (`g`), and confirm it would test someone's understanding of the file → it becomes a **golden question**.
- Rate it **bad** (`b`) or skip it (`s`) and it can't become golden.

If you ask the same thing again later, Shikhu notices it already has a question for it and moves that question to the front of your next quiz instead of generating a duplicate.

### 7. Watch coverage go up

```bash
shikhu coverage
```

```
╭──────────────────────── Knowledge Coverage ────────────────────────╮
│ 1/25 files fully covered  |  0 in progress  |  24 not started      │
╰────────────────────────── 4% complete ─────────────────────────────╯
```

*(Illustrative output.)* A file counts as fully covered once it has **3 golden questions**. Until then it shows as *in progress*, and its `n/3 golden` count in the study queue ticks up with each golden. The headline number moves one file at a time, so it's a slow, honest climb.

## Other ways in

### Study a file with `/shikhu-study`

When a quiz shows you a file you don't really understand, use `/shikhu-study <file>` in your agent. It loads the cached summary, walks you through the file section by section, and logs every conceptual question you ask. Then:

```bash
shikhu generate-from-study path/to/file.py
shikhu quiz --file path/to/file.py
```

This reinforces exactly the gaps you surfaced, not generic file-level questions. `/shikhu-study` is the deliberate route; `/shikhu-inquiry` is the same destination with no ceremony.

### Generate a full question bank with `refresh`

```bash
shikhu refresh
shikhu quiz
```

`refresh` scans your tracked files and generates a batch of general questions for every one of them, through OpenRouter (files matching `.quizignore` are skipped). Under the hood it runs two passes in parallel: **summaries** and **questions** (one batch per file). Both skip files whose content hash and prompt version haven't changed, so re-running after a small edit only regenerates the files that changed. Tune parallelism with `--summary-workers` (default 8), and run just the summaries with `shikhu summarize`.

`shikhu quiz` takes `--n <int>` (questions per round, default 5), `--file <path>` (one file only) and `--since <ref>` (only files changed since a git ref).

### Review just your branch before shipping

Before you open a PR, focus on the files the branch actually changed:

```bash
shikhu refresh --since main            # generate questions only for changed files
shikhu quiz --since main               # quiz only on changed files (re-validations first)
shikhu coverage --since main --check   # exit 1 if a changed file has no fresh golden question
```

`--since` takes any git ref and compares against where your branch split off, plus uncommitted edits. `--check` requires 1 fresh golden per file by default; raise the bar with `--min 3`. With `--since`, a file changed by more than 10 lines only counts goldens on questions written since your branch started, so re-checking an old question can't pass it; ask about the change with `/shikhu-inquiry` and quiz on the new questions. `quiz` and `coverage` also re-check staleness on startup, so a file you just edited stops counting as covered until you re-validate it. Everything reads your local `coverage.db`, so run it on your machine (for example in a pre-push hook), not in CI.

## Concepts

- **Golden questions:** a question you answered correctly, rated good, and confirmed tests real understanding of the file. Three per file = fully covered.
- **Cross-file questions:** a question that spans several files is quizzed once and credits each file it touches, so one good question can move more than one file.
- **Questions are used once:** each question you ask seeds one quiz question. Ask it again and the existing question is re-queued to the front of your next quiz, not duplicated.
- **Staleness:** when a file changes (detected via SHA-256 hashing), its questions are marked stale on the next `refresh`, `quiz` or `coverage`. Stale questions stay in the database (nothing is deleted) and `refresh` generates a fresh batch on top.
- **Re-validation:** if a file changes after you mastered it, its goldens are flagged for re-validation and come up first in your next quiz. Answer correctly and they go back to golden; answer wrong and they lose golden status, and your coverage updates accordingly.

## All Commands

| Command | What it does |
|---------|-------------|
| `shikhu init` | Set up the database, check API keys, create `.quizignore`, install both skills (`--no-skill` to skip) |
| `shikhu install-skill` | Install the `/shikhu-inquiry` and `/shikhu-study` skills (`--global` for all projects) |
| `shikhu coverage` | Print the knowledge-coverage report and a study queue (`--queue N` sets its length, `0` hides it) |
| `shikhu map` | Show where your past Claude Code questions have been, next to coverage, and pick a file to start on. Scores new past prompts with Jev after showing the cost (`--dry-run` to only estimate, `--yes` to skip the prompt) |
| `shikhu coverage --since main --check [--min N]` | Report changed files; exit 1 if any has fewer than N fresh goldens (default 1) |
| `shikhu summarize` | Parallel model-written summaries for every tracked file (`--workers`, `--extensions`) |
| `shikhu summarize --file path.py` | Force-regenerate the summary for one file |
| `shikhu generate-from-study path.py` | Generate quiz questions (`--n`, default 3) seeded by the questions you asked about that file, including past ones `shikhu map` found |
| `shikhu quiz` | Take a quiz (default 5 questions) |
| `shikhu quiz --n 10` | Quiz with 10 questions |
| `shikhu quiz --file path.py` | Quiz on a single file |
| `shikhu quiz --since main` | Quiz only on files changed since `main` |
| `shikhu refresh` | Staleness check, then regenerate stale questions and summaries |
| `shikhu refresh --since main` | Generate summaries and questions only for files changed since `main` |
| `shikhu clean` | Delete the database (asks for confirmation; `--yes` skips it) |

The skills call a few more commands behind the scenes. You normally won't run these yourself:

| Command | Used by | What it does |
|---------|---------|-------------|
| `shikhu inquiry-packet "<question>"` | `/shikhu-inquiry` | Find the tracked files that answer a question, with the evidence why (`--json` for machine-readable output) |
| `shikhu record-inquiry <file> "<question>" --confirmed\|--rejected` | `/shikhu-inquiry` | Record the exchange so it can seed a quiz (`--also <file>` repeats for each additional file; `--score`, `--runner-up`, `--runner-up-score` record the packet's ranking) |
| `shikhu study-context <file>` | `/shikhu-study` | Print a file's cached summary and prior review records |
| `shikhu log-review <file>` | `/shikhu-study` | Start (`--start`) or close (`--review-id`, `--summary`, `--transcript`, `--notes`) a study session |
| `shikhu log-study-question <review_id> "<question>"` | `/shikhu-study` | Record a question asked during a study session (`--conceptual/--not-conceptual`, `--answered/--unanswered`) |

## Privacy & Data

Everything Shikhu knows lives in one local SQLite file, `coverage.db`, in the repo you run it from. Nothing is uploaded anywhere. Four things are worth knowing:

- **File contents are sent to OpenRouter**, which routes them to the provider behind the model in use (by default Inception, for Mercury 2.5), to generate questions and summaries — that, plus the past prompts `shikhu map` scores (below), is the only data that leaves your machine, under your own API key. Use `.quizignore` to exclude anything you don't want sent. Setting `SHIKHU_MODEL` sends your code to whichever provider serves that model instead.
- **Requests identify shikhu to OpenRouter** by name and repo URL ([app attribution](https://openrouter.ai/docs/app-attribution)), which is what lists shikhu on OpenRouter's public app rankings. Only the app name and URL are sent; nothing about you, your key, or your code.
- **Shikhu reads your local Claude Code transcripts** for the current project (`~/.claude/projects/...`) when you run `shikhu map`, to find conceptual questions you've asked, and stores them in `coverage.db` to seed better quiz questions. Apart from `shikhu map` (below), these prompts never leave your machine — but it's one more reason `coverage.db` must stay out of git. `shikhu init` adds it to your `.gitignore` automatically.
- **`shikhu map` sends your past prompts to OpenRouter** for Jev to score: each prompt, the end of the assistant reply it followed, and your file summaries. It shows the cost and asks before sending anything, and `--dry-run` sends nothing.

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
| `SHIKHU_JEV_BUDGET_USD` | No | Spending cap for Jev calls per command run (default `1.00`). `shikhu map` stops at the cap, keeps what it scored, and picks up where it left off next run. |
| `SHIKHU_MODEL` | No | **Experimental, unsupported.** Override the OpenRouter model id (default `inception/mercury-2.5`). The model must support structured outputs; reasoning models may fail with truncated responses. |

## Tech

- Python 3.12+, managed with [uv](https://docs.astral.sh/uv/)
- [OpenRouter](https://openrouter.ai/) + [Mercury 2.5](https://www.inceptionlabs.ai/) (default model) for question generation
- [Typer](https://typer.tiangolo.com/) + [Rich](https://rich.readthedocs.io/) for the CLI
- SQLite for local storage
- SHA-256 file hashing for staleness detection

## Love the repo and have some ideas?
Feel free to open an issue! We aren't accepting PRs at this time.
