---
name: shikhu-inquiry
description: >-
  Answer a question about how or why something in THIS repository works, by showing which
  tracked files answer it and why, then recording the exchange so it can seed a quiz later.
  Use it whenever the user asks a genuine question whose subject belongs to this repository
  — its source, CLI behavior, config, CI, hooks or build setup — and answering it well means
  reading files here. These arrive casually and mid-task, often elliptical or typo-laden:
  "how are we storing quizzes rn?", "what does that for loop do?", "what's the pre-push
  hook?", "why do I gotta do that venv thing?", "explain how verifying/decoding works".
  Use it for those. Nothing else detects these, so a question you skip is lost.
  Do NOT use it for: instructions to do work; short remarks, fragments or acknowledgements;
  session or tool boilerplate; requests to restate or clarify something already said;
  questions about the assistant itself; questions about external products, services or
  libraries; or product, pricing and strategy questions.
  The user can also invoke it explicitly as /shikhu-inquiry.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(shikhu *)
  - Bash(*python -m shikhu *)
---

# shikhu-inquiry — answer from the codebase, then bank the question

## Why this exists

Shikhu tracks how well the user understands *their own* code. Historically that meant
generating quizzes and asking them to sit through them, which people don't do. But people
*do* ask their agent to explain their code, constantly. This skill turns that existing
habit into the same signal: the question they already asked becomes the seed of a quiz
question, and the files you cite become the record of what it was about.

Two things follow from that, and they are the whole point:

- **Show your work.** Don't just answer — say which files answer it and why those files.
  The user should end the turn knowing where the answer lives, not just what it is.
- **Ask once, cheaply.** One short confirm closes the loop. Silence earns nothing.

## 0. Invoked explicitly?

If the user ran `/shikhu-inquiry` directly, skip any judgment about whether this is a
conceptual question — they've already made that call. Use their message as the question, or
ask them for one if they gave none, then continue from step 1.

## 1. Get the packet

```bash
shikhu inquiry-packet "<the user's question>"
```

If `shikhu` is not on PATH or is an older install without this command, invoke the module
directly instead: `python -m shikhu inquiry-packet "<question>"`, using the interpreter for
this project.

Three possible outcomes:

| output | what it means | what you do |
|---|---|---|
| `START HERE` + files | files were found | continue to step 2 |
| `not answerable from this codebase` | the question is about a library, a tool, or general knowledge | answer normally, **record nothing**, skip the confirm |
| `No cached file summaries` | the repo hasn't been summarized | say one line: *"shikhu has no summaries for this repo yet — `shikhu summarize` would let me point at files."* then answer normally |

Never invent a packet. If the command fails for any reason, just answer the question
normally and say nothing about shikhu.

## 2. Answer, leading with the files

Structure the answer so the files come first and carry their justification:

> Two files answer this. **`src/shikhu/store.py`** is where it actually lives — it's the
> SQLite persistence layer, and the `questions` table is defined there. **`src/shikhu/ingest.py`**
> matters because it's what writes rows in the first place.
>
> [then the actual explanation, citing `path:line`]

Rules:

- **Lead with the strong file(s).** The packet's scores are calibrated; a 0.93 is a
  confident hit and a 0.35 is a maybe. Present them with that asymmetry, don't flatten
  them into a list.
- **Read the files before explaining them.** The packet is built from cached summaries, not
  from current source. Confirm the code still says what you're about to claim.
- **Cite `path:line`** so the user can jump there.
- **Don't show raw scores** unless the user asks. "This is the main one, and this one's
  related" carries the same information without the noise.
- If the packet's top file looks wrong to you after reading it, say so and name the file you
  think is actually right. You have the source; the packet only had summaries.

## 3. Ask the confirm — one line, at the end

After answering, ask exactly one short question:

> *"Was that the right place to be looking?"*

Keep it to one line. Do not offer a menu, do not explain the credit system, do not ask it
in the middle of the answer.

## 4. Record their reply

When they answer, record it and say nothing further about it:

```bash
# they said yes / "yeah" / "that's it" / moved on satisfied
shikhu record-inquiry "<top file from the packet>" "<their original question>" --confirmed --score <top score>

# they said no / named a different file / said it was the wrong area
shikhu record-inquiry "<top file from the packet>" "<their original question>" --rejected --score <top score>
```

Pass `--runner-up` and `--runner-up-score` when the packet listed a second file.

**A rejection is valuable, not a failure.** It records that the question was real but the
file was wrong, which is exactly the correction that improves ranking. Record it with the
same lack of ceremony as a confirmation.

If the user ignores the confirm and moves on to something else, **record nothing** and drop
it. Don't re-ask, and don't record a confirmation they didn't give.

## 5. Don't oversell what was earned

Confirming validates the **question**, not the user's knowledge — they just asked it, which
is mild evidence they *didn't* know. Knowledge credit comes only from answering it later in
a quiz. So never say "that's covered now" or "coverage went up."

At most, one line if it feels natural:

> *"Banked — `shikhu generate-from-study src/shikhu/store.py` will turn that into a quiz question."*

Usually say nothing at all. The recording is bookkeeping, not an achievement.

## When not to use this

- The user asked you to *do* something (write, fix, run, refactor). That's not an inquiry.
- The question is about a library, a tool, the agent itself, or general programming.
  The gate in step 1 catches most of these; trust it when it says not answerable.
- The user is mid-task and a detour would break their flow. Answer, skip the confirm.
