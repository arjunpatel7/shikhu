"""Tests for the question lifecycle: states, moves, and what code changes do."""

import pytest

from shikhu.lifecycle import (
    CODE_CHANGES,
    MOVES,
    Event,
    IllegalMove,
    MissingVersion,
    Question,
    State,
    apply,
    diagram,
    move,
    state,
    why,
)

V1 = {"a.py": "v1", "b.py": "v1"}
V2 = {"a.py": "v2", "b.py": "v1"}  # a.py changed


def _question(*kinds: str) -> Question:
    q = Question(id=1)
    apply(q, Event("created", "t0", {"anchors": dict(V1)}))
    for k in kinds:
        apply(q, Event(k, "t"))
    return q


def _in(s: State) -> tuple[Question, dict]:
    """A question in state `s`, with the file versions that put it there."""
    return {
        State.UNASKED: lambda: (_question(), V1),
        State.ANSWERED: lambda: (_question("answered"), V1),
        State.GOLDEN: lambda: (_question("answered", "promoted"), V1),
        State.STALE: lambda: (_question("answered"), V2),
        State.NEEDS_RECHECK: lambda: (_question("answered", "promoted"), V2),
        State.RETIRED: lambda: (_question("answered", "promoted", "retired"), V2),
    }[s]()


def test_every_state_is_reachable():
    for s in State:
        q, now = _in(s)
        assert state(q, now) is s


@pytest.mark.parametrize("name", MOVES)
def test_each_move_leads_where_moves_says(name):
    """MOVES is what the diagram is drawn from, so it must match what state() computes."""
    sources, target = MOVES[name]
    for s in sources:
        q, now = _in(s)
        move(q, Event(name, "t1"), now)
        assert state(q, now) is target, f"{name} from {s.value}"


@pytest.mark.parametrize("name", MOVES)
def test_each_move_is_refused_from_other_states(name):
    sources, _ = MOVES[name]
    for s in set(State) - sources:
        q, now = _in(s)
        with pytest.raises(IllegalMove):
            move(q, Event(name, "t1"), now)
        assert state(q, now) is s  # a refused move changes nothing


def test_code_changes_lead_where_code_changes_says():
    for s, to in CODE_CHANGES.items():
        q, _ = _in(s)
        assert state(q, V2) is to, s.value
    for s in set(State) - set(CODE_CHANGES):  # already moved: another change keeps it there
        q, _ = _in(s)
        assert state(q, {"a.py": "v3", "b.py": "v3"}) is s


def test_undoing_the_change_restores_the_question():
    """Switching back to the branch the question was checked on puts it straight back."""
    q, _ = _in(State.GOLDEN)
    assert state(q, V2) is State.NEEDS_RECHECK
    assert state(q, V1) is State.GOLDEN


def test_a_recheck_anchors_every_file_to_the_current_code():
    q, now = _in(State.NEEDS_RECHECK)
    move(q, Event("rechecked", "t1", {"anchors": {"a.py": "stale"}}), now)
    assert q.anchors == V2
    assert state(q, V2) is State.GOLDEN
    assert state(q, V1) is State.NEEDS_RECHECK  # now it's the old code that differs


def test_agent_answers_and_ratings_are_kept_but_change_nothing():
    q, now = _in(State.UNASKED)
    move(q, Event("agent_answered", "t1", {"by": "claude-code", "correct": True}), now)
    move(q, Event("rated", "t2", {"rating": "good"}), now)
    assert state(q, now) is State.UNASKED
    assert [e.kind for e in q.history] == ["created", "agent_answered", "rated"]


def test_an_unknown_checked_version_counts_as_moved():
    q = Question(id=1)
    apply(q, Event("created", "t0", {"anchors": {"a.py": None}}))
    assert state(q, {"a.py": "v1"}) is State.STALE
    assert "no known checked version" in why(q, {"a.py": "v1"})


def test_a_file_missing_from_versions_now_raises():
    q, _ = _in(State.GOLDEN)
    with pytest.raises(MissingVersion):
        state(q, {"a.py": "v1"})
    assert state(q, {"a.py": "v1", "b.py": None}) is State.NEEDS_RECHECK  # None: file gone


def test_why_names_the_changed_file():
    q, _ = _in(State.GOLDEN)
    assert why(q, V2) == "a.py changed since it was last checked"
    assert "hasn't changed" in why(q, V1)


def test_diagram_draws_every_move_and_code_change():
    d = diagram()
    for name, (sources, target) in MOVES.items():
        for s in sources:
            assert f"{s.name.lower()} --> {target.name.lower()} : {name}" in d
    for s, to in CODE_CHANGES.items():
        assert f"{s.name.lower()} --> {to.name.lower()} : code changed" in d
