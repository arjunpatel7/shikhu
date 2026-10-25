"""The question lifecycle, in one place.

A question's state is never stored. It is computed from two things:
  - what has happened to the question (its events), and
  - the code as it is right now.

  The normal path:     unasked ──answered──▶ answered ──promoted──▶ golden

  When the code moves: golden ──▶ needs re-check ──rechecked──▶ golden again (re-anchored)
                                                 └──retired───▶ retired
                       unasked / answered ──▶ stale

"The code moved" means a file the question is anchored to no longer matches the version it was
last checked against. Because that is computed rather than stored, switching back to a branch,
or undoing an edit, puts the question straight back.

Nothing here touches the database or the disk: callers pass in the events and the current file
versions (staleness.compute_file_hash).
"""

from dataclasses import dataclass, field
from enum import StrEnum


class State(StrEnum):
    UNASKED = "unasked"
    ANSWERED = "answered"
    GOLDEN = "golden"
    NEEDS_RECHECK = "needs re-check"
    STALE = "stale"
    RETIRED = "retired"


@dataclass
class Event:
    """Something that happened to a question. Events are only ever added, never edited."""

    # created | answered | agent_answered | rated | promoted | rechecked | retired
    kind: str
    at: str
    detail: dict = field(default_factory=dict)


@dataclass
class Question:
    id: int
    anchors: dict[str, str | None] = field(default_factory=dict)  # file -> version checked against
    answered: bool = False
    golden: bool = False
    retired: bool = False
    history: list[Event] = field(default_factory=list)


# The moves a person makes, the states each is allowed from, and the state it leads to.
# Code changing is not a move: it's computed (see state() and CODE_CHANGES), which is what keeps
# a branch switch from undoing anything. A re-check is its own move rather than an answer, since
# answering a question whose code moved doesn't make it current again until you confirm it.
MOVES: dict[str, tuple[frozenset[State], State]] = {
    #  move          allowed from                                    leads to
    "answered": (frozenset({State.UNASKED, State.ANSWERED}), State.ANSWERED),
    "promoted": (frozenset({State.ANSWERED}), State.GOLDEN),
    "rechecked": (frozenset({State.NEEDS_RECHECK}), State.GOLDEN),
    "retired": (frozenset({State.NEEDS_RECHECK}), State.RETIRED),
}

# What a question becomes when its code moves. Not moves: state() computes these.
CODE_CHANGES: dict[State, State] = {
    State.UNASKED: State.STALE,
    State.ANSWERED: State.STALE,
    State.GOLDEN: State.NEEDS_RECHECK,
}


class IllegalMove(ValueError):
    pass


class MissingVersion(KeyError):
    """versions_now has no entry for one of the question's files.

    Raised rather than treated as "moved": a caller that forgot to hash a file would otherwise
    stale every question on it. Pass None for a file that's gone or can't be read."""


def move(q: Question, e: Event, versions_now: dict[str, str | None]) -> None:
    """Make a move now, refusing one the rules don't allow (e.g. promoting an unanswered question).

    A re-check anchors the question to the current version of every file it covers."""
    if e.kind in MOVES:
        allowed, _ = MOVES[e.kind]
        current = state(q, versions_now)
        if current not in allowed:
            raise IllegalMove(f"#{q.id} is {current.value}, so '{e.kind}' isn't allowed")
    if e.kind == "rechecked":
        e = Event(e.kind, e.at, {**e.detail, "anchors": {p: versions_now[p] for p in q.anchors}})
    apply(q, e)


def apply(q: Question, e: Event) -> None:
    """What each event does to a question. This is the only place a question changes.

    Replaying history uses this directly; new moves go through move(), which checks the rules."""
    q.history.append(e)
    if e.kind == "created":
        q.anchors = dict(e.detail["anchors"])
    elif e.kind == "answered":
        q.answered = True
    elif e.kind == "promoted":  # answered correctly, rated good, confirmed it tests the file
        q.golden = True
    elif e.kind == "rechecked":  # passed a re-check: now anchored to the new code
        q.anchors.update(e.detail["anchors"])
    elif e.kind == "retired":  # failed a re-check
        q.retired = True
    # "rated" and "agent_answered" are kept in the history but don't change the state.


def moved_files(q: Question, versions_now: dict[str, str | None]) -> list[str]:
    """Files this question is anchored to that no longer match the version it was checked against."""
    missing = [p for p in q.anchors if p not in versions_now]
    if missing:
        raise MissingVersion(f"#{q.id}: no current version for {', '.join(missing)}")
    return [p for p, checked in q.anchors.items() if checked is None or versions_now[p] != checked]


def state(q: Question, versions_now: dict[str, str | None]) -> State:
    """The one function that decides a question's state."""
    if q.retired:
        return State.RETIRED
    if q.golden:
        current = State.GOLDEN
    else:
        current = State.ANSWERED if q.answered else State.UNASKED
    return CODE_CHANGES[current] if moved_files(q, versions_now) else current


def why(q: Question, versions_now: dict[str, str | None]) -> str:
    """A one-line reason for the state, in plain words."""
    s = state(q, versions_now)
    if s is State.RETIRED:
        return "it failed a re-check after its code changed"
    moved = moved_files(q, versions_now)
    if moved:
        unknown = [p for p in moved if q.anchors[p] is None]
        reason = (
            "has no known checked version"
            if len(unknown) == len(moved)
            else "changed since it was last checked"
        )
        return f"{', '.join(moved)} {reason}"
    return {
        State.GOLDEN: "you answered it correctly, rated it good, and its code hasn't changed",
        State.ANSWERED: "you've answered it, but it isn't golden",
        State.UNASKED: "you haven't answered it yet",
    }[s]


def diagram() -> str:
    """The lifecycle as a Mermaid state diagram, generated from MOVES and CODE_CHANGES."""
    order = list(State)
    ids = {s: s.name.lower() for s in State}
    lines = ["stateDiagram-v2", "    [*] --> unasked : created"]
    lines += [f"    {ids[s]} : {s.value}" for s in State]
    for name, (sources, target) in MOVES.items():
        for s in sorted(sources, key=order.index):
            lines.append(f"    {ids[s]} --> {ids[target]} : {name}")
    for s, to in CODE_CHANGES.items():
        lines.append(f"    {ids[s]} --> {ids[to]} : code changed")
    return "\n".join(lines)
