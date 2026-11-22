#!/usr/bin/env python3
"""Build a fail-closed L4-T07 Switch performance/failover evidence report.

The report is generated only after the workflow's E2E, benchmark, fault-drill and
PostgreSQL/Redis worker-recovery test commands have succeeded.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FAILOVER_SUITES = [
    "tests.test_issue_665_s_665_03_fault_drill",
    "tests.test_issue_665_a_665_07_fault_degradation",
    "tests.test_postgres_redis_worker_recovery_integration",
]
PERFORMANCE_METRICS = ("run_lifecycle_transaction", "blackboard_write_read")


def build_report(benchmark: dict[str, Any]) -> dict[str, Any]:
    configuration = benchmark.get("configuration")
    if not isinstance(configuration, dict):
        raise ValueError("benchmark configuration is missing")
    workers = configuration.get("workers")
    operations_per_worker = configuration.get("operations_per_worker")
    if (not isinstance(workers, int) or isinstance(workers, bool) or workers < 1
            or not isinstance(operations_per_worker, int)
            or isinstance(operations_per_worker, bool) or operations_per_worker < 1):
        raise ValueError("benchmark worker/operation configuration is invalid")
    expected = workers * operations_per_worker

    legacy = benchmark.get("legacy_sqlite", {})
    postgres = benchmark.get("platform_postgres_pool", {})
    redis = benchmark.get("platform_redis_queue", {})
    if postgres.get("status") != "measured":
        raise ValueError("PostgreSQL benchmark was not measured")
    if redis.get("status") != "measured":
        raise ValueError("Redis benchmark was not measured")

    measured: dict[str, Any] = {}
    for backend, source, metric_key in (
        ("legacy_sqlite", legacy, None),
        ("platform_postgres", postgres.get("metrics", {}), "metrics"),
    ):
        metrics = source if metric_key is None else source
        for name in PERFORMANCE_METRICS:
            value = metrics.get(name)
            if not isinstance(value, dict) or value.get("operations") != expected:
                raise ValueError(f"{backend}.{name} expected {expected} measured operations")
            latency = value.get("latency_ms", {})
            if not all(
                isinstance(latency.get(key), (int, float))
                and not isinstance(latency.get(key), bool)
                and math.isfinite(latency[key])
                and latency[key] >= 0
                for key in ("p50", "p95", "p99", "max")
            ):
                raise ValueError(f"{backend}.{name} latency distribution is incomplete")
            throughput = value.get("ops_per_second")
            if (not isinstance(throughput, (int, float))
                    or isinstance(throughput, bool)
                    or not math.isfinite(throughput) or throughput <= 0):
                raise ValueError(f"{backend}.{name} throughput is invalid")
            measured[f"{backend}.{name}"] = {
                "operations": value["operations"],
                "ops_per_second": throughput,
                "latency_ms": latency,
            }

    redis_metrics = redis.get("metrics", {})
    if redis_metrics.get("messages") != expected:
        raise ValueError(f"Redis queue expected {expected} acknowledged messages")
    redis_latency = redis_metrics.get("latency_ms", {})
    if not all(
        isinstance(redis_latency.get(key), (int, float))
        and not isinstance(redis_latency.get(key), bool)
        and math.isfinite(redis_latency[key])
        and redis_latency[key] >= 0
        for key in ("p50", "p95", "p99", "max")
    ):
        raise ValueError("Redis queue latency distribution is incomplete")
    redis_throughput = redis_metrics.get("ops_per_second")
    if (not isinstance(redis_throughput, (int, float))
            or isinstance(redis_throughput, bool)
            or not math.isfinite(redis_throughput) or redis_throughput <= 0):
        raise ValueError("Redis queue throughput is invalid")

    return {
        "schema_version": 1,
        "acceptance": "L4-T07",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "automated_ci_status": "passed",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "workers": workers,
            "operations_per_worker": operations_per_worker,
            "operations_per_backend": expected,
        },
        "performance": {
            "status": "measured",
            "metrics": measured,
            "redis_queue": {
                "messages": redis_metrics["messages"],
                "ops_per_second": redis_throughput,
                "latency_ms": redis_latency,
            },
            "legacy_comparison": "diagnostic_only",
            "parity_conclusion": "incomplete",
            "parity_reason": (
                "CI synthetic SQLite and ephemeral PostgreSQL/Redis services do not share "
                "a production hardware, storage, dataset and workload fingerprint."
            ),
        },
        "failover": {
            "status": "automated_adapter_drills_passed",
            "suites": FAILOVER_SUITES,
            "verified_paths": [
                "Redis publish outage opens circuit breaker and preserves process-local replay only",
                "PostgreSQL primary outage blocks quota/model dispatch; simulated standby promotion permits one dispatch",
                "Vault outage fails secret resolution closed without stale-secret fallback",
                "PostgreSQL receipt lease and Redis pending message recover after worker restart",
            ],
            "production_failover": "not_tested",
            "limitations": [
                "No live PostgreSQL HA promotion or topology/DNS endpoint failover was performed",
                "No Redis process/cluster failover or Vault cluster switchover was performed",
                "No production traffic, credentials, customer data or physical device was used",
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))
    report = build_report(benchmark)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print("SWITCH_L4_T07_ACCEPTANCE=PASSED (automated CI adapter drills)")
    print("SWITCH_L4_T07_PRODUCTION_FAILOVER=NOT_TESTED")
    print("SWITCH_L4_T07_PERFORMANCE_PARITY=INCOMPLETE")


if __name__ == "__main__":
    main()
