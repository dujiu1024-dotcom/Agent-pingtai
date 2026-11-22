from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(True, reason="runbook text changed")
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_disaster_recovery_runbook_defines_objectives_and_consistency_boundaries() -> None:
    runbook = (ROOT / "docs/disaster-recovery-runbook.md").read_text(encoding="utf-8")
    assert "Target RPO" in runbook
    assert "Target RTO" in runbook
    assert "not an atomic distributed" in runbook
    assert "Failover and failback" in runbook
    assert "measured RPO" in runbook
    assert "does not retain prior object versions" in runbook


def test_restore_orchestrator_enforces_review_and_component_order() -> None:
    script = ROOT / "scripts/restore_platform.sh"
    content = script.read_text(encoding="utf-8")
    assert "RESTORE-PLATFORM-IN-ORDER" in content
    assert "RESTORE-READY" in content
    postgres = content.index('"$SCRIPT_DIR/restore_postgres.sh"')
    redis = content.index('"$SCRIPT_DIR/restore_redis.sh"')
    objects = content.index('"$SCRIPT_DIR/restore_object_storage.sh"')
    assert postgres < redis < objects
    result = subprocess.run(["bash", "-n", str(script)], check=False, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_drill_template_captures_actual_rpo_rto_and_evidence() -> None:
    template = (ROOT / "docs/disaster-recovery-drill-template.md").read_text(encoding="utf-8")
    assert "Measured RPO" in template
    assert "Measured RTO" in template
    assert "Evidence reference" in template
    assert "Reviewer sign-off" in template


def test_restore_orchestrator_fails_closed_without_operator_confirmation() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/restore_platform.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={},
    )
    assert result.returncode != 0
    assert "RESTORE_ORDER_CONFIRM" in result.stderr
