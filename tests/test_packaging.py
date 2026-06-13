"""Tests for #41 — shikhu works when installed as a tool in an arbitrary repo.

The prompts that drive generation must load from inside the package, not the
current working directory.
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
