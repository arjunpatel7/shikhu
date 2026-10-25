"""Short files need fewer goldens to count as covered, and get fewer questions generated."""

from unittest.mock import patch

from conftest import _insert_questions, runner

import shikhu.store as store
from shikhu.cli import app
from shikhu.commands.utils import COVERED_THRESHOLD, SMALL_FILE_LINES, golden_target


def _file(tmp_path, name, lines, blank_every=0):
    """A file with `lines` non-blank lines (plus blank ones, which don't count)."""
    body = "".join(
        f"x{i} = {i}\n" + ("\n" if blank_every and i % blank_every == 0 else "")
        for i in range(lines)
    )
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def _golden(path, n):
    ids = _insert_questions(path, n=n)
    for qid in ids:
        store.grade_question(qid, user_answer="A", correct=True)
        store.mark_golden(qid)


def test_short_files_need_one_golden_and_long_files_three(tmp_path):
    assert golden_target(_file(tmp_path, "short.py", SMALL_FILE_LINES - 1, blank_every=2)) == 1
    assert golden_target(_file(tmp_path, "long.py", SMALL_FILE_LINES)) == COVERED_THRESHOLD
    assert golden_target(str(tmp_path / "missing.py")) == COVERED_THRESHOLD


def _coverage(paths, *args):
    with patch("shikhu.commands.coverage.get_trackable_files", return_value=paths):
        return runner.invoke(app, ["coverage", *args])


def test_coverage_uses_each_files_own_target(tmp_path):
    short, long = _file(tmp_path, "short.py", 10), _file(tmp_path, "long.py", 200)
    _golden(short, 1)
    _golden(long, 1)
    result = _coverage([short, long])
    assert result.exit_code == 0, result.output
    assert "1/2 files fully covered" in result.output
    assert "1/1" in result.output and "1/3" in result.output


def test_min_still_sets_one_bar_for_every_file(tmp_path):
    short = _file(tmp_path, "short.py", 10)
    _golden(short, 1)
    result = _coverage([short], "--min", "2")
    assert "0/1 files fully covered" in result.output
    assert "1/2" in result.output


def test_check_still_needs_one_golden_per_file(tmp_path):
    long = _file(tmp_path, "long.py", 200)
    _golden(long, 1)
    result = _coverage([long], "--check")
    assert result.exit_code == 0, result.output


def _refresh_asks(paths):
    """Run refresh with generation mocked; returns {file: questions requested}."""
    asked = {}

    def fake_gen(file_path, num_questions=5):
        asked[file_path] = num_questions
        return None  # nothing written; we only want the request

    with (
        patch("shikhu.commands.refresh.get_trackable_files", return_value=paths),
        patch(
            "shikhu.commands.refresh._summarize_one",
            side_effect=lambda p, *a, **k: (p, "fresh", None),
        ),
        patch("shikhu.generator.generate_question_from_file", fake_gen),
    ):
        result = runner.invoke(app, ["refresh"])
    assert result.exit_code == 0, result.output
    return asked


def test_refresh_generates_only_what_reaches_the_target(tmp_path):
    short = _file(tmp_path, "short.py", 10)
    covered_short = _file(tmp_path, "covered.py", 10)
    long = _file(tmp_path, "long.py", 200)
    _golden(covered_short, 1)
    _golden(long, 1)  # one golden toward 3...
    _insert_questions(long, n=1)  # ...and one question waiting
    asked = _refresh_asks([short, covered_short, long])
    assert asked == {short: 1, long: 1}


def test_generate_from_study_defaults_to_the_files_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    short = _file(tmp_path, "short.py", 10)
    rid = store.start_review(short)
    store.log_study_question(rid, "why is short like this?", was_conceptual=True)
    asked = []

    def fake_gen(file_path, num_questions=3, seeds=None, **kw):
        asked.append(num_questions)
        return None

    with patch("shikhu.generator.generate_questions_from_study_seeds", fake_gen):
        runner.invoke(app, ["generate-from-study", short])
        runner.invoke(app, ["generate-from-study", short, "--n", "2"])
    assert asked == [1, 2]
