---
name: shikhu
description: How to use the shikhu CLI itself — its commands, setup, coverage and golden questions, the push hook, and the habits that make its quizzes work. Use it when the user asks how to do something with shikhu ("how do I quiz on my branch?", "what does --since do?", "why is my push blocked?", "how do I set up the hook?", "where's my key stored?"), and whenever a `git push` prints `shikhu gate:`. Do NOT use it for questions about the user's own code; that's /shikhu-inquiry.
---

# shikhu — using the tool

Shikhu checks that a developer understands the code they ship, especially code their agent
wrote. It writes multiple-choice questions from their code and the questions they asked about
it, quizzes them in the terminal, and tracks **knowledge coverage**: which files are backed by
*golden* questions. Everything is stored in `coverage.db` in the repo, on their machine.

Answer from this page; run `shikhu <command> --help` when you need a flag it doesn't cover.
Never take a quiz for the user: `shikhu quiz` is interactive and the knowledge is theirs.

## The two loops

**Learn an existing codebase**
```bash
shikhu init          # database, skills, asks for the OpenRouter key and the push hook
shikhu summarize     # one cheap model call per file; lets shikhu tell what a question is about
shikhu map           # Claude Code users: where their past questions were; picks a file to start
```
Then they ask their agent about the code (/shikhu-inquiry answers, then writes quiz questions
from the question with `shikhu generate-from-study <file>`), and run `shikhu quiz`.

**Ship a change (the push gate)**
1. Code on a branch as usual, with the agent.
2. Ask about the change. /shikhu-inquiry answers, then writes questions aimed at the diff with
   `shikhu generate-from-study <file> --since main`.
3. `shikhu quiz --since main`, then `git push`. The hook checks each changed file has a fresh
   golden question.

## Commands they run

| Command | What it does |
|---|---|
| `shikhu quiz [--n 5] [--file F] [--since main]` | Take a quiz. After each answer, rate it `g`/`b`/`s` |
| `shikhu coverage [--since main --check]` | Coverage report; `--check` exits 1 if a changed file needs a quiz |
| `shikhu generate-from-study F [--since main]` | Questions on F, seeded by questions the user asked about it |
| `shikhu refresh [--since main]` | General questions and summaries for every file (or the changed ones) |
| `shikhu summarize [--file F]` | Cached file summaries |
| `shikhu map [--dry-run]` | Past Claude Code questions per file; the one paid step, asks first |
| `shikhu install-hook [--mode warn\|block]` | The pre-push gate |
| `shikhu install-skill [--global]` | Re-install the skills after upgrading shikhu |

## How it works

- **Golden question:** answered correctly, rated good (`g`), and confirmed as testing real
  understanding. A file is covered at 3 goldens, or 1 if it's under 75 non-blank lines.
- **Staleness:** editing a file makes its goldens stale until they're re-checked in a quiz.
- **`--since <ref>`** compares against where the branch left that ref, plus uncommitted edits.
  For a file changed by more than 10 lines, the gate only counts questions written since the
  branch started, so re-checking old questions can't pass it.
- **Which files:** git-tracked files ending in `.py .js .ts .jsx .tsx .html .css`, minus
  `.quizignore`. Other languages: `--extensions .go,.rs` on summarize, refresh and coverage.
- **Key:** `OPENROUTER_API_KEY` from the shell, then the repo's `.env`, then the one
  `shikhu init` saved in `~/.config/shikhu/.env` (readable only by the user).
- **Local only:** `coverage.db` is gitignored and never leaves the machine, so the gate runs in
  a pre-push hook, not CI. It checks pushes, not merges on GitHub.

## Habits that make it work

- **Ask before generating.** Questions seeded by something the user asked are better than
  `refresh`'s general ones. On a branch, `--since main` aims them at the change.
- **Bad questions happen.** If two answers look right or the marked answer is wrong, rate it
  `b`; it won't count and won't come back. Then generate more for that file.
- **To see what the gate still needs:** `shikhu coverage --since main --check`.
- **Clearing several files**, quiz each one and join them with `;`, not `&&`: a missed answer
  exits non-zero and would stop the chain.
  `shikhu quiz --file a.py --n 1; shikhu quiz --file b.py --n 1`
- **A file with no questions left:** ask about it again (best), or `shikhu refresh --since main`.
- **Where to start learning:** `shikhu map` (Claude Code history) or `shikhu coverage` (its study
  queue lists the least-covered files), then /shikhu-study for a guided walk through one file.

## When a push prints `shikhu gate:`

- `push blocked` (block mode) or `pushed anyway` (warn mode): some changed files haven't been
  quizzed. Tell the user which files it listed and that `shikhu quiz --since <base>` will ask
  about them. If a file has no questions yet, suggest they ask you about the change first.
- `the check couldn't run`: shikhu didn't start or the base branch is missing. The message says
  how to fix it (reinstall shikhu, or `git config shikhu.gate.base origin/main`).
- **Never get past the gate yourself**: no `SHIKHU_GATE=off`, no `--no-verify`, no changing
  `shikhu.gate.mode`. Skipping it is the user's call. They can switch modes with
  `git config shikhu.gate.mode warn|block` or skip once with `SHIKHU_GATE=off git push`.
