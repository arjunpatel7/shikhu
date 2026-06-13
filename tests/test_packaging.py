"""Tests for #41 — shikhu works when installed as a tool in an arbitrary repo.

The prompts that drive generation must load from inside the package (not the
current working directory), and loading must not fail when the package dir is
read-only (e.g. site-packages), where the dev-only prompt archive can't be written.
"""

import subprocess
import sys


def test_generator_imports_outside_repo(tmp_path):
    """Importing the generator from a foreign cwd must not raise.

    `load_prompts()` runs at import time, so a cwd-relative prompts path makes
    `import shikhu.generator` crash in any repo that isn't this one — which is
    exactly how the tool is used once installed elsewhere.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import shikhu.generator"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_load_prompts_ignores_archive_write_failure(monkeypatch, tmp_path):
    """A read-only archive dir (installed site-packages) must not break loading.

    Auto-archiving the prompts file on a version bump is a dev-only convenience;
    when the destination can't be written, load_prompts should swallow it and
    still return the parsed prompts.
    """
    import shikhu.generator as g

    # Point the archive at an empty dir so the copy is attempted, then make the
    # copy fail as it would against a read-only filesystem.
    monkeypatch.setattr(g, "ARCHIVE_DIR", tmp_path / "archive")

    def _boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(g.shutil, "copy2", _boom)

    prompts = g.load_prompts()
    assert "version" in prompts
