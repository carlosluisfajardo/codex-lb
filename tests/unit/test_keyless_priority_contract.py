from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_keyless_priority_contract(tmp_path: Path) -> None:
    """Run the synthetic contract without changing pytest's cached app state."""
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "TMP": str(tmp_path),
            "TEMP": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
            "CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE": str(repo_root / "config/additional_quota_registry.json"),
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "tests.fixtures.keyless_priority_contract", "-v"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
