# A-665-05 Core journey and abnormal-log acceptance

## Automated acceptance

Run after installing the platform test dependencies:

```bash
python scripts/switch_core_acceptance.py
```

The runner executes six existing end-to-end / integration suites:

- Agent definition, knowledge/RAG, multi-expert workflow, approval, Blackboard and artifact delivery
- Authenticated platform journey, tenant isolation, security and release-gate contracts
- Run lifecycle transitions and cancellation
- Event consumer delivery/receipt behavior
- Outbox relay worker lifecycle and recovery
- Alert/notification delivery behavior

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
claimed by A-665-05. No production server, credentials, or external resources
were requested or used.
