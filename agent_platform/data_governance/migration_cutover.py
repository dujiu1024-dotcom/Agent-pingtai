"""Fail-closed evidence gate for legacy data migration cutover.

This module evaluates submitted evidence only. It never changes traffic, writes
to either database, or shuts down a legacy system.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_MODES = {"dual_read", "dual_write", "canary"}
_REQUIRED_CHECKS = (
    "backfill_complete",
    "shadow_reads_passed",
    "dual_write_reconciliation_passed",
    "idempotency_verified",
    "target_health_passed",
    "rollback_ready",
)
_SHUTDOWN_CHECKS = (
    "cutover_complete",
    "observation_window_passed",
    "no_legacy_reads",
    "no_legacy_writes",
    "backup_verified",
    "rollback_window_closed",
)


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _bounded_number(value: Any, *, minimum: float, maximum: float) -> bool:
    return type(value) in (int, float) and minimum <= value <= maximum


def validate_cutover_evidence(document: Any) -> dict[str, Any]:
    """Return cutover and legacy-shutdown decisions without executing either."""
    failures: list[str] = []
    if not isinstance(document, dict):
        return {
            "cutover_ready": False,
            "legacy_shutdown_ready": False,
            "failures": ["evidence must be a JSON object"],
            "rollback_triggers": [],
            "execution": "not_performed",
        }

    if not _non_empty(document.get("cutover_id")):
        failures.append("cutover_id: required")
    if document.get("mode") not in _MODES:
        failures.append(f"mode: must be one of {', '.join(sorted(_MODES))}")

    consistency = document.get("consistency")
    mismatch_rate: float | None = None
    if not isinstance(consistency, dict):
        failures.append("consistency: required object")
        consistency = {}
    sampled = consistency.get("sampled_rows")
    mismatched = consistency.get("mismatched_rows")
    missing = consistency.get("missing_rows")
    if type(sampled) is not int or sampled < 1:
        failures.append("consistency.sampled_rows: must be a positive integer")
    for field, value in (("mismatched_rows", mismatched), ("missing_rows", missing)):
        if type(value) is not int or value < 0:
            failures.append(f"consistency.{field}: must be a non-negative integer")
    if type(sampled) is int and sampled > 0 and type(mismatched) is int and mismatched >= 0:
        if type(missing) is int and missing >= 0:
            if mismatched + missing > sampled:
                failures.append("consistency: mismatched_rows + missing_rows cannot exceed sampled_rows")
            else:
                mismatch_rate = (mismatched + missing) / sampled

    threshold = consistency.get("max_mismatch_rate")
    if not _bounded_number(threshold, minimum=0.0, maximum=1.0):
        failures.append("consistency.max_mismatch_rate: must be between 0 and 1")
    elif mismatch_rate is not None and mismatch_rate > threshold:
        failures.append("consistency: observed mismatch rate exceeds approved threshold")

    lag = consistency.get("observed_lag_seconds")
    max_lag = consistency.get("max_lag_seconds")
    if not _bounded_number(lag, minimum=0.0, maximum=float("inf")):
        failures.append("consistency.observed_lag_seconds: must be non-negative")
    if not _bounded_number(max_lag, minimum=0.0, maximum=float("inf")):
        failures.append("consistency.max_lag_seconds: must be non-negative")
    elif _bounded_number(lag, minimum=0.0, maximum=float("inf")) and lag > max_lag:
        failures.append("consistency: observed replication lag exceeds approved maximum")

    checks = document.get("checks")
    if not isinstance(checks, dict):
        failures.append("checks: required object")
        checks = {}
    for field in _REQUIRED_CHECKS:
        if checks.get(field) is not True:
            failures.append(f"checks.{field}: must be true")

    traffic = document.get("traffic")
    if not isinstance(traffic, dict):
        failures.append("traffic: required object")
        traffic = {}
    percent = traffic.get("target_percent")
    if type(percent) is not int or not 0 <= percent <= 100:
        failures.append("traffic.target_percent: must be an integer in [0, 100]")
    if traffic.get("rollback_on_health_failure") is not True:
        failures.append("traffic.rollback_on_health_failure: must be true")
    if traffic.get("rollback_on_consistency_failure") is not True:
        failures.append("traffic.rollback_on_consistency_failure: must be true")

    for field in ("diff_report_ref", "rollback_runbook_ref", "approved_by"):
        if not _non_empty(document.get(field)):
            failures.append(f"{field}: required non-empty evidence reference or approver")

    rollback_triggers: list[str] = []
    if mismatch_rate is not None and _bounded_number(threshold, minimum=0.0, maximum=1.0):
        if mismatch_rate > threshold:
            rollback_triggers.append("consistency_mismatch_threshold_exceeded")
    if (_bounded_number(lag, minimum=0.0, maximum=float("inf"))
            and _bounded_number(max_lag, minimum=0.0, maximum=float("inf"))
            and lag > max_lag):
        rollback_triggers.append("replication_lag_threshold_exceeded")
    if checks.get("target_health_passed") is not True:
        rollback_triggers.append("target_health_check_failed")

    cutover_ready = not failures
    shutdown = document.get("legacy_shutdown")
    shutdown_failures: list[str] = []
    if not cutover_ready:
        shutdown_failures.append("cutover gate must pass before legacy shutdown")
    if not isinstance(shutdown, dict):
        shutdown_failures.append("legacy_shutdown: required object")
        shutdown = {}
    for field in _SHUTDOWN_CHECKS:
        if shutdown.get(field) is not True:
            shutdown_failures.append(f"legacy_shutdown.{field}: must be true")
    if not _non_empty(shutdown.get("approved_by")):
        shutdown_failures.append("legacy_shutdown.approved_by: required")
    if not _non_empty(shutdown.get("evidence_ref")):
        shutdown_failures.append("legacy_shutdown.evidence_ref: required")

    return {
        "cutover_id": document.get("cutover_id"),
        "mode": document.get("mode"),
        "observed_mismatch_rate": mismatch_rate,
        "cutover_ready": cutover_ready,
        "legacy_shutdown_ready": cutover_ready and not shutdown_failures,
        "failures": failures,
        "legacy_shutdown_failures": shutdown_failures,
        "rollback_triggers": rollback_triggers,
        "execution": "not_performed; evidence gate only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.evidence.read_text(encoding="utf-8"))
        result = validate_cutover_evidence(document)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"cutover_ready": False, "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["cutover_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
