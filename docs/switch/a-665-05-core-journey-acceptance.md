# A-665-05 Core journey and abnormal-log acceptance

## Automated acceptance

Run after installing the platform test dependencies:

```bash
python scripts/switch_core_acceptance.py
```

The runner executes the six existing end-to-end / integration suites and the Issue #665 Agent→Workflow→Run→Blackboard→notification journey:

- Agent definition, knowledge/RAG, multi-expert workflow, approval, Blackboard and artifact delivery
- Authenticated platform journey, tenant isolation, security and release-gate contracts
- Run lifecycle transitions and cancellation
- Event consumer delivery/receipt behavior
- Outbox relay worker lifecycle and recovery
- Alert/notification delivery behavior
- Tenant-scoped Agent publication/resolution, multi-expert workflow execution, Run terminal state, Blackboard claims/artifact lineage, Outbox relay and notification payload linkage (S-665-01)

The runner returns non-zero on any unittest failure/error or any Python
`ERROR` / `CRITICAL` log record emitted during the run. It reports logger,
source file and line without printing log message contents, avoiding accidental
secret disclosure. Warnings are not treated as abnormal by this gate because
some recovery tests intentionally exercise transient conditions; they remain
visible in the normal test output if a test emits them.

## Evidence boundary and known gaps

This is deterministic CI acceptance using repository test doubles and the
services provisioned by the existing platform-release-gate workflow. It proves
only the exercised automated paths. It does not prove production traffic,
external notification-provider delivery, real Redis/PostgreSQL failover,
performance parity with the legacy SQLite implementation, or migration
consistency. Those belong to the other S-32/A-665 acceptance items and are not
claimed by A-665-05. The Platform Release Gate separately executes the S-665-02/03/04 and A-665-06/07 suites, plus an isolated CI PostgreSQL/Redis probe and uploads its JSON evidence artifact. Its SQLite fixture and CI service measurements are not a matched production legacy-vs-platform benchmark; the parity evaluator remains incomplete until comparable workload/environment fingerprints and actual reports are supplied. No production server, credentials, or external resources
were requested or used.
