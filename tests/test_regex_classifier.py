"""Tests for regex prompt classifier."""

from shikhu.classifier import classify as regex_classify

# --- Clearly conceptual ---


def test_why_question():
    assert regex_classify("why does the staleness check use hashing?") == "conceptual"


def test_explain():
    assert regex_classify("explain the golden question system") == "conceptual"


def test_should_i():
    assert regex_classify("should I use a separate DB for prompts?") == "conceptual"


def test_design_decision():
    assert regex_classify("what was the design decision behind golden questions?") == "conceptual"


# --- Clearly not conceptual ---


def test_greeting():
    assert regex_classify("yeah looks good") == "not_conceptual"


def test_acknowledgment():
    assert regex_classify("ok thanks") == "not_conceptual"


def test_direct_command():
    assert regex_classify("fix the bug in store.py") == "not_conceptual"


def test_show_me():
    assert regex_classify("show me the coverage report") == "not_conceptual"


def test_simple_yes():
    assert regex_classify("yep") == "not_conceptual"


def test_commit_request():
    assert regex_classify("commit this and push it") == "not_conceptual"


# --- Edge cases ---


def test_command_with_conceptual_word():
    """'fix' + 'it' triggers anti-pattern, no strong conceptual signal."""
    assert regex_classify("fix it so the architecture loads") == "not_conceptual"


def test_why_overrides_antipattern():
    """Strong conceptual signal ('why') wins over anti-pattern."""
    assert regex_classify("ok but why does it work that way?") == "conceptual"


def test_case_insensitive():
    assert regex_classify("WHY is the hash stored in the files table?") == "conceptual"


def test_empty_string():
    assert regex_classify("") == "not_conceptual"


def test_short_ambiguous():
    assert regex_classify("hmm") == "not_conceptual"


def test_short_query_filtered():
    """Very short prompts are never conceptual, even with trigger words."""
    assert regex_classify("why?") == "not_conceptual"
    assert regex_classify("how does it") == "not_conceptual"


def test_just_long_enough():
    assert regex_classify("why does this work") == "conceptual"
