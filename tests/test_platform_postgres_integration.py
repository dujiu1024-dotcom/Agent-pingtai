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
    from agent_platform.events.receipts import PostgresEventReceiptStore
    from agent_platform.identity.postgres import PostgresIdentityRepository
    from agent_platform.identity.security import PasswordHasher
    from agent_platform.identity.service import IdentityService, AuthenticationError, AuthorizationDenied
    from agent_platform.identity.models import ResourceRef


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


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class PostgresIdentityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"identity-it-{uuid4().hex}"
        self.user_id = uuid4()
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
                (self.user_id, "identity@example.com", "identity@example.com",
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
                          ('identity.role.manage', 'Manage role bindings')
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
                          (%s, 'reader', 'identity.role.manage')""",
                (self.tenant_id, self.tenant_id),
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'viewer', 'viewer')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'user', %s, 'project', 'project-1', 'reader', 'bootstrap')""",
                (uuid4(), self.tenant_id, str(self.user_id)),
            )
            await conn.commit()

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.connect = connect
        self.repository = PostgresIdentityRepository(connect)
        self.service = IdentityService(self.repository)

    async def test_tenant_scoped_login_authorization_and_logout(self):
        issued = await self.service.login(
            email="IDENTITY@example.com",
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

    async def test_nonmember_cannot_create_session_for_another_tenant(self):
        with self.assertRaises(AuthenticationError):
            await self.service.login(
                email="identity@example.com",
                password="correct horse battery staple",
                tenant_id="tenant-not-a-member",
            )

    async def test_revoked_role_binding_takes_effect_without_session_reissue(self):
        issued = await self.service.login(
            email="identity@example.com",
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

    async def test_role_grant_and_revoke_are_audited_in_the_same_transaction(self):
        issued = await self.service.login(
            email="identity@example.com",
            password="correct horse battery staple",
            tenant_id=self.tenant_id,
        )
        principal = await self.service.authenticate(issued.access_token)
        binding_id = await self.service.grant_role(
            principal=principal,
            subject_type="user",
            subject_id="user-target",
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
                   WHERE tenant_id=%s AND subject_id='user-target'
                   ORDER BY audit_id""",
                (self.tenant_id,),
            )
            self.assertEqual([row["action"] for row in await cur.fetchall()],
                             ["role.granted", "role.revoked"])


if __name__ == "__main__":
    unittest.main()
