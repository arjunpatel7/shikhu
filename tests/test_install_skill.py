"""Tests for `shikhu install-skill` and its detect-based placement.

The skill must land in whichever agent dirs already exist, fall back to both
when none do, and `shikhu init` must install it unless told not to.
"""

from shikhu.commands.install_skill import install_skill_files


def _skill_path(base, agent):
    return base / agent / "skills" / "shikhu-study" / "SKILL.md"


def test_installs_into_claude_when_only_claude_exists(tmp_path):
    (tmp_path / ".claude").mkdir()

    written = install_skill_files(tmp_path)

    assert written == [_skill_path(tmp_path, ".claude")]
    assert _skill_path(tmp_path, ".claude").read_text().startswith("---\nname: shikhu-study")


def test_installs_into_agents_when_only_agents_exists(tmp_path):
    (tmp_path / ".agents").mkdir()

    written = install_skill_files(tmp_path)

    assert written == [_skill_path(tmp_path, ".agents")]


def test_falls_back_to_both_when_no_agent_dir_exists(tmp_path):
    written = install_skill_files(tmp_path)

    assert set(written) == {_skill_path(tmp_path, ".claude"), _skill_path(tmp_path, ".agents")}


def test_init_installs_the_skill(tmp_path, monkeypatch):
    from shikhu.commands import init as init_mod

    monkeypatch.chdir(tmp_path)
    init_mod.init(no_skill=False)

    # No agent dir existed, so it falls back to both.
    assert _skill_path(tmp_path, ".claude").exists()
    assert _skill_path(tmp_path, ".agents").exists()


def test_init_no_skill_flag_skips_install(tmp_path, monkeypatch):
    from shikhu.commands import init as init_mod

    monkeypatch.chdir(tmp_path)
    init_mod.init(no_skill=True)

    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".agents" / "skills").exists()
