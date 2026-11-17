# Issue #666 / A-666-08 — Rollback drill acceptance

## Implemented rollback primitive

`CutoverGateway.rollback_to_legacy()` replaces the immutable routing policy with a disabled, zero-percent policy on the existing gateway instance. Subsequent requests resolved by that process route to the legacy ASGI application. The operation does not recreate either application.

The focused drill tests two independent contracts:

1. A consistency failure during canary advances the decision to `ROLLED_BACK`; the write plan makes legacy authoritative and stops target writes/replication.
2. The same `CutoverGateway` instance first routes an allowlisted tenant to the platform and, after `rollback_to_legacy()`, routes the next request to legacy.

Run:

```bash
python -m unittest tests.test_issue_666_a_666_08_rollback_drill -v
ruff check agent_platform/switching/gateway.py tests/test_issue_666_a_666_08_rollback_drill.py
```

## Five-minute and production boundary

The automated test validates in-process routing and policy contracts only. It does not prove a production five-minute rollback, multi-worker policy fan-out, request draining, PostgreSQL-to-SQLite reverse data replay, or real legacy service health. Multi-worker hosts must publish the disabled policy to every worker and verify acknowledgements. Before production cutover, operators must separately prove reverse data reconciliation and write fencing; this PR does not claim that a PostgreSQL-to-SQLite backfill adapter currently exists or that a live rollback was rehearsed.

A-666-08 therefore gains an executable rollback contract, but remains field-acceptance pending until a deployment drill records per-worker routing acknowledgements, legacy health, reverse-sync row/checksum parity, and elapsed time under 300 seconds.
