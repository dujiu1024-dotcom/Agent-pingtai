# Migration dual-run and cutover policy

This policy covers only Issue #19 / C-19-03. It is a control-plane decision
module; callers remain responsible for durable evidence storage, adapter
invocation, metrics collection, and operational approval.

## Phases and write authority

| Phase | Read behavior | Write behavior |
|---|---|---|
| `legacy_primary` | Legacy only | Legacy only |
| `dual_write` | Legacy primary; target shadow-read | Legacy authoritative; write legacy and target |
| `canary` | Stable tenant/request hash chooses legacy or target; other side is shadow-read | Target authoritative; write target and legacy |
| `target_primary` | Target primary; legacy shadow-read | Target authoritative; write target and legacy during rollback window |
| `rolled_back` | Legacy only | Legacy only |
| `retired` | Target only | Target only |

Canary assignment is deterministic for a tenant/request key, so retries do
not randomly move a request between systems. The caller must provide an
explicit canary percentage; the policy does not infer or silently ramp it.

## Consistency and rollback

Every decision evaluates evidence freshness, sampled-row count, row mismatch ratio,
source/target checksums, replication lag, and write-error rate. Defaults require at least
100 sampled rows, exact checksums, zero row mismatches, at most 5 seconds of
replication lag, and at most 0.1% write errors. Missing/insufficient evidence
fails closed. Evidence older than 60 seconds is stale by default and cannot
advance a phase; stale evidence in an active dual-write/canary/target-primary
phase produces a rollback decision. Operators can tune the freshness bound
with `max_evidence_age_seconds` for their measurement pipeline.

A consistency failure while dual-writing, canarying, or serving target-primary
traffic returns a rollback decision. The caller must execute the rollback
through its deployment/router adapter and record the decision and evidence.
The module does not directly mutate traffic or databases.

## Advancement and retirement gates

- Legacy-primary may advance only to dual-write.
- Dual-write may advance to canary only after consistency checks pass and the
  configured observation window has elapsed.
- Canary may advance to target-primary only after the same gates pass.
- Target-primary remains dual-writing through the rollback window.
- Legacy retirement is rejected until the rollback window has elapsed, no
  legacy writes are observed, and consistency evidence remains clean.
- Retired is terminal; no automatic transition back is allowed. Recovery after
  retirement requires a separately approved operational procedure.

The defaults are conservative starting values, not universal SLOs. Operators
must set thresholds and observation/rollback windows for the data class and
business criticality before enabling a production cutover.

## Verification

`tests/test_migration_cutover.py` covers deterministic canary assignment,
dual-write authority, checksum/lag/error drift, stale-evidence rejection and rollback, observation
window enforcement, invalid phase transitions, and legacy retirement gates.
Production traffic switching and real-system rollback are intentionally not
simulated or claimed by these unit tests.
