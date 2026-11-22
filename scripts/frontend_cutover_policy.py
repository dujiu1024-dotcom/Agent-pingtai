"""Fail-closed validation for a frontend canary/rollback evidence record.

This module validates a proposed entry-point transition. It never changes proxy
configuration, traffic, application data, or running services.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

STAGES = {"development", "internal", "acceptance", "default"}
ENTRYPOINTS = {"legacy", "candidate"}
ROLLBACK_ACTION = "switch_frontend_entry_only"


class CutoverRecordError(ValueError):
    """Raised when a cutover record is incomplete or unsafe."""


def validate_record(record: dict[str, Any]) -> dict[str, Any]:
    required = {
        "stage",
        "legacy_artifact_sha",
        "candidate_artifact_sha",
        "active_entry",
        "rollback_target_sha",
        "config_checksum",
        "canary_percent",
        "sticky_routing",
        "health_gates",
        "p0_p1_open",
        "preserve_in_flight_work",
        "business_data_rollback",
    }
    missing = sorted(required - record.keys())
    if missing:
        raise CutoverRecordError(f"missing required fields: {', '.join(missing)}")

    if record["stage"] not in STAGES:
        raise CutoverRecordError("stage must be development, internal, acceptance, or default")
    for field in ("legacy_artifact_sha", "candidate_artifact_sha", "rollback_target_sha", "config_checksum"):
        value = record[field]
        if not isinstance(value, str) or not value.strip():
            raise CutoverRecordError(f"{field} must be a non-empty string")
    if record["legacy_artifact_sha"] == record["candidate_artifact_sha"]:
        raise CutoverRecordError("legacy and candidate artifact SHAs must differ")
    if record["rollback_target_sha"] != record["legacy_artifact_sha"]:
        raise CutoverRecordError("rollback target must be the recorded legacy artifact SHA")
    if record["active_entry"] not in ENTRYPOINTS:
        raise CutoverRecordError("active_entry must be legacy or candidate")

    percent = record["canary_percent"]
    if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
        raise CutoverRecordError("canary_percent must be an integer between 0 and 100")
    if record["stage"] == "development" and percent != 0:
        raise CutoverRecordError("development stage must not route canary traffic")
    if record["stage"] == "internal" and not 1 <= percent <= 10:
        raise CutoverRecordError("internal stage canary_percent must be between 1 and 10")
    if record["stage"] == "acceptance" and not 1 <= percent <= 50:
        raise CutoverRecordError("acceptance stage canary_percent must be between 1 and 50")
    if record["stage"] == "default" and (percent != 100 or record["active_entry"] != "candidate"):
        raise CutoverRecordError("default stage requires candidate active at 100 percent")

    if record["sticky_routing"] is not True:
        raise CutoverRecordError("sticky_routing must be true to prevent cross-version refreshes")
    gates = record["health_gates"]
    if not isinstance(gates, dict) or not gates:
        raise CutoverRecordError("health_gates must be a non-empty object")
    failed = sorted(name for name, passed in gates.items() if passed is not True)
    if failed and record["active_entry"] == "candidate":
        action = ROLLBACK_ACTION
    else:
        action = "hold_or_continue"
    if record["p0_p1_open"] is True and record["active_entry"] == "candidate":
        action = ROLLBACK_ACTION
    elif record["p0_p1_open"] not in (True, False):
        raise CutoverRecordError("p0_p1_open must be boolean")

    if record["preserve_in_flight_work"] is not True:
        raise CutoverRecordError("rollback must preserve in-flight Task/Run work")
    if record["business_data_rollback"] is not False:
        raise CutoverRecordError("frontend rollback must never roll back business data")

    return {
        "decision": action,
        "failed_health_gates": failed,
        "rollback_target_sha": record["legacy_artifact_sha"] if action == ROLLBACK_ACTION else None,
        "preserve_in_flight_work": True,
        "business_data_rollback": False,
        "mutates_live_system": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-record", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = json.loads(args.validate_record.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise CutoverRecordError("record root must be a JSON object")
        result = validate_record(record)
    except (OSError, json.JSONDecodeError, CutoverRecordError) as exc:
        parser.exit(2, f"invalid cutover record: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
