"""shikhu install-skill — drop the /shikhu-study skill into an agent's skills dir.

The skill markdown is bundled in the package (src/shikhu/_skill/SKILL.md), so
installing it requires the CLI to be present and keeps the two version-matched.
Agents read skills from different locations: Claude Code uses `.claude/skills/`,
while Codex/Cursor/OpenCode use the shared `.agents/skills/`. We install into
whichever already exist in the target, falling back to both when none do.
"""

from pathlib import Path

import typer

from shikhu.commands.utils import console

SKILL_NAME = "shikhu-study"

# Marker dir that signals an agent is in use -> the skills dir to install into.
AGENT_SKILL_DIRS = {
    ".claude": ".claude/skills",  # Claude Code
    ".agents": ".agents/skills",  # Codex, Cursor, OpenCode, ... (shared convention)
}

_SKILL_SOURCE = Path(__file__).parent.parent / "_skill" / "SKILL.md"


def _targets(base: Path) -> list[Path]:
    """Skills dirs to install into under `base` (simple detect, else both)."""
    detected = [
        base / skills for marker, skills in AGENT_SKILL_DIRS.items() if (base / marker).is_dir()
    ]
    if detected:
        return detected
    return [base / skills for skills in AGENT_SKILL_DIRS.values()]


def install_skill_files(base: Path) -> list[Path]:
    """Copy the bundled skill into the detected agent dirs under `base`.

    Returns the list of SKILL.md paths written.
    """
    source = _SKILL_SOURCE.read_text()
    written: list[Path] = []
    for skills_dir in _targets(base):
        dest = skills_dir / SKILL_NAME / "SKILL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source)
        written.append(dest)
    return written


def install_skill(
    global_: bool = typer.Option(
        False, "--global", help="Install into your home dir (~/) instead of this project."
    ),
):
    """Install the /shikhu-study skill for your coding agent."""
    base = Path.home() if global_ else Path.cwd()
    written = install_skill_files(base)
    for dest in written:
        console.print(f"  [green]>[/green] Installed /{SKILL_NAME} skill → {dest}")
    console.print(
        f"\n  Use it in your agent with [bold]/{SKILL_NAME} <file>[/bold] "
        "(restart the agent if it doesn't show up yet)."
    )
