"""shikhu install-hook — check your understanding of a change before you push it.

Writes a git pre-push hook that runs `shikhu coverage --since <base> --check`: in warn mode
it lists the changed files you haven't been quizzed on and pushes anyway; in block mode it
stops the push until you have. The hook runs locally against coverage.db, so it can't be a
CI check: it's the last step before your code leaves your machine.
"""

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from shikhu.commands.utils import console

_HOOK_SOURCE = Path(__file__).parent.parent / "_hook" / "pre-push"
HOOK_MARKER = "# shikhu push gate."
MODES = ("warn", "block")


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _shikhu_command() -> str:
    """How the hook should call shikhu: this install's full path, quoted for sh."""
    path = shutil.which("shikhu")
    return shlex.quote(path) if path else shlex.join([sys.executable, "-m", "shikhu"])


def interactive() -> bool:
    """Only ask when someone is at the keyboard, not in scripts or an agent's shell."""
    return sys.stdin.isatty()


def in_git_repo() -> bool:
    return bool(_git("rev-parse", "--is-inside-work-tree"))


def render_hook() -> str:
    return _HOOK_SOURCE.read_text().replace("__SHIKHU__", _shikhu_command())


def install_hook_file(mode: str) -> tuple[Path, bool]:
    """Write the hook and save `mode`. Returns (where it went, whether it's standalone).

    Never overwrites another tool's pre-push hook (Entire, pre-commit, Husky via
    core.hooksPath): it goes beside it as shikhu-pre-push, for that hook to call."""
    common = Path(_git("rev-parse", "--git-common-dir")).resolve()
    pre_push = common / "hooks" / "pre-push"
    managed = bool(_git("config", "--get", "core.hooksPath"))
    ours = pre_push.exists() and HOOK_MARKER in pre_push.read_text()
    standalone = not managed and (ours or not pre_push.exists())
    target = pre_push if standalone else pre_push.with_name("shikhu-pre-push")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_hook())
    target.chmod(0o755)
    _git("config", "--local", "shikhu.gate.mode", mode)
    return target, standalone


def ask_mode() -> str | None:
    """Ask how the gate should behave. None means don't install it."""
    console.print(
        "\n  Check that you understand your changes before each [bold]git push[/bold]?\n"
        "    [bold]w[/bold]  warn: list the changes you haven't been quizzed on, then push anyway\n"
        "    [bold]b[/bold]  block: stop the push until you've quizzed on them\n"
        "    [bold]s[/bold]  skip: don't install the hook (add it later with shikhu install-hook)"
    )
    choice = ""
    while choice not in ("w", "b", "s"):
        choice = typer.prompt("  Choose w, b or s", default="w").strip().lower()[:1]
    return {"w": "warn", "b": "block"}.get(choice)


def report(target: Path, standalone: bool, mode: str) -> None:
    if standalone:
        console.print(f"  [green]>[/green] Installed the push gate ({mode} mode) → {target}")
    else:
        console.print(
            f"  [green]>[/green] Wrote the push gate ({mode} mode) → {target}\n"
            "    This repo already has a pre-push hook, so it wasn't replaced. "
            "Add this line to that hook to run the gate:\n"
            f'      {shlex.quote(str(target))} "$@" </dev/null || exit 1'
        )
    other = "block" if mode == "warn" else "warn"
    console.print(f"    [dim]Switch any time: git config shikhu.gate.mode {other}[/dim]")


def install_hook(
    mode: str = typer.Option(
        None, "--mode", help="warn: list unquizzed changes and push anyway. block: stop the push."
    ),
):
    """Check your understanding of a change before you push it (a git pre-push hook)."""
    if not in_git_repo():
        console.print("[red]Not a git repository.[/red] Run this inside the repo you push from.")
        raise typer.Exit(code=1)
    if mode is None:
        mode = ask_mode() if interactive() else "warn"
        if mode is None:
            return
    if mode not in MODES:
        console.print(f"[red]--mode must be warn or block, not {mode}.[/red]")
        raise typer.Exit(code=2)
    report(*install_hook_file(mode), mode)
