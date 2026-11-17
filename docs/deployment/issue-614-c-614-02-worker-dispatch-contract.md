# Issue #614 — C-614-02 Run worker dispatch contract

This change adds a focused regression contract around the production Run worker's
claim-to-dispatch boundary.

## Covered race

A worker may successfully claim a durable task row, then fail to retrieve the
corresponding task record (for example, because the record was concurrently
removed or became unavailable between the claim and read). The worker must:

- count the claimed item as skipped, not succeeded or failed;
- release the lease using the exact tenant, Run ID and claiming worker ID;
- avoid resolving or constructing an executor for a missing task record;
- preserve the configured claim batch size and lease duration.

The regression is in `tests/test_issue_614_runtime_composition.py` and is run
by the Platform Release Gate focused-unit job. The test uses a deterministic
store double for this narrow race; it does not replace PostgreSQL concurrency
or multi-worker integration testing.

## Verification boundary

Python syntax compilation of the new test block passed during authoring.
The connector environment does not provide a checked-out repository or project
dependency environment, so the full unittest module and Ruff were not executed
locally. GitHub Actions on the PR head are the authoritative executable gate.

This test does not establish a live PostgreSQL lease race, production executor
construction, Vault/Provider connectivity, or a complete Agent-to-Run execution
journey. Those remain deployment and integration acceptance requirements.
