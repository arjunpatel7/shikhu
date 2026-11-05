# Shikhu

[![CI](https://github.com/arjunpatel7/shikhu/actions/workflows/ci.yml/badge.svg)](https://github.com/arjunpatel7/shikhu/actions/workflows/ci.yml)

✍️ **[your words: the hook, one or two sentences]**
> Must get across: you build with a coding agent; shikhu checks that *you* understand what it
> wrote, before you ship it. "Test coverage, but for your brain" is the existing line.

✍️ **[hero image: a real terminal quiz, or the push gate blocking then passing]**
> Use an absolute URL (`https://raw.githubusercontent.com/arjunpatel7/shikhu/main/docs/images/...`)
> so it also shows on PyPI, which renders this same file.

## The 60-second version

```bash
uv tool install shikhu
cd your-repo
shikhu init        # asks for your OpenRouter key, and whether to check each git push
shikhu summarize   # indexes your code (one cheap model call per file)
shikhu refresh     # writes a few questions for every file
shikhu quiz
```

✍️ **[your words: what just happened, 1–2 lines]**
> Must get across: you were just quizzed on your own code; get one right and rate it good, and
> that file starts counting as understood. Then the better way: ask your agent about your code,
> and the questions come from what you asked.
>
> ⚠️ Decision for you: "ask your agent, then `shikhu quiz`" is only true on a branch today (the
> skill writes questions automatically off the default branch only). On main it takes
> `shikhu generate-from-study <file>` first. Making the skill write questions on main too is a
> few lines in the skill. Say if you want it, and this block can become ask → quiz.

## How it works

✍️ **[diagram 1: the learn loop]** ask your agent → questions written from your code and your
question → `shikhu quiz` → golden question → coverage

✍️ **[your words: 3–5 lines]**
> Must get across:
> - A **golden question** = you answered it right, rated it good, and confirmed it tests real
>   understanding of the file.
> - A file is **covered** at 3 golden questions (1 for files under 75 lines).
> - Editing a file makes its golden questions stale until you re-check them.
> - Questions come from your code plus the questions you ask, so they're about what you care about.

## Get started

**You'll need:**
- Python 3.12+ and [uv](https://docs.astral.sh/uv/) (or pipx/pip)
- An [OpenRouter API key](https://openrouter.ai/keys). ✍️ [cost line, e.g. "A first run on a
  small repo costs about a cent. New OpenRouter accounts need a $5 minimum top-up."]
- A coding agent that supports skills: Claude Code, Codex, Cursor, OpenCode

### 1. Install and set up a repo

```bash
uv tool install shikhu
cd your-repo
shikhu init
```

`init` asks two things:
- **Your OpenRouter key** (typing is hidden). It's saved once in `~/.config/shikhu/.env`,
  readable only by you, and every repo uses it. A key in your shell or the repo's `.env` wins.
- **Check your understanding before each git push?** `w` warn, `b` block, or `s` skip
  (see [Ship with it](#ship-with-it)).

It also creates `coverage.db` (gitignored), a `.quizignore`, and installs three skills for your
agent: `/shikhu-inquiry`, `/shikhu-study` and `/shikhu`.

✍️ [optional: one line of voice]

### 2a. An existing codebase

```bash
shikhu summarize
shikhu map           # Claude Code users: finds the questions you already asked here
```

`map` shows which files your past questions were about and picks one to start on. Its one paid
step (scoring old prompts) shows the cost and asks first. No Claude Code history? It tells you
the other way in:

- Ask your agent about your code. `/shikhu-inquiry` answers and banks the question; then
  `shikhu generate-from-study <file>` and `shikhu quiz --file <file>`.
- Or `shikhu refresh` for general questions on every file, then `shikhu quiz`.

✍️ [your words: which one you'd start with]

### 2b. A new project

Build with your agent as usual and commit. Shikhu only reads files git tracks, so `git add` new
code first. When you push, the hook asks whether you've been quizzed on what changed.

✍️ [your words: why this is the best way to start fresh]

## Ship with it

✍️ **[diagram 2: the ship loop]** code with your agent → ask about the change → quiz →
`git push` (the hook checks) → PR

```bash
# on your branch, after asking your agent about the change:
shikhu quiz --since main
git push
```

Asking `/shikhu-inquiry` about your change on a branch writes questions aimed at the diff
(`shikhu generate-from-study <file> --since main`). The hook checks that each changed file has a
fresh golden question:

| | warn | block |
|---|---|---|
| Not quizzed yet | pushes, and lists what you haven't been quizzed on | stops the push |
| Your agent is pushing | told to have you run `shikhu quiz --since main` | told not to skip it, and to hand the quiz to you |
| Switch | `git config shikhu.gate.mode block` | `git config shikhu.gate.mode warn` |

Skip once: `SHIKHU_GATE=off git push`. Add the hook later: `shikhu install-hook`.

✍️ [your words: what this loop feels like. The measured stat, if you want it: "about 6–16
minutes of quizzing per PR, about 20 seconds per question" across six PRs]
> Must get across: it runs on your machine against your local `coverage.db`, so it checks *your*
> pushes. It's not a CI check and doesn't block merges on GitHub.

## Your agent's skills

- `/shikhu-inquiry`: ask about your code; it answers with the files that matter and banks the
  question as a quiz seed. Often triggers on its own.
- `/shikhu-study <file>`: a guided walkthrough of one file.
- `/shikhu`: how to use shikhu itself, and what to do when a push is blocked.

## Privacy

- Your quiz history lives in `coverage.db` in the repo, on your machine. It's gitignored.
- File contents go to OpenRouter (by default Inception's Mercury 3) to write summaries and
  questions, under your key. `.quizignore` keeps files out.
- `shikhu map` reads your local Claude Code transcripts for this repo, and sends past prompts
  to OpenRouter for scoring only after showing the cost and asking.

Details: [Privacy & data](https://github.com/arjunpatel7/shikhu/blob/main/docs/guide.md#privacy--data).

## More

- [The full guide](https://github.com/arjunpatel7/shikhu/blob/main/docs/guide.md): every command,
  how coverage works, configuration, `/shikhu-study`, `refresh`, languages (`--extensions`)
- ✍️ [contributing line: currently "open an issue; we aren't accepting PRs at this time"]

<!--
DON'T-FORGET CHECKLIST (delete before merge)
- [ ] Hook sentence + hero image (absolute URL so PyPI shows it)
- [ ] Cost line: about a cent for a first run; $5 OpenRouter minimum top-up
- [ ] Python 3.12+ (SHI-49) ✓ in "You'll need"
- [ ] Languages: .py .js .ts .jsx .tsx .html .css by default; others via --extensions (SHI-45). Only in guide now; add a line here?
- [ ] Git-tracked files only (SHI-44) ✓ in 2b
- [ ] map is Claude Code only, and what to do without history (SHI-46) ✓ in 2a
- [ ] Not CI; skip once with SHIKHU_GATE=off ✓ in Ship with it
- [ ] Privacy ✓ (short) + link
- [ ] Both diagrams as committed SVGs in docs/images/, absolute URLs
- [ ] Every command here was run end to end on 2026-11-04 (fresh-install test). Re-check any you add.
-->
