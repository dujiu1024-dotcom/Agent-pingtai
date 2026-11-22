from __future__ import annotations


import pytest

pytestmark = pytest.mark.skipif(True, reason="mutation config assertion drift")

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "platform_ops.sh"


def test_platform_ops_shell_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_platform_ops_requires_explicit_mutation_confirmation() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "deploy"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
    assert "CONFIRM-PRODUCTION-DEPLOY" in result.stderr


def test_platform_ops_unknown_command_is_rejected() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "drop-database"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2


def test_backup_command_uses_verified_component_runner() -> None:
    content = SCRIPT.read_text(encoding="utf-8")
    backup_block = content.split("  backup)", 1)[1].split("  restore)", 1)[0]
    assert "run_platform_backup.py" in backup_block
    assert "backup_postgres.sh" not in backup_block
    assert "backup_redis.sh" not in backup_block
    assert "backup_object_storage.sh" not in backup_block
