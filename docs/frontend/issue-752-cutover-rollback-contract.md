# Issue #752 / L2-T18 — Frontend cutover and rollback contract

This supplements the existing parallel-build and auth E2E implementation merged in PR #933. It does not duplicate Vite build targets, identity pages, or route smoke tests.

## Safe rollout record

Before a deployment operator changes the reverse-proxy entry, capture a JSON record with:
- stage: development, internal, acceptance, or default;
- immutable legacy and candidate artifact SHAs;
- active entry and exact legacy rollback target SHA;
- reverse-proxy/deployment configuration checksum;
- canary percentage and sticky-routing confirmation;
- health gates for bootstrap, core routes, API errors, frontend exceptions, SSE reconnect, and file operations;
- P0/P1 defect state;
- explicit confirmation that in-flight Task/Run work is preserved and business data is not rolled back.

The offline validator is `scripts/frontend_cutover_policy.py`. Example:

```sh
python scripts/frontend_cutover_policy.py --validate-record /path/to/cutover-record.json
python -m unittest tests.test_frontend_cutover_policy -v
```

The validator is deliberately read-only. It emits `switch_frontend_entry_only` when a candidate entry has a failed health gate or an open P0/P1 defect. It rejects a rollback target other than the recorded legacy artifact, non-sticky routing, unsafe stage percentages, loss of in-flight work, and any business-data rollback.

## Rollout sequence

1. Development: build candidate with `pnpm --dir frontend exec vite build --mode parallel`; confirm it writes `agent_platform/web/static-parallel` and leaves `agent_platform/web/static` intact.
2. Internal: route 1–10% using a stable cookie/test header at the reverse proxy; retain the legacy entry.
3. Acceptance: increase to at most 50% only after health evidence and no P0/P1 defects.
4. Default: 100% candidate only after release sign-off; retain the previous artifact for at least one rollback window.
5. Do not hot-switch `auth.enabled`; mode changes require deployment configuration change and process restart.

## Rollback contract

- Freeze rollout and preserve the evidence record, build SHAs, config checksum, timestamp, and operator identity.
- Switch only the frontend entry/proxy target to the exact recorded legacy artifact.
- Preserve in-flight Tasks/Runs and all business data. Do not reverse Task, Run, Approval, Session, File, or audit records.
- Verify legacy entry, bootstrap, core routes, API origin, SSE and file paths before closing the incident.
- Re-enter rollout only after fixing the defect and repeating CI plus the deployment rehearsal.

## Automated evidence and limits

The unittest suite validates the decision contract and failure paths without contacting a proxy, server, database, or external resource. It does not perform a real traffic shift or prove a five-minute operational recovery. Real proxy stickiness, observed canary metrics, actual artifact availability, and timed rollback remain deployment-environment acceptance items. No real device or server is requested by this task.
