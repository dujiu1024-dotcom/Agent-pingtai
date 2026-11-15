"""Real PostgreSQL integration tests; enabled only with AGENT_PLATFORM_TEST_DSN."""

import asyncio
import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from agent_platform.approval.postgres import PostgresApprovalVerifier, PostgresAuditSink
    from agent_platform.blackboard.postgres_repository import PostgresBlackboardRepository
    from agent_platform.blackboard.protocol import VersionConflict
    from agent_platform.contracts import (
        BlackboardEntry,
        BlackboardEvent,
        EntryKind,
        EntryStatus,
        Scope,
    )
    from agent_platform.events.outbox import PostgresOutboxStore


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class PostgresPlatformIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"it-{uuid4().hex}"
        migration = Path("agent_platform/storage/postgres/001_platform_core.sql").read_text()
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            await conn.execute(migration, prepare=False)
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
        ids_a = {item.outbox_id for item in a}
        ids_b = {item.outbox_id for item in b}
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

    async def test_audit_sink_persists_outcome(self):
        sink = PostgresAuditSink(self.connect)
        await sink.record(
            tenant_id=self.tenant_id, actor_id="expert-a", action="read",
            resource="blackboard:run-integration", action_digest="b" * 64,
            outcome="succeeded", detail="integration test",
        )
        async with await self.connect() as conn:
            cur = await conn.execute(
                "SELECT outcome FROM platform_tool_audit WHERE tenant_id=%s",
                (self.tenant_id,),
            )
            self.assertEqual((await cur.fetchone())["outcome"], "succeeded")


if __name__ == "__main__":
    unittest.main()
