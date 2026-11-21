# Main Implementation Cleanup Audit — 2026-11-22

## 1. Purpose and audit boundary

This report records the second-round implementation cleanup for repository `dujiu1024-dotcom/Agent-pingtai`.

- Audited branch: `main`
- Main HEAD observed: `edcc08e2d74259c7f5ea8ce7f1acda2f94a260b4`
- Atomic source of truth: [Issue #267](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/267)
- Strategic scope: [Issue #848](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/848)
- Requirements coverage governance: [Issue #884](https://github.com/dujiu1024-dotcom/Agent-pingtai/issues/884)

This was a repository tree, source-file, migration, test-file, PR merge-state and PR-head workflow audit. No local pytest/Ruff run, live database execution, production deployment, external provider test, OCI host probe or field acceptance was performed in this round. Presence of a test file is not evidence that the test ran. A merged PR is not proof that its CI or production acceptance passed.

## 2. Decision vocabulary

| Status | Meaning |
|---|---|
| Implemented slice | Relevant code and test files are present on main; only the evidenced slice is removed from redevelopment. |
| Partially implemented | Some required code is present, but explicit functionality or integration remains. |
| Pending CI/Gate | PR is merged but one or more relevant workflows are failed/cancelled, or no current main status is available. |
| Pending field acceptance | Real provider, database, object storage, OCI host, external business system or production evidence is absent. |
| Not implemented as a domain outcome | Existing lower-level services may be reusable, but the issue's end-to-end business capability has not been evidenced. |

## 3. Domain cleanup: Issues #8–#21

| Issue | Main code evidence | Representative merged PR evidence | Current residual scope |
|---|---|---|---|
| #8 Sessions/assets/connectors | `control_plane/sessions_assets.py`, `api/sessions.py`, `runtime/session_persistence.py`, `storage/object_storage.py`, connector sync/cleanup workers; migrations 011/027/029/044 | #239/#327/#329/#333/#337; relevant PR-head CI/release/security passed | Real PostgreSQL connector worker/revocation cleanup; production object storage and malware scanner; production composition authorization and field evidence |
| #9 Tasks/Run lifecycle | `runtime/lifecycle.py`, `reliability.py`, `run_context.py`, `api/runs.py`, PostgreSQL lifecycle/checkpoint code | #324/#328/#330/#331 passed; #530 merged but ci/release/harness/quality failed | Fix #530 gates; real PostgreSQL crash/lease/checkpoint recovery and external-effect idempotency |
| #10 Knowledge/RAG | `knowledge/ingestion.py`, `chunking.py`, `retrieval.py`, `rag.py`, `postgres_repository.py`, ACL/citation modules; migrations 032/067/068/069 | #610/#613/#616/#628 merged; ci/release/harness/quality failed or cancelled | Incremental update/deletion; ACL propagation to index/cache under concurrency; gate repair; real pgvector/provider/corpus validation |
| #11 Memory | extraction/review/conflict/retrieval/deletion/maintenance/PostgreSQL modules; migrations 009/081 | #630/#633 merged; ci/release failed | Gate repair; PostgreSQL deletion jobs, async index/cache propagation and race acceptance |
| #12 Runtime/Harness | adapter contract, harness benchmark/factory, Tool Gateway, delegation, structured output, OCI sandbox, production composition | #263/#311/#313/#349/#360/#363/#368/#389 passed; #519/#591/#619 have failed/cancelled gates | Gate repair; real OCI file/network/process/credential isolation; production provider/multi-instance/cancel/recovery acceptance |
| #13 Blackboard | repository/API/event delivery/replay/SSE/multi-expert services | #371 passed; #612 merged but ci cancelled and release/harness/quality failed | Gate repair; real parallel expert conflict/decision/approval/replay evidence |
| #14 Workbench | Workbench API, task/approval/artifact UI and end-to-end test files | #405 passed; #615 merged but ci/release/harness/quality failed | Gate repair; real model/Gateway/object storage/browser/device acceptance; individual page ownership remains #742–#750/#814–#818 |
| #15 Identity/security | identity/resource scope, approval, append-only audit, permission runtime and sandbox | #297/#300/#311/#313/#316 passed | Four explicit remaining checks: live reauthorization before replay/worker execution; real authorized Tool Effect Reconciliation with fail-closed audit; PostgreSQL/MySQL connector read-only enforcement; production no-auth/CSRF bypass startup rejection |
| #16 Observability/evaluation | tracing, metrics, alerts/outbox/worker, evaluation datasets/runner/store/governance, prompt rollout | #391 passed; #661 merged with ci/release/harness/quality failed | Gate repair; real PostgreSQL/Redis alert delivery and evaluation scheduling; human scoring and release gate evidence |
| #17 Quota/cost | admission/concurrency/PostgreSQL/pricing/production worker/storage meter/usage reporting | #321/#423 passed; #482 cancelled; #673/#675 failed | Gate repair; production multi-worker quota enforcement, real tool/storage metering and billing reconciliation |
| #18 Deployment/recovery | backups/migrations/versioned deployment, Outbox/Redis replay, object storage backup/restore | #511 cancelled; #721 ci cancelled and release/harness/quality failed | Four explicit remaining checks: monotonic migration IDs/checksum compatibility; Outbox/Redis Stream recovery; cross-component consistent backup and measured RPO/RTO; least-privilege object-store identities/readiness |
| #19 Migration/data governance | legacy inventory, migration batches/extract/diff/runner/cutover/lifecycle/audit | #340/#343 passed | Real source inventory, full parity reconciliation, rollback/no-dual-write proof, cascade deletion/export and production cutover |
| #20 Public API/webhook/SDK/extensions | public contract, webhook worker/replay/DLQ, SDK, extension lifecycle/publisher trust, localization | #427 failed/cancelled; #436 no workflow records; #450 cancelled | Gate re-run; real Redis/PostgreSQL/Secret Manager; webhook retry/dead-letter/replay and third-party SDK interoperability |
| #21 E2E/release gate | core business journey E2E, release gate/evidence/report, backup/fault recovery tests | #439 merged but release gate failed and ci cancelled | Gate repair; real business E2E, security negatives, fault injection, backup/restore and release sign-off |

## 4. Extension work packages: Issues #22–#31

These Issues remain closed as historical planning entry points, not as proof that every requirement is accepted. Their code/PR state supersedes older “not dispatched/no PR” notes in the atomic ledger.

| Issue | Main code/test evidence | Representative merged PRs | Current residual scope |
|---|---|---|---|
| #22 Graph | graph schema/storage/extraction/query/governance, graph UI, migrations 082/090/091/092/095, graph tests | #536/#537/#662/#668/#669 | ci/release/harness/quality failures/cancellations; real corpus provenance, quality, latency and deletion propagation |
| #23 Prompt Lab | prompt versions/variants/store/experiments/rollouts/API/UI, migrations 017/079/083/084/096 | #538/#571/#573/#577 | ci/release failures/cancellations; A/B stability, tenant/label traffic split and real rollout acceptance |
| #24 RAG Playground | upload/chunk/query playground/metrics/API/UI and tests | #590/#620/#623/#563 | ci/release/harness/quality failures/cancellations; real provider/corpus and reproducibility |
| #25 Session persistence | Session model/API/Run checkpoint/history/permissions/deletion; reuses #8 | #627/#634 | ci/release/harness/quality failures; PostgreSQL restart and deletion propagation acceptance |
| #26 Model Eval | dataset/runner/variant comparison/report export/rollout bridge/API/UI | #585/#586/#595/#598 | ci/release/harness/quality failures; real model and metric reproducibility |
| #27 Connector Catalog | credential store/catalog/BaseConnector/shared auth/revocation | #579/#583/#588/#606 | ci/release/harness/quality failures; real Vault/KMS/OAuth and provider interoperability |
| #28 Skill Registry | manifest/registry/dependency review/OCI sandbox/API/tests | #546/#549/#552/#555/#607/#651/#654 | ci/release/harness/quality failures/cancellations; real OCI/ClamAV and lifecycle cleanup |
| #29 Replay | replay API/runtime/workbench UI/tests | #574/#593/#580/#597 | ci/release/harness/quality failures; real Run replay consistency and browser acceptance |
| #30 Streaming | SSE protocol/client/Agent streaming/Blackboard SSE/Workbench/tests | #576 | ci/release/harness/quality failures; latency, reconnect and stream parity under real load |
| #31 Batch | executor/report/API/UI/PostgreSQL jobs/tests | #545/#551/#554/#685/#687/#689/#702 | ci/release/harness/quality failures/cancellations; throughput, failure isolation and load acceptance |

## 5. Token/context budget: Issue #72

Issue #72 was reopened because the live Issue state was closed despite its historical comments explicitly saying it should remain open and PR #736 still had failed gates.

Already merged and not to be reimplemented: Token Estimator/Model Router preflight, production budget wiring, root Run attribution, atomic multi-scope reservation groups, context overflow strategies and usage visibility.

Merged PRs #255/#265/#271/#276/#282/#299/#309/#339/#346 had ci/release/security success. PRs #660/#664/#672/#736 are merged but have ci/release/harness/quality failures.

Residual scope: repair those gates; verify all production Harness/Provider/delegation paths enforce budget preflight and root Run reservations; complete provider-dispatch crash reconciliation, retry/recovery idempotency and multi-worker PostgreSQL acceptance; verify Run detail/audit budget visibility in a real authorized environment.

## 6. Strategic gap Issues #885–#889

No dedicated end-to-end delivery PR was identified for these five domain issues during this round. Existing platform components are dependencies to reuse, not proof that the new domain outcomes are complete.

| Issue | Reuse boundary | Missing domain outcome |
|---|---|---|
| #885 Cross-system business automation | Reuse #8 connectors, #9 Task/Run, #13 Blackboard, #15 approval/security, #20 API/Webhook | Cross-two-real-systems workflow, Saga compensation, business receipt/reconciliation, exception queue and field integration |
| #886 Enterprise AI ROI | Reuse #16 observability/evaluation, #17 cost/pricing, #9 Run and #870 digital employee identity | Human baseline, AI contribution attribution, handoff/rework deductions, finance-confirmed value, department/job ROI and business pilot |
| #887 Knowledge asset governance | Reuse #10 Knowledge/RAG/ACL, #19 lifecycle, #22 graph and #15 authorization | Ownership, quality/freshness policy, lineage/impact analysis, downstream asset impact and real governance pilot |
| #888 AI supply-chain governance | Reuse #15 security, #20 publisher trust, #27 connector catalog and #28 skill registry/sandbox | Cross-asset SBOM/provenance/signature/vulnerability/license gates, version impact and runtime revocation response |
| #889 Hybrid deployment continuity | Reuse #18 deployment/backup/recovery, #15 identity/policy/audit and #9 Run recovery | Offline autonomy, control-plane degradation, authorization cache/revocation semantics, isolated-network operation and measured HA/DR |

## 7. Atomic ledger and current repository status

- #267 currently contains **186 unique atomic IDs**; duplicate supplemental evidence rows for C-29-02/C-29-03 were relabeled so they no longer count as duplicate tasks.
- 185 atomic checkboxes are checked and one remains unchecked: A-12-02 real production OCI file/network/process/credential isolation probe. Historical checkboxes are not current CI or field acceptance evidence.
- Current Issue inventory: 63 open / 36 closed / 99 total. #72 is open again.
- Main HEAD combined status returned no status records in this audit. PR-head workflows are recorded per batch; failed/cancelled runs remain acceptance debt.
- PR #890 remains open and unmerged. No PR was merged and no Issue was deleted during this cleanup round.

## 8. Next execution gates

1. Repair the failed/cancelled CI/release/harness/quality workflows for merged PRs; prioritize P0/P1 domain gates.
2. Execute opt-in integration tests against real PostgreSQL/Redis/object storage/Secret Manager/OCI hosts.
3. Run production/field acceptance for #15/#18 and the strategic gaps #885–#889.
4. Complete the full Requirement ID → unique Issue → main code path → test → PR/commit → current CI → field evidence matrix before treating the atomic ledger as fully audited.
