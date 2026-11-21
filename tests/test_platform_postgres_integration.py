"""Real PostgreSQL integration tests; enabled only with AGENT_PLATFORM_TEST_DSN."""

import asyncio
import os
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from agent_platform.approval.postgres import (
        PostgresApprovalVerifier,
        PostgresAuditReader,
        PostgresAuditSink,
    )
    from agent_platform.blackboard.postgres_repository import PostgresBlackboardRepository
    from agent_platform.blackboard.protocol import VersionConflict
    from agent_platform.contracts import (
        BlackboardEntry,
        BlackboardEvent,
        EntryKind,
        EntryStatus,
        Scope,
    )
    from agent_platform.control_plane.data_connectors import DataConnection
    from agent_platform.control_plane.postgres import (
        PostgresRegistryRepository,
        PostgresSessionRepository,
    )
    from agent_platform.control_plane.postgres_connectors import (
        PostgresConnectorRepository,
    )
    from agent_platform.control_plane.registry import RegistryService
    from agent_platform.control_plane.sessions_assets import (
        InMemoryObjectStorage,
        MessageRole,
        SessionAssetService,
    )
    from agent_platform.events.outbox import PostgresOutboxStore
    from agent_platform.events.receipts import PostgresEventReceiptStore
    from agent_platform.identity.bootstrap import BootstrapError, bootstrap_tenant_owner
    from agent_platform.identity.models import ResourceRef
    from agent_platform.identity.postgres import PostgresIdentityRepository
    from agent_platform.identity.security import PasswordHasher
    from agent_platform.identity.service import (
        AuthenticationError,
        AuthorizationDenied,
        IdentityService,
    )
    from agent_platform.identity.service_accounts import (
        PostgresServiceIdentityRepository,
        ServiceIdentityError,
        ServiceIdentityService,
    )
    from agent_platform.identity.tenants import create_tenant_with_owner
    from agent_platform.runtime.lifecycle import RunLifecycleManager, RunStatus
    from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore
    from agent_platform.runtime.postgres_registry_snapshot import PostgresRunSnapshotStore
    from agent_platform.runtime.registry_snapshot import RunRegistrySnapshotService
    from agent_platform.tools.idempotency import PostgresToolEffectStore
    from agent_platform.workflows.blackboard_services import BlackboardMultiExpertServices
    from agent_platform.workflows.multi_expert import build_multi_expert_workflow


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class PostgresPlatformIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"it-{uuid4().hex}"
        migration_dir = Path("agent_platform/storage/postgres")
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (self.tenant_id, "Integration Test"),
            )
            await conn.commit()

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.connect = connect
        self.repo = PostgresBlackboardRepository(
            tenant_id=self.tenant_id, connection_factory=connect
        )

    async def asyncTearDown(self):
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_outbox
                   SET published_at=COALESCE(published_at, now()),
                       lease_token=NULL, lease_until=NULL
                   WHERE tenant_id=%s""",
                (self.tenant_id,),
            )
            await conn.commit()

    def make_entry(self, *, content=None, status=None, entry_id=None, correlation_id=None):
        return BlackboardEntry(
            scope=Scope.RUN,
            scope_id="run-integration",
            kind=EntryKind.FACT,
            content=content or {"fact": "original"},
            author_id="expert-a",
            status=status or EntryStatus.PROPOSED,
            entry_id=entry_id or str(uuid4()),
            correlation_id=correlation_id or str(uuid4()),
        )

    async def test_langgraph_multi_expert_workflow_persists_to_postgres_blackboard(self):
        try:
            from langgraph.checkpoint.memory import InMemorySaver
            from langgraph.types import Command
        except ImportError:
            self.skipTest("LangGraph is required for workflow/PostgreSQL integration")

        class Callbacks:
            async def plan_experts(self, *, objective, run_context):
                return [{"expert_id": "researcher"}, {"expert_id": "critic"}]

            async def run_expert(self, *, assignment, objective, run_context):
                return {
                    "claim": "verified claim from " + assignment["expert_id"],
                    "source_refs": ["source:" + assignment["expert_id"]],
                }

            async def synthesize(self, *, objective, contributions, run_context):
                return {"summary": "initial synthesis"}

            async def review(self, *, synthesis, contributions, run_context):
                return {
                    "conflicts": [{
                        "conflict_key": "claim-consistency",
                        "reason": "claims need reconciliation",
                        "evidence_refs": ["source:researcher", "source:critic"],
                    }],
                    "requires_approval": True,
                }

            async def resolve_conflicts(self, *, synthesis, conflicts, run_context):
                return {
                    "synthesis": {"summary": "reconciled synthesis"},
                    "decisions": [{
                        "conflict_key": "claim-consistency",
                        "outcome": "accept reconciled claim",
                        "rationale": "evidence reviewed",
                        "evidence_refs": ["source:researcher"],
                    }],
                    "requires_approval": True,
                }

            async def prepare_approval(self, *, state):
                return {"action": "publish", "summary": state["synthesis"]["summary"]}

            async def apply_approval(self, *, state, decision):
                return {"approved": decision.get("approved") is True, "approver_id": "integration-reviewer"}

            async def archive(self, *, state):
                return {"summary": state["synthesis"]["summary"]}

            async def reject(self, *, state):
                return {"reason": "rejected"}

        services = BlackboardMultiExpertServices(
            tenant_id=self.tenant_id, repository=self.repo, callbacks=Callbacks()
        )
        graph = build_multi_expert_workflow(
            services=services, checkpointer=InMemorySaver()
        )
        initial_state = {
            "tenant_id": self.tenant_id,
            "task_id": "task-postgres-blackboard",
            "run_id": "run-postgres-blackboard",
            "objective": "verify durable multi-expert lineage",
            "run_context": {"model_template": "general"},
        }
        config = {"configurable": {"thread_id": self.tenant_id + "-blackboard-run"}}
        paused = await graph.ainvoke(initial_state, config=config)
        self.assertIn("__interrupt__", paused)
        completed = await graph.ainvoke(
            Command(resume={"approved": True}), config=config
        )
        self.assertEqual(completed["status"], "completed")
        entries = await self.repo.list_entries(
            scope=Scope.TASK, scope_id="task-postgres-blackboard", limit=100
        )
        kinds = {entry.kind for entry in entries}
        required_kinds = {
            EntryKind.CLAIM, EntryKind.ARTIFACT, EntryKind.CONFLICT,
            EntryKind.CHALLENGE, EntryKind.DECISION, EntryKind.NOTE,
        }
        self.assertTrue(
            required_kinds.issubset(kinds),
            f"missing Blackboard kinds: {required_kinds - kinds}; got: {kinds}",
        )
        final_entry = await self.repo.get(
            completed["final_result"]["blackboard_entry_id"]
        )
        self.assertGreaterEqual(len(final_entry.source_refs), 6)
        async with await self.connect() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM platform_outbox WHERE tenant_id=%s",
                (self.tenant_id,),
            )
            self.assertGreaterEqual((await cur.fetchone())["n"], len(entries))

    async def test_migration_and_blackboard_write_are_atomic_with_outbox(self):
        entry = self.make_entry()
        await self.repo.propose(entry)
        latest = await self.repo.get(entry.entry_id)
        self.assertEqual(latest.content, {"fact": "original"})

        async with await self.connect() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM blackboard_events WHERE tenant_id=%s AND entry_id=%s",
                (self.tenant_id, entry.entry_id),
            )
            self.assertEqual((await cur.fetchone())["n"], 1)
            cur = await conn.execute(
                "SELECT count(*) AS n FROM platform_outbox WHERE tenant_id=%s",
                (self.tenant_id,),
            )
            self.assertEqual((await cur.fetchone())["n"], 1)


    async def test_replaying_same_blackboard_event_is_idempotent_and_payload_bound(self):
        entry = await self.repo.propose(self.make_entry())
        event = BlackboardEvent(
            event_type="blackboard.replay_probe",
            scope=entry.scope,
            scope_id=entry.scope_id,
            entry_id=entry.entry_id,
            entry_version=entry.version,
            actor_id="integration-test",
            payload={"attempt": 1},
            correlation_id=entry.correlation_id,
        )
        await asyncio.gather(
            self.repo.append_event(event),
            self.repo.append_event(event),
        )
        with self.assertRaisesRegex(ValueError, "reused with a different payload"):
            await self.repo.append_event(replace(event, payload={"attempt": 2}))

        async with await self.connect() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM blackboard_events WHERE tenant_id=%s AND event_id=%s",
                (self.tenant_id, event.event_id),
            )
            self.assertEqual((await cur.fetchone())["n"], 1)
            cur = await conn.execute(
                "SELECT count(*) AS n FROM platform_outbox WHERE tenant_id=%s AND event_id=%s",
                (self.tenant_id, event.event_id),
            )
            self.assertEqual((await cur.fetchone())["n"], 1)

    async def test_event_and_outbox_write_roll_back_as_one_transaction(self):
        entry = await self.repo.propose(self.make_entry())
        event = BlackboardEvent(
            event_type="blackboard.atomicity_probe",
            scope=entry.scope,
            scope_id=entry.scope_id,
            entry_id=entry.entry_id,
            entry_version=entry.version,
            actor_id="integration-test",
            correlation_id=entry.correlation_id,
        )
        async with await self.connect() as conn:
            await conn.execute(
                "INSERT INTO platform_outbox (tenant_id, event_id, topic, payload) VALUES (%s,%s,%s,'{}'::jsonb)",
                (self.tenant_id, event.event_id, "duplicate.event"),
            )
            await conn.commit()

        from psycopg.errors import UniqueViolation
        with self.assertRaises(UniqueViolation):
            await self.repo.append_event(event)

        async with await self.connect() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM blackboard_events WHERE tenant_id=%s AND event_id=%s",
                (self.tenant_id, event.event_id),
            )
            self.assertEqual((await cur.fetchone())["n"], 0)

    async def test_revision_is_append_only_and_rejects_stale_version(self):
        entry = await self.repo.propose(self.make_entry())
        revised = await self.repo.revise(
            entry.entry_id,
            expected_version=1,
            replacement=self.make_entry(
                content={"fact": "revised"}, status=EntryStatus.ACCEPTED,
                entry_id=entry.entry_id, correlation_id=entry.correlation_id,
            ),
        )
        self.assertEqual(revised.version, 2)
        self.assertEqual((await self.repo.get(entry.entry_id)).content, {"fact": "revised"})

        with self.assertRaises(VersionConflict):
            await self.repo.revise(
                entry.entry_id, expected_version=1,
                replacement=self.make_entry(
                    content={"fact": "stale"}, entry_id=entry.entry_id,
                    correlation_id=entry.correlation_id,
                ),
            )

        async with await self.connect() as conn:
            cur = await conn.execute(
                "SELECT version FROM blackboard_entries WHERE tenant_id=%s AND entry_id=%s ORDER BY version",
                (self.tenant_id, entry.entry_id),
            )
            self.assertEqual([row["version"] for row in await cur.fetchall()], [1, 2])

    async def test_concurrent_blackboard_revisions_allow_only_one_writer(self):
        entry = await self.repo.propose(self.make_entry())
        results = await asyncio.gather(
            self.repo.revise(
                entry.entry_id,
                expected_version=1,
                replacement=self.make_entry(
                    content={"fact": "writer-a"}, status=EntryStatus.ACCEPTED,
                    entry_id=entry.entry_id, correlation_id=entry.correlation_id,
                ),
            ),
            self.repo.revise(
                entry.entry_id,
                expected_version=1,
                replacement=self.make_entry(
                    content={"fact": "writer-b"}, status=EntryStatus.ACCEPTED,
                    entry_id=entry.entry_id, correlation_id=entry.correlation_id,
                ),
            ),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(item, VersionConflict) for item in results), 1)
        self.assertEqual(sum(isinstance(item, BlackboardEntry) for item in results), 1)
        versions = await self.repo.list_versions(entry.entry_id)
        self.assertEqual([item.version for item in versions], [1, 2])
        self.assertIn((await self.repo.get(entry.entry_id)).content["fact"], {"writer-a", "writer-b"})

    async def test_tenant_scoping_hides_other_tenant_entries(self):
        entry = await self.repo.propose(self.make_entry())
        other = PostgresBlackboardRepository(
            tenant_id=f"other-{uuid4().hex}", connection_factory=self.connect
        )
        self.assertIsNone(await other.get(entry.entry_id))
        self.assertEqual(await other.list_entries(scope=Scope.RUN, scope_id="run-integration"), [])

    async def test_concurrent_outbox_claims_do_not_claim_same_row(self):
        await self.repo.propose(self.make_entry())
        store_a = PostgresOutboxStore(connection_factory=self.connect)
        store_b = PostgresOutboxStore(connection_factory=self.connect)
        a, b = await asyncio.gather(
            store_a.claim_batch(limit=10), store_b.claim_batch(limit=10)
        )
        ids_a = {item.outbox_id for item in a if item.tenant_id == self.tenant_id}
        ids_b = {item.outbox_id for item in b if item.tenant_id == self.tenant_id}
        self.assertFalse(ids_a & ids_b)
        self.assertEqual(len(ids_a | ids_b), 1)

    async def test_expired_lease_can_be_reclaimed_and_old_token_cannot_ack(self):
        await self.repo.propose(self.make_entry())
        store = PostgresOutboxStore(connection_factory=self.connect, lease_seconds=5)
        first = (await store.claim_batch(limit=1))[0]
        async with await self.connect() as conn:
            await conn.execute(
                "UPDATE platform_outbox SET lease_until=now()-interval '1 second' WHERE tenant_id=%s AND outbox_id=%s",
                (self.tenant_id, first.outbox_id),
            )
            await conn.commit()
        second = (await store.claim_batch(limit=1))[0]
        self.assertNotEqual(first.lease_token, second.lease_token)
        self.assertFalse(await store.mark_published(first))
        self.assertTrue(await store.mark_published(second))


    async def test_outbox_failure_uses_persistent_retry_backoff(self):
        await self.repo.propose(self.make_entry())
        store = PostgresOutboxStore(connection_factory=self.connect)
        record = (await store.claim_batch(limit=1))[0]
        await store.mark_failed(record, "redis unavailable")
        self.assertEqual(await store.claim_batch(limit=1), [])
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_outbox SET next_attempt_at=now()
                   WHERE tenant_id=%s AND outbox_id=%s""",
                (self.tenant_id, record.outbox_id),
            )
            await conn.commit()
        retry = await store.claim_batch(limit=1)
        self.assertEqual(len(retry), 1)
        self.assertEqual(retry[0].event_id, record.event_id)

    async def test_approval_is_tenant_scoped_and_consumed_once(self):
        approval_id = uuid4()
        digest = "a" * 64
        async with await self.connect() as conn:
            await conn.execute(
                """INSERT INTO platform_approvals
                (tenant_id, approval_id, action, resource, risk_level, action_hash,
                 requested_by, decided_by, status, expires_at, decided_at)
                VALUES (%s,%s,'send','mail:1',2,%s,'agent','human','approved',
                        now()+interval '5 minutes',now())""",
                (self.tenant_id, approval_id, digest),
            )
            await conn.commit()

        verifier = PostgresApprovalVerifier(self.connect)
        self.assertFalse(await verifier.consume(
            tenant_id=f"other-{uuid4().hex}", approval_id=str(approval_id),
            action_digest=digest,
        ))
        self.assertTrue(await verifier.consume(
            tenant_id=self.tenant_id, approval_id=str(approval_id),
            action_digest=digest,
        ))
        self.assertFalse(await verifier.consume(
            tenant_id=self.tenant_id, approval_id=str(approval_id),
            action_digest=digest,
        ))


    async def test_event_receipts_prevent_concurrent_and_completed_reprocessing(self):
        store = PostgresEventReceiptStore(self.connect, lease_seconds=5)
        event_id = str(uuid4())
        first = await store.claim(
            tenant_id=self.tenant_id, consumer_name="worker-a", event_id=event_id
        )
        self.assertIsNotNone(first)
        self.assertEqual(first.attempts, 1)

        concurrent = await store.claim(
            tenant_id=self.tenant_id, consumer_name="worker-a", event_id=event_id
        )
        self.assertIsNone(concurrent)

        self.assertTrue(await store.fail(first, "temporary failure"))
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_event_receipts SET next_attempt_at=now()
                   WHERE tenant_id=%s AND consumer_name=%s AND event_id=%s""",
                (self.tenant_id, "worker-a", event_id),
            )
            await conn.commit()
        retry = await store.claim(
            tenant_id=self.tenant_id, consumer_name="worker-a", event_id=event_id
        )
        self.assertIsNotNone(retry)
        self.assertEqual(retry.attempts, 2)
        self.assertTrue(await store.complete(retry))
        self.assertTrue(await store.is_completed(
            tenant_id=self.tenant_id, consumer_name="worker-a", event_id=event_id
        ))
        self.assertIsNone(await store.claim(
            tenant_id=self.tenant_id, consumer_name="worker-a", event_id=event_id
        ))


    async def test_event_receipts_with_same_event_id_are_isolated_by_tenant(self):
        other_tenant = f"receipt-other-{uuid4().hex}"
        async with await self.connect() as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (other_tenant, "Other Receipt Tenant"),
            )
            await conn.commit()
        event_id = str(uuid4())
        store = PostgresEventReceiptStore(self.connect)
        tenant_a = await store.claim(
            tenant_id=self.tenant_id, consumer_name="isolation-test",
            event_id=event_id,
        )
        tenant_b = await store.claim(
            tenant_id=other_tenant, consumer_name="isolation-test",
            event_id=event_id,
        )
        self.assertIsNotNone(tenant_a)
        self.assertIsNotNone(tenant_b)
        self.assertTrue(await store.complete(tenant_a))
        self.assertTrue(await store.is_completed(
            tenant_id=self.tenant_id, consumer_name="isolation-test",
            event_id=event_id,
        ))
        self.assertFalse(await store.is_completed(
            tenant_id=other_tenant, consumer_name="isolation-test",
            event_id=event_id,
        ))

    async def test_event_receipt_claim_is_single_owner_and_fences_stale_workers(self):
        stores = [
            PostgresEventReceiptStore(self.connect, lease_seconds=5),
            PostgresEventReceiptStore(self.connect, lease_seconds=5),
        ]
        event_id = str(uuid4())
        claims = await asyncio.gather(*(
            store.claim(
                tenant_id=self.tenant_id,
                consumer_name="multi-instance-worker",
                event_id=event_id,
            )
            for store in stores
        ))
        winners = [claim for claim in claims if claim is not None]
        self.assertEqual(len(winners), 1)
        stale_claim = winners[0]

        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_event_receipts
                   SET lease_until = now() - interval '1 second'
                   WHERE tenant_id=%s AND consumer_name=%s AND event_id=%s""",
                (self.tenant_id, "multi-instance-worker", event_id),
            )
            await conn.commit()

        replacement_store = stores[1] if claims[0] is not None else stores[0]
        replacement = await replacement_store.claim(
            tenant_id=self.tenant_id,
            consumer_name="multi-instance-worker",
            event_id=event_id,
        )
        self.assertIsNotNone(replacement)
        self.assertNotEqual(stale_claim.lease_token, replacement.lease_token)
        self.assertFalse(await stores[0].complete(stale_claim))
        self.assertTrue(await replacement_store.complete(replacement))

    async def test_event_receipt_can_be_terminally_dead_lettered(self):
        store = PostgresEventReceiptStore(self.connect, lease_seconds=5)
        event_id = str(uuid4())
        claim = await store.claim(
            tenant_id=self.tenant_id, consumer_name="worker-dlq", event_id=event_id
        )
        self.assertIsNotNone(claim)
        self.assertTrue(await store.dead_letter(claim, "max attempts reached"))
        self.assertTrue(await store.is_completed(
            tenant_id=self.tenant_id, consumer_name="worker-dlq", event_id=event_id
        ))
        self.assertIsNone(await store.claim(
            tenant_id=self.tenant_id, consumer_name="worker-dlq", event_id=event_id
        ))
        async with await self.connect() as conn:
            cur = await conn.execute(
                """SELECT status, last_error, dead_lettered_at
                   FROM platform_event_receipts
                   WHERE tenant_id=%s AND consumer_name=%s AND event_id=%s""",
                (self.tenant_id, "worker-dlq", event_id),
            )
            row = await cur.fetchone()
            self.assertEqual(row["status"], "dead_lettered")
            self.assertEqual(row["last_error"], "max attempts reached")
            self.assertIsNotNone(row["dead_lettered_at"])

    async def test_audit_sink_persists_outcome(self):
        sink = PostgresAuditSink(self.connect)
        await sink.record(
            tenant_id=self.tenant_id, actor_id="expert-a", action="read",
            resource="blackboard:run-integration", action_digest="b" * 64,
            outcome="succeeded", detail="integration test",
            run_id="run-audit-integration", agent_id="expert-a",
        )
        async with await self.connect() as conn:
            cur = await conn.execute(
                "SELECT outcome, run_id, agent_id FROM platform_tool_audit WHERE tenant_id=%s",
                (self.tenant_id,),
            )
            row = await cur.fetchone()
            self.assertEqual(row["outcome"], "succeeded")
            self.assertEqual(row["run_id"], "run-audit-integration")
            self.assertEqual(row["agent_id"], "expert-a")


    async def test_workbench_worker_claims_highest_priority_first(self):
        store = PostgresRunStateStore(self.connect)
        low_priority_run = str(uuid4())
        high_priority_run = str(uuid4())
        for run_id, priority in (
            (low_priority_run, 100),
            (high_priority_run, 900),
        ):
            self.assertTrue(await store.create_task_run(
                tenant_id=self.tenant_id, run_id=run_id, actor_id="priority-user",
                agent_id="priority-agent", agent_version=1,
                template_id="priority-template", template_version=1,
                input={"prompt": run_id}, preparation_owner=f"prep-{run_id}",
                registry_snapshot=[], priority=priority,
            ))
            async with await self.connect() as conn:
                await conn.execute(
                    """UPDATE platform_runs
                       SET dispatch_ready=true, registry_snapshot_digest=%s,
                           preparation_owner=NULL, preparation_lease_expires_at=NULL
                       WHERE tenant_id=%s AND run_id=%s""",
                    ("a" * 64, self.tenant_id, run_id),
                )
                await conn.commit()

        claimed = await store.claim_workbench_tasks(
            "worker-priority-integration", tenant_id=self.tenant_id,
            limit=2, lease_seconds=30,
        )
        self.assertEqual(
            [row.run_id for row in claimed],
            [high_priority_run, low_priority_run],
        )
        # Do not leave claimed rows in the global worker queue for other integration tests.
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_runs
                   SET status='failed', finished_at=now(),
                       worker_id=NULL, lease_expires_at=NULL
                   WHERE tenant_id=%s AND run_id IN (%s,%s)""",
                (self.tenant_id, high_priority_run, low_priority_run),
            )
            await conn.commit()


    async def test_failed_workbench_task_retry_preserves_registry_snapshot_and_priority(self):
        store = PostgresRunStateStore(self.connect)
        original_run_id = str(uuid4())
        self.assertTrue(await store.create_task_run(
            tenant_id=self.tenant_id, run_id=original_run_id, actor_id="retry-user",
            agent_id="retry-agent", agent_version=2,
            template_id="retry-template", template_version=3,
            input={"prompt": "retry this"}, preparation_owner="prepare-retry",
            registry_snapshot=[{"kind": "agent", "item_id": "retry-agent", "version": 2}],
            priority=800,
        ))
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_runs
                   SET status='failed', finished_at=now(),
                       registry_snapshot_digest=%s, dispatch_ready=true,
                       preparation_owner=NULL, preparation_lease_expires_at=NULL
                   WHERE tenant_id=%s AND run_id=%s""",
                ("b" * 64, self.tenant_id, original_run_id),
            )
            await conn.commit()

        retried_run_id = await store.create_retry_task_run(
            tenant_id=self.tenant_id, task_id=original_run_id,
            actor_id="retry-user", run_id=str(uuid4()),
            idempotency_key=f"retry:{original_run_id}:integration-1",
            request_digest="c" * 64,
        )
        retried = await store.get_task_record(
            self.tenant_id, retried_run_id, actor_id="retry-user",
        )
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["priority"], 800)
        self.assertEqual(str(retried["retry_of_run_id"]), original_run_id)
        self.assertEqual(
            retried["registry_snapshot"],
            [{"kind": "agent", "item_id": "retry-agent", "version": 2}],
        )




        # Keep the globally polled workbench queue isolated from subsequent tests.
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_runs
                   SET status='cancelled', finished_at=now(), dispatch_ready=false
                   WHERE tenant_id=%s AND run_id=%s""",
                (self.tenant_id, retried_run_id),
            )
            await conn.commit()

@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class PostgresIdentityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"identity-it-{uuid4().hex}"
        self.user_id = uuid4()
        self.email = f"identity-{uuid4().hex}@example.com"
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (self.tenant_id, "Identity Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_users
                   (user_id, email, email_normalized, display_name, password_hash)
                   VALUES (%s, %s, %s, %s, %s)""",
                (self.user_id, self.email, self.email,
                 "Identity User", PasswordHasher.hash("correct horse battery staple")),
            )
            await conn.execute(
                """INSERT INTO platform_tenant_memberships
                   (tenant_id, user_id, status, joined_at)
                   VALUES (%s, %s, 'active', now())""",
                (self.tenant_id, self.user_id),
            )
            await conn.execute(
                """INSERT INTO platform_permissions (permission_key, description)
                   VALUES ('project.read', 'Read project'),
                          ('identity.role.manage', 'Manage role bindings'),
                          ('user.manage', 'Manage tenant users'),
                          ('tenant.manage', 'Manage tenant')
                   ON CONFLICT DO NOTHING"""
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name, is_system)
                   VALUES (%s, 'reader', 'reader', true)""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_permissions
                   (tenant_id, role_id, permission_key)
                   VALUES (%s, 'reader', 'project.read'),
                          (%s, 'reader', 'identity.role.manage'),
                          (%s, 'reader', 'user.manage')""",
                (self.tenant_id, self.tenant_id, self.tenant_id),
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'viewer', 'viewer')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'tenant_admin', 'tenant_admin')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_permissions
                   (tenant_id, role_id, permission_key)
                   VALUES (%s, 'tenant_admin', 'tenant.manage')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'user', %s, 'tenant', %s, 'reader', 'bootstrap')""",
                (uuid4(), self.tenant_id, str(self.user_id), self.tenant_id),
            )
            await conn.commit()

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.connect = connect
        self.repository = PostgresIdentityRepository(connect)
        self.service = IdentityService(self.repository)


    async def test_authorization_context_resolves_only_active_tenant_resources(self):
        project_ids = [f"project-{uuid4().hex[:8]}" for _ in range(3)]
        agent_id = f"agent-{uuid4().hex[:8]}"
        team_id = f"team-{uuid4().hex[:8]}"
        user_id = uuid4()
        tenant_reader_id = uuid4()
        email = f"{user_id}@example.com"
        tenant_reader_email = f"{tenant_reader_id}@example.com"
        async with await self.connect() as conn:
            for subject_id, subject_email, display_name in (
                (user_id, email, "Scoped Integration User"),
                (tenant_reader_id, tenant_reader_email, "Tenant Reader"),
            ):
                await conn.execute(
                    """INSERT INTO platform_users
                       (user_id, email, email_normalized, display_name, password_hash, status)
                       VALUES (%s, %s, %s, %s, 'integration-hash', 'active')""",
                    (subject_id, subject_email, subject_email, display_name),
                )
                await conn.execute(
                    """INSERT INTO platform_tenant_memberships
                       (tenant_id, user_id, status)
                       VALUES (%s, %s, 'active')""",
                    (self.tenant_id, subject_id),
                )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'tenant-member', 'Tenant Member')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'project-reader', 'Project Reader')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'tenant-project-reader', 'Tenant Project Reader')""",
                (self.tenant_id,),
            )
            for permission_key in ("identity.role.manage", "user.manage", "project.read",
                                   "knowledge.manage", "memory.manage", "agent.run"):
                await conn.execute(
                    """INSERT INTO platform_permissions (permission_key, description)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (permission_key, permission_key),
                )
            for permission_key in ("identity.role.manage", "user.manage"):
                await conn.execute(
                    """INSERT INTO platform_role_permissions
                       (tenant_id, role_id, permission_key)
                       VALUES (%s, 'tenant-member', %s)""",
                    (self.tenant_id, permission_key),
                )
            await conn.execute(
                """INSERT INTO platform_role_permissions
                   (tenant_id, role_id, permission_key)
                   VALUES (%s, 'project-reader', 'project.read')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_permissions
                   (tenant_id, role_id, permission_key)
                   VALUES (%s, 'tenant-project-reader', 'project.read')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'user', %s, 'tenant', %s,
                           'tenant-member', 'integration')""",
                (uuid4(), self.tenant_id, str(user_id), self.tenant_id),
            )
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'user', %s, 'tenant', %s,
                           'tenant-project-reader', 'integration')""",
                (uuid4(), self.tenant_id, str(tenant_reader_id), self.tenant_id),
            )
            for project_id in project_ids:
                await conn.execute(
                    """INSERT INTO platform_projects
                       (tenant_id, project_id, slug, name)
                       VALUES (%s, %s, %s, %s)""",
                    (self.tenant_id, project_id, project_id, project_id),
                )
            # Project membership without an RBAC binding is not an effective grant.
            await conn.execute(
                """INSERT INTO platform_project_members
                   (tenant_id, project_id, user_id, status)
                   VALUES (%s, %s, %s, 'active')""",
                (self.tenant_id, project_ids[0], user_id),
            )
            await conn.execute(
                """INSERT INTO platform_teams (tenant_id, team_id, name)
                   VALUES (%s, %s, 'Role ACL Team')""",
                (self.tenant_id, team_id),
            )
            await conn.execute(
                """INSERT INTO platform_team_memberships
                   (tenant_id, team_id, user_id, status)
                   VALUES (%s, %s, %s, 'active')""",
                (self.tenant_id, team_id, user_id),
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'team-scoped-reader', 'Team Scoped Reader')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'team', %s, 'project', %s,
                           'team-scoped-reader', 'integration')""",
                (uuid4(), self.tenant_id, team_id, project_ids[1]),
            )
            # Project 1 is read-only for the first user.
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'user', %s, 'project', %s,
                           'project-reader', 'integration')""",
                (uuid4(), self.tenant_id, str(user_id), project_ids[1]),
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'knowledge-manager', 'Knowledge Manager')""",
                (self.tenant_id,),
            )
            for permission_key in ("knowledge.manage", "memory.manage"):
                await conn.execute(
                    """INSERT INTO platform_role_permissions
                       (tenant_id, role_id, permission_key)
                       VALUES (%s, 'knowledge-manager', %s)""",
                    (self.tenant_id, permission_key),
                )
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'user', %s, 'project', %s,
                           'knowledge-manager', 'integration')""",
                (uuid4(), self.tenant_id, str(user_id), project_ids[2]),
            )
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id, agent_id, display_name, role_definition)
                   VALUES (%s, %s, 'Integration Agent', 'test agent')""",
                (self.tenant_id, agent_id),
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'agent-runner', 'Agent Runner')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_permissions
                   (tenant_id, role_id, permission_key)
                   VALUES (%s, 'agent-runner', 'agent.run')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'user', %s, 'agent', %s,
                           'agent-runner', 'integration')""",
                (uuid4(), self.tenant_id, str(user_id), agent_id),
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'agent-memory-manager', 'Agent Memory Manager')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_permissions
                   (tenant_id, role_id, permission_key)
                   VALUES (%s, 'agent-memory-manager', 'memory.manage')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'user', %s, 'agent', %s,
                           'agent-memory-manager', 'integration')""",
                (uuid4(), self.tenant_id, str(user_id), agent_id),
            )
            await conn.commit()

        repository = PostgresIdentityRepository(self.connect)
        context = await repository.get_authorization_context(
            tenant_id=self.tenant_id, user_id=str(user_id),
        )
        self.assertIsNotNone(context)
        self.assertEqual(context.tenant_roles, frozenset({"tenant-member"}))
        self.assertEqual(
            context.effective_role_ids,
            frozenset({
                "tenant-member", "project-reader", "team-scoped-reader",
                "knowledge-manager", "agent-runner", "agent-memory-manager",
            }),
        )
        self.assertEqual(
            context.tenant_permissions,
            frozenset({"identity.role.manage", "user.manage"}),
        )
        self.assertEqual(context.project_ids, frozenset(project_ids[1:]))
        self.assertEqual(
            context.knowledge_manage_project_ids, frozenset({project_ids[2]}),
        )
        self.assertEqual(
            context.memory_manage_project_ids, frozenset({project_ids[2]}),
        )
        self.assertEqual(context.agent_ids, frozenset({agent_id}))
        self.assertEqual(context.memory_manage_agent_ids, frozenset({agent_id}))

        tenant_reader_context = await repository.get_authorization_context(
            tenant_id=self.tenant_id, user_id=str(tenant_reader_id),
        )
        self.assertIsNotNone(tenant_reader_context)
        self.assertEqual(
            tenant_reader_context.effective_role_ids,
            frozenset({"tenant-project-reader"}),
        )
        self.assertEqual(tenant_reader_context.project_ids, frozenset(project_ids))
        self.assertEqual(tenant_reader_context.effective_role_ids, frozenset({"tenant-project-reader"}))
        self.assertEqual(tenant_reader_context.knowledge_manage_project_ids, frozenset())
        self.assertEqual(tenant_reader_context.agent_ids, frozenset())

        self.assertIsNone(await repository.get_authorization_context(
            tenant_id=f"other-{uuid4().hex}", user_id=str(user_id),
        ))

    async def test_service_identity_credentials_are_persisted_and_revoked_in_postgres(self):
        service_account_id = f"svc-{uuid4().hex}"
        agent_id = f"agent-{uuid4().hex}"
        async with await self.connect() as conn:
            await conn.execute(
                """INSERT INTO platform_service_accounts
                   (tenant_id, service_account_id, name, status)
                   VALUES (%s, %s, 'Integration Worker', 'active')""",
                (self.tenant_id, service_account_id),
            )
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id, agent_id, display_name, role_definition)
                   VALUES (%s, %s, 'Integration Agent', 'test agent')""",
                (self.tenant_id, agent_id),
            )
            await conn.execute(
                """INSERT INTO platform_agent_identities
                   (tenant_id, agent_id, service_account_id, status)
                   VALUES (%s, %s, %s, 'active')""",
                (self.tenant_id, agent_id, service_account_id),
            )
            await conn.execute(
                """INSERT INTO platform_permissions (permission_key, description)
                   VALUES ('agent.run', 'Run agents') ON CONFLICT DO NOTHING"""
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'machine-runner', 'Machine Runner')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_permissions
                   (tenant_id, role_id, permission_key)
                   VALUES (%s, 'machine-runner', 'agent.run')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'service_account', %s, 'agent', %s,
                           'machine-runner', 'integration')""",
                (uuid4(), self.tenant_id, service_account_id, agent_id),
            )
            await conn.commit()

        service = ServiceIdentityService(PostgresServiceIdentityRepository(self.connect))
        issued = await service.issue_credential(
            tenant_id=self.tenant_id, service_account_id=service_account_id,
            agent_id=agent_id, name="integration credential", scopes=("agent.run",),
        )
        principal = await service.authenticate(issued.token)
        self.assertEqual(principal.agent_id, agent_id)
        await service.authorize(
            principal=principal, resource=ResourceRef("agent", agent_id),
            permission_key="agent.run",
        )
        with self.assertRaises(ServiceIdentityError):
            await service.authorize(
                principal=principal, resource=ResourceRef("agent", agent_id),
                permission_key="identity.role.manage",
            )
        self.assertTrue(await service.revoke(
            tenant_id=self.tenant_id, service_account_id=service_account_id,
            credential_id=issued.credential_id,
        ))
        with self.assertRaises(ServiceIdentityError):
            await service.authenticate(issued.token)

    async def test_tenant_scoped_login_authorization_and_logout(self):
        issued = await self.service.login(
            email=self.email.upper(),
            password="correct horse battery staple",
            tenant_id=self.tenant_id,
        )
        principal = await self.service.authenticate(issued.access_token)
        self.assertEqual(principal.tenant_id, self.tenant_id)
        await self.service.authorize(
            principal=principal,
            resource=ResourceRef("project", "project-1"),
            permission_key="project.read",
        )
        self.assertTrue(await self.service.logout(principal))
        with self.assertRaises(AuthenticationError):
            await self.service.authenticate(issued.access_token)

    async def test_postgres_session_and_asset_are_isolated_across_tenants(self):
        from agent_platform.control_plane.postgres import PostgresSessionRepository
        from agent_platform.control_plane.sessions_assets import (
            AssetStatus,
            ConversationSession,
            FileAsset,
            SessionNotFound,
        )

        tenant_b = f"it-{uuid4().hex}"
        async with await self.connect() as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (tenant_b, "Second Integration Tenant"),
            )
            await conn.commit()

        repo_a = PostgresSessionRepository(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        repo_b = PostgresSessionRepository(
            tenant_id=tenant_b, connection_factory=self.connect,
        )
        service_a = SessionAssetService(repo_a, InMemoryObjectStorage())
        session_a = await service_a.create_session(
            tenant_id=self.tenant_id, owner_id="tenant-a-user",
        )
        asset_a = await service_a.upload_asset(
            tenant_id=self.tenant_id, owner_id="tenant-a-user",
            filename="tenant-a.txt", media_type="text/plain",
            content=b"tenant-a-secret", session_id=session_a.session_id,
        )

        # Tenant-B repository must not resolve either resource by the same IDs.
        self.assertIsNone(await repo_b.get_session(tenant_b, session_a.session_id))
        self.assertIsNone(await repo_b.get_asset(tenant_b, asset_a.asset_id))
        with self.assertRaises(PermissionError):
            await repo_b.create_session(ConversationSession(
                tenant_id=self.tenant_id, session_id=str(uuid4()),
                owner_id="forged-tenant-a",
            ))
        with self.assertRaises(PermissionError):
            await repo_b.create_asset(FileAsset(
                tenant_id=self.tenant_id, asset_id=str(uuid4()),
                owner_id="forged-tenant-a", object_key="tenant/a/forged",
                filename="forged.txt", media_type="text/plain", size_bytes=1,
                sha256="0" * 64, status=AssetStatus.AVAILABLE,
            ))
        self.assertEqual(
            await repo_b.list_messages(tenant_b, session_a.session_id), (),
        )

        # A tenant-B asset cannot attach to tenant-A's session. The repository
        # rejects this at the application layer with SessionNotFound (tenant-scoped
        # lookup keeps cross-tenant existence hidden); the composite FK remains a
        # defence-in-depth backstop should that check ever be bypassed.
        from psycopg.errors import ForeignKeyViolation
        with self.assertRaises((SessionNotFound, ForeignKeyViolation)):
            await repo_b.create_asset(FileAsset(
                tenant_id=tenant_b, asset_id=str(uuid4()), owner_id="tenant-b-user",
                object_key=f"tenant/{tenant_b}/assets/{uuid4().hex}",
                filename="cross-link.txt", media_type="text/plain", size_bytes=1,
                sha256="1" * 64, status=AssetStatus.AVAILABLE,
                session_id=session_a.session_id,
            ))

    async def test_nonmember_cannot_create_session_for_another_tenant(self):
        with self.assertRaises(AuthenticationError):
            await self.service.login(
                email=self.email,
                password="correct horse battery staple",
                tenant_id="tenant-not-a-member",
            )

    async def test_revoked_role_binding_takes_effect_without_session_reissue(self):
        issued = await self.service.login(
            email=self.email,
            password="correct horse battery staple",
            tenant_id=self.tenant_id,
        )
        principal = await self.service.authenticate(issued.access_token)
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_role_bindings SET revoked_at=now()
                   WHERE tenant_id=%s AND subject_id=%s""",
                (self.tenant_id, str(self.user_id)),
            )
            await conn.commit()
        with self.assertRaises(AuthorizationDenied):
            await self.service.authorize(
                principal=principal,
                resource=ResourceRef("project", "project-1"),
                permission_key="project.read",
            )



    async def test_api_credential_issue_authenticate_and_revoke(self):
        issued = await self.service.login(
            email=self.email, password="correct horse battery staple",
            tenant_id=self.tenant_id,
        )
        principal = await self.service.authenticate(issued.access_token)
        credential = await self.service.create_api_credential(
            principal=principal, name="integration-key",
            scopes=("project.read",),
        )
        authenticated = await self.service.authenticate_api_credential(credential.access_token)
        await self.service.authorize_api_credential(
            credential=authenticated, resource=ResourceRef("project", "project-1"),
            permission_key="project.read",
        )
        with self.assertRaises(AuthorizationDenied):
            await self.service.authorize_api_credential(
                credential=authenticated, resource=ResourceRef("project", "project-1"),
                permission_key="agent.run",
            )
        self.assertEqual(authenticated.tenant_id, self.tenant_id)
        self.assertEqual(authenticated.user_id, str(self.user_id))
        rotated = await self.service.rotate_api_credential(
            principal=principal, old_credential_id=credential.credential_id,
            name="integration-key", scopes=("project.read",),
        )
        with self.assertRaises(AuthenticationError):
            await self.service.authenticate_api_credential(credential.access_token)
        current = await self.service.authenticate_api_credential(rotated.access_token)
        self.assertEqual(current.credential_id, rotated.credential_id)
        self.assertTrue(await self.service.revoke_api_credential(
            principal=principal, credential_id=rotated.credential_id,
        ))
        with self.assertRaises(AuthenticationError):
            await self.service.authenticate_api_credential(rotated.access_token)



    async def test_role_grant_and_invitation_cannot_escalate_actor_privileges(self):
        issued = await self.service.login(
            email=self.email, password="correct horse battery staple",
            tenant_id=self.tenant_id,
        )
        principal = await self.service.authenticate(issued.access_token)
        with self.assertRaises(PermissionError):
            await self.service.grant_role(
                principal=principal, subject_type="user", subject_id=str(self.user_id),
                resource=ResourceRef("tenant", self.tenant_id), role_id="tenant_admin",
            )
        with self.assertRaises(PermissionError):
            await self.service.create_invitation(
                principal=principal, email=self.email, role_id="tenant_admin",
            )

    async def test_invitation_is_email_bound_single_use_and_audited(self):
        issued = await self.service.login(
            email=self.email, password="correct horse battery staple",
            tenant_id=self.tenant_id,
        )
        principal = await self.service.authenticate(issued.access_token)
        invitation_id, token, _ = await self.service.create_invitation(
            principal=principal, email=self.email, role_id="viewer",
        )
        self.assertTrue(await self.service.accept_invitation(
            principal=principal, invitation_token=token,
        ))
        with self.assertRaises(AuthenticationError):
            await self.service.accept_invitation(
                principal=principal, invitation_token=token,
            )
        async with await self.connect() as conn:
            cur = await conn.execute(
                """SELECT action FROM platform_identity_audit
                   WHERE tenant_id=%s AND action IN
                     ('invitation.created', 'invitation.accepted')
                   ORDER BY audit_id""",
                (self.tenant_id,),
            )
            self.assertEqual(
                [row["action"] for row in await cur.fetchall()],
                ["invitation.created", "invitation.accepted"],
            )
            cur = await conn.execute(
                """SELECT count(*) AS n FROM platform_role_bindings
                   WHERE tenant_id=%s AND subject_id=%s
                     AND role_id='viewer' AND revoked_at IS NULL""",
                (self.tenant_id, str(self.user_id)),
            )
            self.assertEqual((await cur.fetchone())["n"], 1)

    async def test_role_grant_and_revoke_are_audited_in_the_same_transaction(self):
        issued = await self.service.login(
            email=self.email,
            password="correct horse battery staple",
            tenant_id=self.tenant_id,
        )
        principal = await self.service.authenticate(issued.access_token)
        binding_id = await self.service.grant_role(
            principal=principal,
            subject_type="user",
            subject_id=str(self.user_id),
            resource=ResourceRef("tenant", self.tenant_id),
            role_id="viewer",
        )
        self.assertTrue(await self.service.revoke_role(
            principal=principal,
            binding_id=binding_id,
            resource=ResourceRef("tenant", self.tenant_id),
        ))
        async with await self.connect() as conn:
            cur = await conn.execute(
                """SELECT action FROM platform_identity_audit
                   WHERE tenant_id=%s AND subject_id=%s
                   ORDER BY audit_id""",
                (self.tenant_id, str(self.user_id)),
            )
            actions = [row["action"] for row in await cur.fetchall()]
            # 同一主体在同一事务前会有 auth.login 审计行，这里只校验角色变更审计
            self.assertEqual([a for a in actions if a.startswith("role.")],
                             ["role.granted", "role.revoked"])


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class PostgresTenantBootstrapIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"bootstrap-it-{uuid4().hex}"
        self.owner_email = f"owner-{uuid4().hex}@example.com"
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
        self.connection = await AsyncConnection.connect(DSN, row_factory=dict_row)
        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)
        self.connect = connect
        await self.connection.execute(
            "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
            (self.tenant_id, "Bootstrap Integration"),
        )
        await self.connection.commit()

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)
        self.connect = connect

    async def asyncTearDown(self):
        await self.connection.close()


    async def test_tenant_creation_and_owner_bootstrap_commit_atomically(self):
        tenant_id = f"new-tenant-{uuid4().hex}"
        user_id = await create_tenant_with_owner(
            self.connection,
            tenant_id=tenant_id,
            display_name="New Tenant",
            owner_email=f"new-owner-{uuid4().hex}@example.com",
            owner_display_name="New Owner",
            owner_password="correct horse battery staple",
        )
        cursor = await self.connection.execute(
            """SELECT t.display_name, m.status, b.role_id
               FROM platform_tenants t
               JOIN platform_tenant_memberships m ON m.tenant_id=t.tenant_id
               JOIN platform_role_bindings b ON b.tenant_id=t.tenant_id
                 AND b.subject_id=m.user_id::text
               WHERE t.tenant_id=%s AND m.user_id=%s""",
            (tenant_id, user_id),
        )
        row = await cursor.fetchone()
        self.assertEqual(row["display_name"], "New Tenant")
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["role_id"], "tenant_admin")

    async def test_tenant_creation_rolls_back_when_owner_bootstrap_fails(self):
        tenant_id = f"rollback-tenant-{uuid4().hex}"
        with self.assertRaises(ValueError):
            await create_tenant_with_owner(
                self.connection,
                tenant_id=tenant_id,
                display_name="Must Roll Back",
                owner_email="bad-owner@example.com",
                owner_display_name="Bad Owner",
                owner_password="short",
            )
        cursor = await self.connection.execute(
            "SELECT 1 FROM platform_tenants WHERE tenant_id=%s",
            (tenant_id,),
        )
        self.assertIsNone(await cursor.fetchone())

    async def test_bootstrap_creates_first_owner_roles_and_audit_atomically(self):
        user_id = await bootstrap_tenant_owner(
            self.connection,
            tenant_id=self.tenant_id,
            email=self.owner_email,
            display_name="Tenant Owner",
            password="correct horse battery staple",
        )
        cursor = await self.connection.execute(
            """SELECT u.email_normalized, m.status, b.role_id
               FROM platform_users u
               JOIN platform_tenant_memberships m ON m.user_id=u.user_id
               JOIN platform_role_bindings b ON b.subject_id=u.user_id::text
               WHERE u.user_id=%s AND m.tenant_id=%s AND b.tenant_id=%s""",
            (user_id, self.tenant_id, self.tenant_id),
        )
        row = await cursor.fetchone()
        self.assertEqual(row["email_normalized"], self.owner_email)
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["role_id"], "tenant_admin")
        cursor = await self.connection.execute(
            """SELECT count(*) AS n FROM platform_role_permissions
               WHERE tenant_id=%s AND role_id='tenant_admin'""",
            (self.tenant_id,),
        )
        self.assertEqual((await cursor.fetchone())["n"], 11)

    async def test_bootstrap_refuses_a_tenant_that_already_has_members(self):
        await bootstrap_tenant_owner(
            self.connection,
            tenant_id=self.tenant_id,
            email=self.owner_email,
            display_name="Tenant Owner",
            password="correct horse battery staple",
        )
        with self.assertRaises(BootstrapError):
            await bootstrap_tenant_owner(
                self.connection,
                tenant_id=self.tenant_id,
                email="second@example.com",
                display_name="Second Owner",
                password="correct horse battery staple",
            )


    async def test_run_registry_snapshot_and_tool_effect_idempotency_are_durable(self):
        agent_id = f"agent-{uuid4().hex}"
        template_id = f"template-{uuid4().hex}"
        run_id = uuid4()
        async with await self.connect() as conn:
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id,agent_id,display_name,role_definition)
                   VALUES (%s,%s,'Idempotency Integration Agent','test')""",
                (self.tenant_id, agent_id),
            )
            await conn.execute(
                """INSERT INTO platform_model_templates
                   (tenant_id,template_id,version,definition,status,created_by)
                   VALUES (%s,%s,1,'{}'::jsonb,'active','integration')""",
                (self.tenant_id, template_id),
            )
            await conn.execute(
                """INSERT INTO platform_runs
                   (tenant_id,run_id,agent_id,template_id,template_version,status)
                   VALUES (%s,%s,%s,%s,1,'queued')""",
                (self.tenant_id, run_id, agent_id, template_id),
            )
            await conn.commit()

        effects = PostgresToolEffectStore(self.connect)
        claimed = await effects.claim(
            tenant_id=self.tenant_id, run_id=str(run_id), call_id="call-1",
            action_digest="a" * 64, actor_id="integration-agent",
        )
        self.assertTrue(claimed.created)
        await effects.complete(
            tenant_id=self.tenant_id, run_id=str(run_id), call_id="call-1",
            result={"external_id": "effect-1"},
        )
        duplicate = await effects.claim(
            tenant_id=self.tenant_id, run_id=str(run_id), call_id="call-1",
            action_digest="a" * 64, actor_id="integration-agent",
        )
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.state, "succeeded")
        self.assertEqual(duplicate.result, {"external_id": "effect-1"})

        registry_repository = PostgresRegistryRepository(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        registry = RegistryService(registry_repository)
        draft = await registry.create_draft(
            tenant_id=self.tenant_id, kind="agent", item_id=agent_id,
            definition={"system_prompt": "version one"}, actor_id="integration-admin",
        )
        await registry.publish(
            tenant_id=self.tenant_id, kind="agent", item_id=agent_id,
            version=draft.version,
        )
        snapshots = PostgresRunSnapshotStore(self.connect)
        snapshot_service = RunRegistrySnapshotService(registry=registry, store=snapshots)
        snapshot = await snapshot_service.pin(
            tenant_id=self.tenant_id, run_id=str(run_id),
            references=(("agent", agent_id),),
        )
        loaded = await snapshot_service.get(
            tenant_id=self.tenant_id, run_id=str(run_id),
        )
        self.assertEqual(loaded.digest, snapshot.digest)
        next_draft = await registry.create_draft(
            tenant_id=self.tenant_id, kind="agent", item_id=agent_id,
            definition={"system_prompt": "version two"}, actor_id="integration-admin",
        )
        await registry.publish(
            tenant_id=self.tenant_id, kind="agent", item_id=agent_id,
            version=next_draft.version,
        )
        with self.assertRaises(ValueError):
            await snapshot_service.pin(
                tenant_id=self.tenant_id, run_id=str(run_id),
                references=(("agent", agent_id),),
            )

        connections = PostgresConnectorRepository(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        connection_id = f"source-{uuid4().hex}"
        await connections.create(DataConnection(
            tenant_id=self.tenant_id, connection_id=connection_id,
            provider="test-drive", secret_ref="secret://integration/source",
            scopes=("files.read",),
        ), created_by="integration")
        self.assertTrue(await connections.save_cursor(
            tenant_id=self.tenant_id, connection_id=connection_id,
            expected_version=0, cursor={"page": 2},
        ))
        self.assertFalse(await connections.save_cursor(
            tenant_id=self.tenant_id, connection_id=connection_id,
            expected_version=0, cursor={"page": 3},
        ))
        revoked = await connections.revoke(
            tenant_id=self.tenant_id, connection_id=connection_id,
        )
        self.assertEqual(revoked.status, "revoked")
        self.assertFalse(await connections.save_cursor(
            tenant_id=self.tenant_id, connection_id=connection_id,
            expected_version=revoked.version, cursor={"page": 4},
        ))

        session_repo = PostgresSessionRepository(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        session_service = SessionAssetService(session_repo, InMemoryObjectStorage())
        session = await session_service.create_session(
            tenant_id=self.tenant_id, owner_id="integration-user", title="Postgres session",
        )
        message = await session_service.append_message(
            tenant_id=self.tenant_id, actor_id="integration-user",
            session_id=session.session_id, role=MessageRole.USER,
            content="trace this file", task_id="task-integration", run_id=str(run_id),
        )
        messages = await session_repo.list_messages(self.tenant_id, session.session_id)
        self.assertEqual(messages[0].message_id, message.message_id)
        self.assertEqual(messages[0].run_id, str(run_id))
        asset = await session_service.upload_asset(
            tenant_id=self.tenant_id, owner_id="integration-user",
            filename="evidence.txt", media_type="text/plain",
            content=b"durable metadata test", session_id=session.session_id,
        )
        saved_asset, saved_content = await session_service.download_asset(
            tenant_id=self.tenant_id, asset_id=asset.asset_id,
        )
        self.assertEqual(saved_asset.sha256, asset.sha256)
        self.assertEqual(saved_content, b"durable metadata test")


    async def test_workbench_task_run_is_idempotent_actor_scoped_and_dispatch_gated(self):
        store = PostgresRunStateStore(self.connect)
        run_id = str(uuid4())
        digest = "b" * 64
        created = await store.create_task_run(
            tenant_id=self.tenant_id, run_id=run_id, actor_id="actor-a",
            agent_id="agent-registry-only", agent_version=3, template_id="template-registry-only",
            template_version=7, input={"prompt": "persisted"},
            project_id="project-a", session_id="session-a",
            preparation_owner="prep-a", idempotency_key="workbench-request-1", request_digest=digest,
            registry_snapshot=[
                {"kind": "agent", "item_id": "researcher", "version": 1},
                {"kind": "model_template", "item_id": "general", "version": 1},
                {"kind": "model_provider", "item_id": "provider-a", "version": 1},
            ],
        )
        self.assertTrue(created)
        self.assertFalse(await store.create_task_run(
            tenant_id=self.tenant_id, run_id=str(uuid4()), actor_id="actor-a",
            agent_id="agent-registry-only", agent_version=3, template_id="template-registry-only",
            template_version=7, input={"prompt": "persisted"},
            project_id="project-a", session_id="session-a",
            preparation_owner="prep-b", idempotency_key="workbench-request-1", request_digest=digest,
            registry_snapshot=[
                {"kind": "agent", "item_id": "agent-registry-only", "version": 3},
                {"kind": "model_template", "item_id": "template-registry-only", "version": 7},
                {"kind": "model_provider", "item_id": "provider-registry-only", "version": 2},
            ],
        ))
        with self.assertRaisesRegex(ValueError, "different task request"):
            await store.create_task_run(
                tenant_id=self.tenant_id, run_id=str(uuid4()), actor_id="actor-a",
                agent_id="agent-registry-only", agent_version=3, template_id="template-registry-only",
                template_version=7, input={"prompt": "changed"},
                preparation_owner="prep-b", idempotency_key="workbench-request-1", request_digest="c" * 64,
                registry_snapshot=[
                    {"kind": "agent", "item_id": "agent-registry-only", "version": 3},
                    {"kind": "model_template", "item_id": "template-registry-only", "version": 7},
                    {"kind": "model_provider", "item_id": "provider-registry-only", "version": 2},
                ],
            )

        before_pin = await store.get_task_record(
            self.tenant_id, run_id, actor_id="actor-a",
        )
        self.assertFalse(before_pin["dispatch_ready"])
        self.assertIsNone(await store.get_task_record(
            self.tenant_id, run_id, actor_id="actor-b",
        ))
        self.assertFalse(await store.mark_task_ready(
            self.tenant_id, run_id, "d" * 64, "prep-a",
        ))

        async with await self.connect() as conn:
            from psycopg.types.json import Jsonb
            await conn.execute(
                """UPDATE platform_runs SET registry_snapshot=%s,
                   registry_snapshot_digest=%s WHERE tenant_id=%s AND run_id=%s""",
                (Jsonb([]), "d" * 64, self.tenant_id, run_id),
            )
            await conn.commit()
        self.assertFalse(await store.claim_task_preparation(
            self.tenant_id, run_id, "prep-b",
        ))
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_runs SET preparation_lease_expires_at=now()-interval '1 second'
                   WHERE tenant_id=%s AND run_id=%s""",
                (self.tenant_id, run_id),
            )
            await conn.commit()
        self.assertTrue(await store.claim_task_preparation(
            self.tenant_id, run_id, "prep-b",
        ))
        self.assertFalse(await store.mark_task_ready(
            self.tenant_id, run_id, "d" * 64, "prep-a",
        ))
        self.assertTrue(await store.owns_task_preparation(
            self.tenant_id, run_id, "prep-b",
        ))
        self.assertTrue(await store.mark_task_ready(
            self.tenant_id, run_id, "d" * 64, "prep-b",
        ))
        self.assertEqual(len(await store.list_task_records(
            self.tenant_id, "actor-a",
        )), 1)
        claimed = await store.claim_workbench_tasks(
            "worker-integration", tenant_id=self.tenant_id, limit=10, lease_seconds=30,
        )
        self.assertEqual([row.run_id for row in claimed], [run_id])

        lifecycle = RunLifecycleManager(store)
        await lifecycle.start(self.tenant_id, run_id)
        await lifecycle.finish(self.tenant_id, run_id, RunStatus.SUCCEEDED)
        await store.save_output(
            self.tenant_id, run_id, {"answer": "complete"},
            usage={"total_tokens": 12}, trace_id="trace-task",
        )
        completed = await store.get_task_record(
            self.tenant_id, run_id, actor_id="actor-a",
        )
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["output"], {"answer": "complete"})
        self.assertEqual(completed["trace_id"], "trace-task")
        source_record = await store.get_run_record(self.tenant_id, run_id)
        self.assertEqual(source_record["created_by"], "actor-a")
        async with await self.connect() as conn:
            cur = await conn.execute(
                """SELECT topic,payload FROM platform_outbox
                   WHERE tenant_id=%s AND topic='memory.extraction.requested'""",
                (self.tenant_id,),
            )
            memory_events = await cur.fetchall()
        self.assertEqual(len(memory_events), 1)
        self.assertEqual(memory_events[0]["payload"]["run_id"], run_id)
        self.assertEqual(memory_events[0]["payload"]["session_id"], "session-a")
        self.assertEqual(memory_events[0]["payload"]["actor_id"], "actor-a")



    async def test_workbench_worker_lease_takeover_retry_and_dead_letter(self):
        store = PostgresRunStateStore(self.connect)
        run_id = str(uuid4())
        await store.create_task_run(
            tenant_id=self.tenant_id, run_id=run_id, actor_id="worker-owner",
            agent_id="agent-a", agent_version=1, template_id="template-a",
            template_version=1, input={"prompt": "recover me"},
            preparation_owner="prep-worker", registry_snapshot=[],
            project_id=None, session_id=None,
            idempotency_key="worker-reliability-" + run_id,
            request_digest="e" * 64,
        )
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_runs SET registry_snapshot_digest=%s
                   WHERE tenant_id=%s AND run_id=%s""",
                ("f" * 64, self.tenant_id, run_id),
            )
            await conn.commit()
        self.assertTrue(await store.mark_task_ready(
            self.tenant_id, run_id, "f" * 64, "prep-worker",
        ))

        first_claim = await store.claim_workbench_tasks(
            "worker-a", limit=1, lease_seconds=30,
        )
        self.assertEqual([row.run_id for row in first_claim], [run_id])
        self.assertEqual(await store.claim_workbench_tasks(
            "worker-b", limit=1, lease_seconds=30,
        ), [])
        lifecycle = RunLifecycleManager(store)
        await lifecycle.start(self.tenant_id, run_id)
        self.assertTrue(await store.heartbeat(
            self.tenant_id, run_id, "worker-a", lease_seconds=30,
        ))

        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_runs SET lease_expires_at=now()-interval '1 second',
                   max_worker_attempts=3 WHERE tenant_id=%s AND run_id=%s""",
                (self.tenant_id, run_id),
            )
            await conn.commit()

        replacement_claim = await store.claim_workbench_tasks(
            "worker-b", limit=1, lease_seconds=30,
        )
        self.assertEqual([row.run_id for row in replacement_claim], [run_id])
        self.assertEqual(replacement_claim[0].status, RunStatus.RUNNING)
        self.assertFalse(await store.heartbeat(
            self.tenant_id, run_id, "worker-a", lease_seconds=30,
        ))
        self.assertTrue(await store.heartbeat(
            self.tenant_id, run_id, "worker-b", lease_seconds=30,
        ))

        self.assertEqual(await store.record_workbench_failure(
            self.tenant_id, run_id, "worker-b", error="transient worker failure",
        ), "interrupted")
        retry_claim = await store.claim_workbench_tasks(
            "worker-c", limit=1, lease_seconds=30,
        )
        self.assertEqual([row.run_id for row in retry_claim], [run_id])
        self.assertEqual(retry_claim[0].status, RunStatus.INTERRUPTED)
        self.assertEqual(await store.record_workbench_failure(
            self.tenant_id, run_id, "worker-c", error="retry budget exhausted",
        ), "failed")

        async with await self.connect() as conn:
            cur = await conn.execute(
                """SELECT status,worker_attempts,max_worker_attempts,dead_lettered_at
                   FROM platform_runs WHERE tenant_id=%s AND run_id=%s""",
                (self.tenant_id, run_id),
            )
            run = await cur.fetchone()
            cur = await conn.execute(
                """SELECT attempt_count,max_attempts,reason
                   FROM platform_run_dead_letters WHERE tenant_id=%s AND run_id=%s""",
                (self.tenant_id, run_id),
            )
            dead_letter = await cur.fetchone()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["worker_attempts"], 3)
        self.assertIsNotNone(run["dead_lettered_at"])
        self.assertEqual(dead_letter["attempt_count"], 3)
        self.assertEqual(dead_letter["max_attempts"], 3)
        self.assertEqual(dead_letter["reason"], "retry budget exhausted")
        self.assertEqual(await store.claim_workbench_tasks(
            "worker-d", limit=1, lease_seconds=30,
        ), [])

    async def test_failed_and_non_session_runs_do_not_queue_memory_events(self):
        store = PostgresRunStateStore(self.connect)
        lifecycle = RunLifecycleManager(store)
        for run_id, session_id, terminal_status in (
            (str(uuid4()), "failed-session", RunStatus.FAILED),
            (str(uuid4()), None, RunStatus.SUCCEEDED),
        ):
            await store.create_task_run(
                tenant_id=self.tenant_id, run_id=run_id, actor_id="actor-a",
                agent_id="agent-a", agent_version=1, template_id="template-a",
                template_version=1, input={"text": "question"},
                preparation_owner="prep-a", registry_snapshot=[],
                project_id=None, session_id=session_id,
            )
            await lifecycle.start(self.tenant_id, run_id)
            await lifecycle.finish(self.tenant_id, run_id, terminal_status)

        async with await self.connect() as conn:
            cur = await conn.execute(
                """SELECT count(*) AS n FROM platform_outbox
                   WHERE tenant_id=%s AND topic='memory.extraction.requested'""",
                (self.tenant_id,),
            )
            self.assertEqual((await cur.fetchone())["n"], 0)

    async def test_postgres_session_repository_lists_only_owner_sessions_and_assets(self):
        repository = PostgresSessionRepository(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        service = SessionAssetService(repository, InMemoryObjectStorage())
        own = await service.create_session(
            tenant_id=self.tenant_id, owner_id="owner-a", title="Owned session",
        )
        await service.create_session(
            tenant_id=self.tenant_id, owner_id="owner-b", title="Other session",
        )
        asset = await service.upload_asset(
            tenant_id=self.tenant_id, owner_id="owner-a", filename="notes.txt",
            media_type="text/plain", content=b"notes", session_id=own.session_id,
        )
        sessions = await repository.list_sessions(self.tenant_id, "owner-a")
        assets = await repository.list_assets(self.tenant_id, "owner-a")
        self.assertEqual([row.session_id for row in sessions], [own.session_id])
        self.assertEqual([row.asset_id for row in assets], [asset.asset_id])



    async def test_postgres_audit_reader_is_tenant_scoped_and_read_only(self):
        sink = PostgresAuditSink(self.connect)
        reader = PostgresAuditReader(self.connect)
        await sink.record(
            tenant_id=self.tenant_id, actor_id="audit-user", action="tool.invoke",
            resource="tool:search", action_digest="a" * 64,
            outcome="succeeded", run_id="audit-run", agent_id="researcher",
            detail="search completed",
        )
        items = await reader.list_events(
            tenant_id=self.tenant_id, actor_id="audit-user", run_id="audit-run",
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["action"], "tool.invoke")
        self.assertEqual(
            await reader.list_events(tenant_id="another-tenant", actor_id="audit-user"), (),
        )



    async def test_postgres_session_repository_lists_only_owner_sessions_and_assets(self):
        repository = PostgresSessionRepository(
            tenant_id=self.tenant_id, connection_factory=self.connect,
        )
        service = SessionAssetService(repository, InMemoryObjectStorage())
        own = await service.create_session(
            tenant_id=self.tenant_id, owner_id="owner-a", title="Owned session",
        )
        await service.create_session(
            tenant_id=self.tenant_id, owner_id="owner-b", title="Other session",
        )
        asset = await service.upload_asset(
            tenant_id=self.tenant_id, owner_id="owner-a", filename="notes.txt",
            media_type="text/plain", content=b"notes", session_id=own.session_id,
        )
        sessions = await repository.list_sessions(self.tenant_id, "owner-a")
        assets = await repository.list_assets(self.tenant_id, "owner-a")
        self.assertEqual([row.session_id for row in sessions], [own.session_id])
        self.assertEqual([row.asset_id for row in assets], [asset.asset_id])


if __name__ == "__main__":
    unittest.main()
