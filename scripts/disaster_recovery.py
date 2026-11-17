#!/usr/bin/env python3
"""Plan disaster recovery and validate measured drill evidence (no infrastructure actions)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "deploy" / "disaster-recovery-policy.json"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone (prefer UTC/Z)")
    return parsed


def recovery_plan(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_version": policy["schema_version"],
        "objectives": policy["objectives"],
        "recovery_order": policy["recovery_order"],
        "consistency_gates": policy["consistency_gates"],
        "failover_gates": policy["failover_gates"],
        "operator_boundary": (
            "Planning only: does not stop services, restore data, promote replicas, "
            "change DNS, or switch traffic."
        ),
    }


def validate_record(record: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    incident = parse_time(record.get("incident_declared_at"), "incident_declared_at")
    last_good = parse_time(record.get("last_known_good_write_at"), "last_known_good_write_at")
    resumed = parse_time(record.get("traffic_resumed_at"), "traffic_resumed_at")
    if last_good > incident:
        raise ValueError("last_known_good_write_at cannot be after incident_declared_at")
    if resumed < incident:
        raise ValueError("traffic_resumed_at cannot be before incident_declared_at")

    measured_rpo = round((incident - last_good).total_seconds(), 3)
    measured_rto = round((resumed - incident).total_seconds(), 3)
    scope = record.get("scope", "full_platform")
    objectives = policy["objectives"]
    objective = objectives.get(scope)
    if objective is None:
        raise ValueError(f"unsupported drill scope: {scope}")

    checks = record.get("component_checks")
    failures: list[str] = []
    if scope == "full_platform":
        required_components = policy["required_components"]
    else:
        scoped_components = record.get("components_in_scope")
        if (
            not isinstance(scoped_components, list)
            or not scoped_components
            or any(not isinstance(item, str) or not item.strip() for item in scoped_components)
            or len(set(scoped_components)) != len(scoped_components)
        ):
            failures.append(
                "components_in_scope must be a non-empty list of unique component names"
            )
            required_components = []
        else:
            required_components = scoped_components

    if not isinstance(checks, dict):
        failures.append("component_checks must be an object")
        checks = {}
    for component in required_components:
        if checks.get(component) is not True:
            failures.append(f"component check missing or failed: {component}")

    consistency = record.get("consistency_checks", {})
    if not isinstance(consistency, dict):
        failures.append("consistency_checks must be an object")
        consistency = {}
    scope_gates = policy.get("consistency_gates_by_scope", {})
    if not isinstance(scope_gates, dict):
        raise ValueError("consistency_gates_by_scope must be an object")
    consistency_gates = scope_gates.get(scope, policy["consistency_gates"])
    if not isinstance(consistency_gates, list) or any(
        not isinstance(gate, str) or not gate for gate in consistency_gates
    ):
        raise ValueError(f"invalid consistency gates for drill scope: {scope}")
    for gate in consistency_gates:
        if consistency.get(gate) is not True:
            failures.append(f"consistency gate missing or failed: {gate}")

    if scope in ("full_platform", "failover", "failback"):
        failover = record.get("failover_checks", {})
        for gate in policy["failover_gates"]:
            if failover.get(gate) is not True:
                failures.append(f"failover gate missing or failed: {gate}")

    if record.get("reviewer_signoff") is not True:
        failures.append("independent reviewer sign-off missing")
    evidence = record.get("evidence_references")
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        failures.append("evidence_references must contain at least one non-empty reference")

    rpo_target = int(objective["rpo_seconds"])
    rto_target = int(objective["rto_seconds"])
    if measured_rpo > rpo_target:
        failures.append("measured RPO exceeds approved target")
    if measured_rto > rto_target:
        failures.append("measured RTO exceeds approved target")

    return {
        "exercise_id": record.get("exercise_id"),
        "scope": scope,
        "measured_rpo_seconds": measured_rpo,
        "measured_rto_seconds": measured_rto,
        "target_rpo_seconds": rpo_target,
        "target_rto_seconds": rto_target,
        "passed": not failures,
        "failures": failures,
        "note": "A passing record validates submitted evidence only; it does not independently prove production recoverability.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="print the approved recovery plan")
    mode.add_argument("--validate-record", type=Path, help="validate a completed drill JSON record")
    args = parser.parse_args()
    try:
        policy = load_json(args.policy)
        result = recovery_plan(policy) if args.plan else validate_record(
            load_json(args.validate_record), policy
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.plan or result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
