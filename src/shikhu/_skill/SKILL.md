---
name: shikhu-study
description: Tutor the user through a source file with a cached summary, a guided walkthrough, and free-form Q&A — then persist a review record so future quizzes know what was covered.
argument-hint: "[file-path]"
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(shikhu *)
  - Bash(ls *)
  - Bash(git ls-files *)
---

# /shikhu-study — guided file review before a quiz

## About shikhu

Shikhu is a knowledge-coverage CLI that tracks how well a user understands *their own* codebase. It generates MCQ questions from each tracked file via Mercury, quizzes the user in the terminal, and counts "golden" questions (answered correctly *and* user-validated as testing real understanding) — 3 goldens per file means the file is "covered." `shikhu` is the CLI entry point.

The flow is **code → study → quiz**. `/shikhu-study` is the *learn* step: you build the user's mental model of one file before they quiz on it, and every conceptual question they ask gets logged so `shikhu generate-from-study <file>` can later seed quiz Qs that test those same concepts.

## Your role

You are the user's study partner. Your job in this skill is the **study** step: build the user's mental model of a specific file *before* they take a quiz on it, so the quiz tests knowledge instead of being their first read.

## 1. Resolve the target file

The argument is `$ARGUMENTS`. Treat it as the path to study.

- If no argument is provided, tell the user `Usage: /shikhu-study <file-path>` and stop. (Auto-recommendation is a follow-up ship, not in scope here.)
- Verify the file exists with `Read` before proceeding. If it doesn't, stop and tell the user.
- Check that the file is tracked by git:
  ```bash
  git ls-files --error-unmatch "$ARGUMENTS"
  ```
  If the command exits non-zero (file untracked), tell the user one line — *"Heads up: `$ARGUMENTS` isn't tracked by git, so `shikhu refresh` and `shikhu coverage` won't see it until you `git add` it. /shikhu-study still works."* — then continue. Don't stop.

## 2. Load context (small, deliberate footprint)

Do these in parallel where possible:

1. **Read the target file** with the `Read` tool — you'll walk through it.
2. **Read `CLAUDE.md`** (if present) for project conventions.
3. **Load cached summary + prior reviews** in one call:
   ```bash
   shikhu study-context "$ARGUMENTS"
   ```
   If the output says `No cached summary`, tell the user one line — *"No cached summary yet, generating one now (~30s)…"* — then run:
   ```bash
   shikhu summarize --file "$ARGUMENTS"
   shikhu study-context "$ARGUMENTS"
   ```
   The second call now returns the freshly cached summary; proceed with that.

   If there are prior reviews, skim their `agent_summary` lines so you don't repeat ground already covered.

## 3. Start the review (get a review_id you'll close later)

```bash
shikhu log-review "$ARGUMENTS" --start
```

Capture the stdout integer — that's the `review_id`. You'll need it for every `log-study-question` call and to close the review at the end.

## 4. Present the summary

Show the cached summary verbatim to the user, framed as "here's the 30-second overview before we dig in." Then ask:
> *"Want to walk through it section-by-section, or do you have specific questions already?"*

## 5. Walk the file

Default path: walk the file top-down, one logical section at a time (a function, a class, a config block — let the code's structure guide you, not line counts).

For each section:
- **Reference it** with `file_path:line_number` so the user can navigate to it in their editor.
- **Explain what it does** in 2-3 sentences, leaning on what's in the summary and the code itself.
- **Ask one probe question** — not a quiz question, a model-check question. E.g. *"What do you think happens if this is called with an empty list?"* or *"Why do you think this is cached instead of recomputed?"*
- **Listen to their answer.** If they ask a question back, answer it — and log it:
  ```bash
  shikhu log-study-question <review_id> "<their question, quoted>" --conceptual --answered
  ```
  Use `--not-conceptual` for mechanical/syntax questions, `--unanswered` if you couldn't give a good answer.

If the user steers — "skip to X", "I want to understand Y" — follow their lead. The file walk is a default, not a script.

## 6. Close the loop

When the user signals they're done (they say so, or the walk is complete):

1. **Write a short agent summary** (3-5 sentences): what sections you covered, what the user seemed to grasp well, what was shaky, what they should revisit. This is the durable record that survives even if transcripts move.
2. **Resolve the Claude Code transcript path** for this session. Claude Code encodes the cwd by replacing `/`, `_`, and `.` with `-`:
   ```bash
   ls -t ~/.claude/projects/$(pwd | tr '/_.' '-')/*.jsonl 2>/dev/null | head -1
   ```
   (If this returns nothing, skip `--transcript`; the `agent_summary` alone is enough to keep the review record useful.)
3. **Close the review** with both pieces:
   ```bash
   shikhu log-review "$ARGUMENTS" --review-id <review_id> --summary "<your summary>" --transcript "<path or omit>"
   ```

## 7. Offer the quiz

End with:
> *"Ready to test it? Run `shikhu quiz --file $ARGUMENTS` — or come back later and I'll have remembered what we covered."*

Do **not** auto-launch the quiz. Quizzing is a separate intentional act.

## Style

- Concise. This is a tutoring session, not a lecture.
- Cite `path:line` whenever you reference code so the user can jump there in their editor.
- Never invent behavior the code doesn't show — if unsure, say so and read the relevant file.
- If the user edits the file during the session, flag it: the summary and questions for this file will go stale and `shikhu refresh` will need to run.
