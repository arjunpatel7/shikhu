"""Tests for `shikhu install-skill` and its detect-based placement.

The skill must land in whichever agent dirs already exist, fall back to both
when none do, and `shikhu init` must install it unless told not to.
"""

from shikhu.commands.install_skill import install_skill_files

SKILLS = ("shikhu", "shikhu-inquiry", "shikhu-study")


def _skill_path(base, agent, name="shikhu-study"):
    return base / agent / "skills" / name / "SKILL.md"


def _all_paths(base, *agents):
    return {_skill_path(base, a, n) for a in agents for n in SKILLS}


def test_installs_into_claude_when_only_claude_exists(tmp_path):
    (tmp_path / ".claude").mkdir()

    written = install_skill_files(tmp_path)

    assert set(written) == _all_paths(tmp_path, ".claude")
    assert _skill_path(tmp_path, ".claude").read_text().startswith("---\nname: shikhu-study")
    assert (
        _skill_path(tmp_path, ".claude", "shikhu-inquiry")
        .read_text()
        .startswith("---\nname: shikhu-inquiry")
    )


def test_installs_into_agents_when_only_agents_exists(tmp_path):
    (tmp_path / ".agents").mkdir()

    written = install_skill_files(tmp_path)

    assert set(written) == _all_paths(tmp_path, ".agents")


def test_falls_back_to_both_when_no_agent_dir_exists(tmp_path):
    written = install_skill_files(tmp_path)

    assert set(written) == _all_paths(tmp_path, ".claude", ".agents")


def test_init_installs_the_skill(tmp_path, monkeypatch):
    from shikhu.commands import init as init_mod

    monkeypatch.chdir(tmp_path)
    init_mod.init(no_skill=False)

    # No agent dir existed, so it falls back to both.
    for path in _all_paths(tmp_path, ".claude", ".agents"):
        assert path.exists(), path


def test_init_no_skill_flag_skips_install(tmp_path, monkeypatch):
    from shikhu.commands import init as init_mod

    monkeypatch.chdir(tmp_path)
    init_mod.init(no_skill=True)

    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".agents" / "skills").exists()


def test_every_skill_description_fits_the_1024_char_limit():
    """Agents choose a skill from its description alone; past 1,024 chars it may be cut off."""
    import re

    from shikhu.commands.install_skill import bundled_skills

    for name, path in bundled_skills().items():
        front = path.read_text().split("---")[1]
        block = re.search(r"^description:(.*?)(?=^\S)", front + "\nend:", re.S | re.M).group(1)
        text = " ".join(
            line.strip() for line in block.splitlines() if line.strip() not in ("", ">-")
        )
        assert 0 < len(text) <= 1024, f"{name}: {len(text)} chars"
