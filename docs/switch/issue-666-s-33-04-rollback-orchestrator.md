# S-33-04 — legacy rollback orchestration

This module provides the ordered, fail-closed orchestration contract for the
five-minute legacy rollback procedure. It does not implement a database driver
or assume that a production backfill has happened.

## Required host operations

The production composition root must inject five operations:

1. `quiesce_platform_writes(rollback_id)`: fence new platform writes and drain
   in-flight writes.
2. `backfill_postgres_to_sqlite(rollback_id)`: perform a complete reverse
   backfill and return `RollbackDataReport`.
3. `verify_legacy_parity(rollback_id, report)`: perform host/database-specific
   integrity checks and explicitly return `True`.
4. `switch_gateway_to_legacy(rollback_id)`: atomically route API and Workflow
   traffic to the legacy application.
5. `resume_legacy_writes(rollback_id)`: resume writes only after the gateway
   has switched.

The coordinator orders these steps and applies one shared deadline (maximum
300 seconds), including time waiting for another rollback on the same
coordinator instance. It serializes concurrent calls within that process.

## Fail-closed rules

- Gateway switching is forbidden until the source and target table inventories,
  total row counts and full-export SHA-256 checksums match.
- Any excluded or unmapped table makes the report ineligible for parity.
- The host verifier must independently accept the backfill before cutover.
- Failure before gateway switching leaves the platform write fence in place;
  this avoids allowing two writable systems after an incomplete backfill.
- A failure after gateway switching is reported with
  `gateway_switched=True`, so operators can distinguish a traffic cutover from
  a failure to resume legacy writes.
- Callback exceptions are reduced to exception type names; the coordinator
  does not serialize database rows, credentials or exception messages.

## Production integration requirements

The coordinator lock is process-local. Production must additionally provide a
durable, cross-worker rollback lease/state record and make each callback
idempotent by `rollback_id`. The gateway callback must atomically publish the
legacy routing policy to every worker. The backfill callback must use a
consistent PostgreSQL snapshot, preserve keys and required sequence state, and
produce canonical full-export checksums for both databases.

The automated tests use deterministic callbacks and prove orchestration order,
parity gates and timeout behavior only. They do not prove that a real PostgreSQL
database can be restored into SQLite, that production data is identical, or
that all workers switch within five minutes. Those remain deployment evidence
for the actual rollback drill.
