#!/usr/bin/env python3
"""Fail-closed evaluator for paired legacy-vs-platform performance reports.

Reports must be collected with the same workload and equivalent hardware.
This tool never fabricates missing measurements or treats synthetic smoke data
as production evidence.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

REQUIRED_WORKLOAD_FIELDS = (
    "workers", "operations_per_worker", "dataset_size",
    "warmup_operations", "measurement_seconds",
)
REQUIRED_ENVIRONMENT_FIELDS = (
    "cpu_model", "cpu_count", "memory_mb", "os",
    "python_version", "storage_profile",
)
METRICS = {
    "run_throughput_ops_per_second": "higher",
    "blackboard_qps": "higher",
    "redis_queue_latency_p95_ms": "lower",
    "postgres_pool_wait_p95_ms": "lower",
}


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _missing_fields(report: dict[str, Any], section: str, fields: tuple[str, ...]) -> list[str]:
    value = report.get(section)
    if not isinstance(value, dict):
        return [f"{section}.{field}" for field in fields]
    return [
        f"{section}.{field}" for field in fields
        if field not in value or value[field] in (None, "")
    ]


def evaluate_parity(
    legacy: dict[str, Any],
    platform: dict[str, Any],
    *,
    max_regression_pct: float = 0.0,
) -> dict[str, Any]:
    """Compare equivalent reports; return pass/fail/incomplete without guessing."""
    if (
        isinstance(max_regression_pct, bool)
        or not isinstance(max_regression_pct, (int, float))
        or not math.isfinite(max_regression_pct)
        or not 0 <= max_regression_pct <= 100
    ):
        raise ValueError("max_regression_pct must be finite and between 0 and 100")
    missing: list[str] = []
    for label, report in (("legacy", legacy), ("platform", platform)):
        if not isinstance(report, dict):
            missing.append(f"{label}:report")
            continue
        missing.extend(
            f"{label}:{field}"
            for field in _missing_fields(report, "workload", REQUIRED_WORKLOAD_FIELDS)
        )
        missing.extend(
            f"{label}:{field}"
            for field in _missing_fields(report, "environment", REQUIRED_ENVIRONMENT_FIELDS)
        )
        metrics = report.get("metrics")
        if not isinstance(metrics, dict):
            missing.extend(f"{label}:metrics.{name}" for name in METRICS)
        else:
            missing.extend(
                f"{label}:metrics.{name}"
                for name in METRICS
                if not _positive_number(metrics.get(name))
            )
    if missing:
        return {"status": "incomplete", "missing": sorted(missing), "comparisons": {}}

    mismatches: list[str] = []
    for section, fields in (
        ("workload", REQUIRED_WORKLOAD_FIELDS),
        ("environment", REQUIRED_ENVIRONMENT_FIELDS),
    ):
        for field in fields:
            if legacy[section][field] != platform[section][field]:
                mismatches.append(f"{section}.{field}")
    if mismatches:
        return {
            "status": "incomplete",
            "reason": "legacy and platform reports are not workload/hardware equivalent",
            "mismatches": mismatches,
            "comparisons": {},
        }

    comparisons: dict[str, Any] = {}
    failures: list[str] = []
    tolerance = max_regression_pct / 100.0
    for name, direction in METRICS.items():
        old = float(legacy["metrics"][name])
        new = float(platform["metrics"][name])
        limit = old * (1.0 - tolerance if direction == "higher" else 1.0 + tolerance)
        passed = new >= limit if direction == "higher" else new <= limit
        comparisons[name] = {
            "direction": direction,
            "legacy": old,
            "platform": new,
            "allowed_regression_pct": max_regression_pct,
            "limit": round(limit, 6),
            "passed": passed,
        }
        if not passed:
            failures.append(name)
    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", required=True, type=Path, help="Legacy SQLite benchmark JSON")
    parser.add_argument("--platform", required=True, type=Path, help="New platform benchmark JSON")
    parser.add_argument("--max-regression-pct", type=float, default=0.0)
    args = parser.parse_args()
    if (
        not math.isfinite(args.max_regression_pct)
        or not 0 <= args.max_regression_pct <= 100
    ):
        parser.error("--max-regression-pct must be finite and between 0 and 100")
    try:
        legacy = json.loads(args.legacy.read_text(encoding="utf-8"))
        platform = json.loads(args.platform.read_text(encoding="utf-8"))
        result = evaluate_parity(
            legacy, platform, max_regression_pct=args.max_regression_pct
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "incomplete", "reason": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"pass": 0, "fail": 1, "incomplete": 2}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
