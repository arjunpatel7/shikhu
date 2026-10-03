"""shikhu install-skill — drop shikhu's skills into an agent's skills dir.

The skill markdown is bundled in the package (src/shikhu/_skill/<name>/SKILL.md), so
installing it requires the CLI to be present and keeps the two version-matched.
Agents read skills from different locations: Claude Code uses `.claude/skills/`,
while Codex/Cursor/OpenCode use the shared `.agents/skills/`. We install into
whichever already exist in the target, falling back to both when none do.
"""

from pathlib import Path

import typer

from shikhu.commands.utils import console

# Marker dir that signals an agent is in use -> the skills dir to install into.
AGENT_SKILL_DIRS = {
    ".claude": ".claude/skills",  # Claude Code
    ".agents": ".agents/skills",  # Codex, Cursor, OpenCode, ... (shared convention)
}

_SKILL_ROOT = Path(__file__).parent.parent / "_skill"


def bundled_skills() -> dict[str, Path]:
    """{skill name: SKILL.md path} for every skill shipped in the wheel.

    Discovered rather than listed so adding a skill is a new directory, not an edit here."""
    return {
        d.name: d / "SKILL.md" for d in sorted(_SKILL_ROOT.iterdir()) if (d / "SKILL.md").is_file()
    }


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
    written: list[Path] = []
    for skills_dir in _targets(base):
        for name, source in bundled_skills().items():
            dest = skills_dir / name / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(source.read_text())
            written.append(dest)
    return written


def install_skill(
    global_: bool = typer.Option(
        False, "--global", help="Install into your home dir (~/) instead of this project."
    ),
):
    """Install shikhu's skills for your coding agent."""
    base = Path.home() if global_ else Path.cwd()
    written = install_skill_files(base)
    for dest in written:
        console.print(f"  [green]>[/green] Installed /{dest.parent.name} skill → {dest}")
    console.print(
        "\n  [bold]/shikhu-study <file>[/bold] walks you through a file. "
        "[bold]/shikhu-inquiry[/bold] answers a question about your code and banks it — "
        "invoke it directly any time, or just ask and it will often trigger on its own."
        "\n  [dim](restart the agent if they don't show up yet)[/dim]"
    )
