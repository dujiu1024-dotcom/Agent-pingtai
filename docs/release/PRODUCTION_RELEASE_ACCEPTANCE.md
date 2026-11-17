# Production release acceptance and evidence runbook

This runbook defines the release decision contract. The validator is deliberately
offline: it validates a signed-off evidence document and does not deploy, execute
migrations, change traffic, or contact production systems.

## Required accountable roles

- **Release owner:** coordinates the release, collects the evidence archive, and
  confirms that every check is complete.
- **Independent release approver:** makes the GO/NO-GO decision. This must be a
  different identity from the release owner.
- **Check owner:** named accountable person for each individual check. The owner
  records the UTC verification time and durable evidence reference.

Use named people or stable team identities, not role placeholders. The evidence
archive reference must identify an immutable, access-controlled archive location.
Archive the exact release candidate SHA, CI run URLs, test reports, migration
preflight output, rollback drill report, smoke report, approvals, and final
decision record. Do not put credentials, tokens, or customer secrets in evidence.

## Mandatory acceptance checklist

| Check | Acceptance evidence |
|---|---|
| E2E | Required critical user journeys and integration tests pass |
| Fault injection | Recovery and failure-mode tests pass; unresolved data-loss risks are blockers |
| Performance | Agreed latency, throughput, concurrency and capacity thresholds pass |
| Security | Security regression and authorization/tenant-isolation checks pass |
| Backup/restore | Backup integrity and restore verification pass |
| Production smoke | Authorized smoke checks pass in the target production environment |
| Migration | Preflight passes; schema/app compatibility window is confirmed; strategy is expand_contract or approved forward_fix |
| Rollback | A rollback/restore drill passes; strategy is compensating_migration, backup_restore, or forward_fix |

Each check requires status passed, a named owner, a fresh UTC timestamp, and a
durable evidence reference. A planned check, CI workflow definition, or unexecuted
test is not evidence that the check passed.

## Blocking conditions

Release is NO-GO if any of the following applies:

- A required check is missing, pending, failed, stale, or has placeholder ownership or evidence.
- The evidence commit SHA differs from the exact release candidate.
- Migration preflight or backward/forward compatibility confirmation is absent.
- Rollback/restore drill is absent or failed.
- Any unresolved blocker is listed.
- The evidence archive reference, release owner, or independent approver is absent.
- The release decision is anything other than go.

The validator also rejects timestamps that are not explicit UTC or are more than
five minutes in the future. Evidence older than the configured freshness window
(default seven days) is stale.

## Decision procedure

1. Freeze the release candidate and record its full 40-character commit SHA.
2. Assign the release owner, independent approver, and each check owner.
3. Run non-production tests, migration preflight, compatibility, backup/restore,
   and rollback drills. Capture immutable outputs.
4. Obtain production smoke evidence only through the separately authorized
   production change process. This repository workflow does not perform it.
5. Archive the evidence bundle immutably and fill
   RELEASE_EVIDENCE_TEMPLATE.json (schema version 2).
6. Set blockers to an empty array only after all blockers are resolved. Set
   release_decision to go only after independent approval.
7. Validate with:

   ```bash
   python scripts/release_gate.py --evidence release-evidence.json --commit-sha CANDIDATE_SHA
   ```

8. Preserve the validator output and approval record alongside the archive.
   A failed validator result is a release block, not a warning.

## Rollback and post-release

Do not assume a destructive down-migration is safe. Prefer expand/contract
compatibility, compensating migrations, verified backup/restore, or a forward-fix
when data transformations cannot be reversed. The chosen strategy and drill result
must be explicit in the evidence document. If a post-release smoke check or
monitoring threshold fails, stop rollout/traffic expansion under the approved
incident procedure, execute the documented rollback strategy, preserve logs and
database recovery evidence, and issue a new release decision before retrying.

## Scope and safety

CI validates the evidence schema with synthetic fixtures only. This PR does not
claim a production migration, restore, smoke test, or release approval occurred.
No production server, real customer data, or external service is contacted.
