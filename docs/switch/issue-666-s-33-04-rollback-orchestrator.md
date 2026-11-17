# S-33-04 / S-666-04 — 回滚执行编排

## Scope

This adds a fail-closed operator runner for the cutover rollback sequence. It complements the in-process routing rollback in PR #703; it does not claim that an application-process policy update alone restores the legacy data store.

## Required operator sequence

1. `freeze_writes`: stop accepting writes to the new platform and pause consumers/workers.
2. `reverse_sync`: run the deployment-owned PostgreSQL → legacy SQLite backfill against a consistent PostgreSQL snapshot.
3. `verify_parity`: compare the restored SQLite snapshot with the frozen PostgreSQL snapshot; non-zero means stop.
4. `switch_gateway_to_legacy`: switch API and Workflow entry points to the legacy deployment.
5. `verify_legacy_health`: prove the legacy service and core read/write path are healthy.
6. `unfreeze_legacy_writes`: enable writes only after route and health checks succeed.

The runner requires all six phases in this exact order. It never invokes a shell, never logs command arguments or output, enforces per-step and overall timeouts (overall maximum 300 seconds), stops on the first failure, and writes an atomic JSON evidence record. If any phase fails, it deliberately does not unfreeze writes.

## Configuration

Create a reviewed JSON plan on the deployment host. Commands must be absolute executable paths and are supplied as argv arrays; secrets should be read by those commands from the host secret manager or protected files, never embedded in this plan.

```json
{
  "version": 1,
  "max_duration_seconds": 270,
  "steps": [
    {"name": "freeze_writes", "argv": ["/opt/platform/bin/freeze-writes"], "timeout_seconds": 20},
    {"name": "reverse_sync", "argv": ["/opt/platform/bin/postgres-to-legacy-sqlite", "--snapshot", "/srv/rollback/frozen.dump"], "timeout_seconds": 150},
    {"name": "verify_parity", "argv": ["/opt/platform/bin/verify-rollback-parity", "--snapshot", "/srv/rollback/frozen.dump"], "timeout_seconds": 35},
    {"name": "switch_gateway_to_legacy", "argv": ["/opt/platform/bin/switch-gateway", "--target", "legacy"], "timeout_seconds": 20},
    {"name": "verify_legacy_health", "argv": ["/opt/platform/bin/check-legacy-health"], "timeout_seconds": 25},
    {"name": "unfreeze_legacy_writes", "argv": ["/opt/platform/bin/unfreeze-legacy-writes"], "timeout_seconds": 10}
  ]
}
```

The executable paths above are examples, not binaries supplied by this repository. The PostgreSQL→SQLite adapter, gateway control-plane integration, write-freeze implementation and health probes must be supplied and tested by the production deployment owner. The runner intentionally refuses to fabricate these capabilities.

## Operation

Plan only (default; does not execute commands):

```sh
python scripts/rollback_cutover.py --plan /etc/agent-platform/rollback-plan.json
```

Execute only after operator approval:

```sh
python scripts/rollback_cutover.py --plan /etc/agent-platform/rollback-plan.json --execute --evidence /var/lib/agent-platform/rollback/rollback-2026-11-18.json
```

Exit codes: 0 = all six steps succeeded; 1 = step failure/timeout/deadline exceeded (inspect evidence; writes remain frozen); 2 = invalid plan or invocation. Evidence records step names, timestamps, durations and exit codes, but no stdout/stderr or command arguments.

## Verification boundary

Unit tests use temporary executable Python commands to verify ordering, fail-fast behavior, timeout handling and evidence generation. They do not execute a production PostgreSQL restore, real gateway control plane or real legacy service. The five-minute objective remains a production drill requirement: measure the full six-step run against a real frozen snapshot and attach its evidence before marking S-33-08/A-666-08 accepted.
