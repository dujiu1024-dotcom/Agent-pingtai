# Issue #21 / L4-T06 business, security and recovery drill

## Automated scope

The Platform Release Gate runs the existing Issue #21 business journey, security-negative and fault-injection suites together with the real ephemeral-service recovery exercises. This change wires the missing backup/restore drills into the existing integration job; it does not create a second CI stack or reimplement existing domain behavior.

| Acceptance area | Executed suite(s) | What the suite establishes |
|---|---|---|
| Core business journey | `tests.test_core_business_journey_e2e`, `tests.test_multi_expert_workflow_integration`, `tests.test_platform_e2e_release_gate` | Login/authentication, knowledge write and retrieval, tenant separation, multi-expert execution, approval pause/resume/rejection, artifact/blackboard lineage |
| Security negative | `tests.test_issue_21_c_21_02_security_negative`, `tests.test_tool_gateway`, `tests.test_sandbox_oci` | Cross-tenant/resource denial, spoofing, prompt/tool argument injection, role/approval bypass, audit/trace minimization, OCI launch restrictions |
| Fault injection | `tests.test_issue_21_c_21_03_fault_injection`, `tests.test_postgres_redis_worker_recovery_integration`, `tests.test_event_consumer` | PostgreSQL receipt outage, Redis claim/ack outage, worker restart/retry, expired lease recovery, duplicate delivery/idempotency, concurrent receipt claim |
| PostgreSQL backup/restore | `tests.test_postgres_backup_restore_integration`, `tests.test_issue_18_a_18_02_backup_restore_drill` | Encrypted backup through repository scripts, checksum/manifest, restore to a distinct ephemeral database, marker verification, measured CI RPO/RTO |
| Object/config backup contracts | `tests.test_object_storage_backup`, `tests.test_object_storage_restore`, `tests.test_backup_integrity_verification`, `tests.test_backup_recovery_scripts`, `tests.test_backup_disaster_recovery`, `tests.test_platform_backup_policy`, `tests.test_platform_backup_runner` | Encryption, checksums/manifests, archive path safety, explicit destructive restore confirmation, retention/scheduling and component policy contracts |

## CI execution and artifacts

The existing `e2e-security-integration` job provisions temporary GitHub Actions PostgreSQL and Redis services. It installs `postgresql-client` and `age`, runs the suite above, and sets:

```text
DRILL_EVIDENCE_DIR=issue-21-drill-evidence
```

When the isolated PostgreSQL drill succeeds, the job uploads artifact `issue-21-l4-t06-recovery-drill-evidence` (30-day retention), containing the JSON exercise record, encrypted backup manifest and checksum sidecar. The test uses unique temporary source and destination databases and drops both after the run. The artifact records measured values from the ephemeral CI service; it is not production RPO/RTO evidence.

The artifact upload is configured with `if: always()` so a failed test run still preserves any partial evidence generated before failure. Missing artifact is not interpreted as a passed drill.

## PR #439 historical gate failure

The historical PR #439 Platform Release Gate failed while importing `tests.test_platform_e2e_release_gate`: the then-current `agent_platform.memory` package exports and `memory.retrieval` symbols were inconsistent (`MemoryRecallHit` import failure). The integration job was cancelled by the workflow concurrency policy after the focused job failed. The current mainline package export surface has since been consolidated; this PR re-runs the affected business journey through the current branch's Release Gate rather than treating the historical cancelled run as evidence.

## Explicitly out of scope / not claimed

- No production server, production credentials, customer data, external model provider, live object-storage endpoint, or physical device was accessed or provisioned.
- Business journey and security tests use isolated test fixtures; they are not a live customer-system E2E or external red-team exercise.
- PostgreSQL/Redis are ephemeral CI services. The drill does not prove multi-region failover, production restore throughput, Redis persistence recovery, MinIO data-plane restoration, cross-component point-in-time consistency, or production SLA/RPO/RTO.
- Object storage backup/restore tests currently validate scripts/contracts and safety guards; they do not perform a live MinIO backup/restore.
- Production migration, rollback, deployment smoke, independent release sign-off and full platform disaster recovery remain governed by `docs/release/PRODUCTION_ACCEPTANCE_REGISTER.md` and the schema-v2 release evidence gate. No production GO is inferred from this CI job.
