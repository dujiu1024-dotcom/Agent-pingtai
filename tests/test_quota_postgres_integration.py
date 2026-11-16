"""PostgreSQL migration smoke tests for quota schema and server-side functions."""

import os
import unittest
from pathlib import Path

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class QuotaPostgresMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_migration_installs_tables_and_atomic_functions(self):
        migration_dir = Path("agent_platform/storage/postgres")
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
            rows = await (
                await conn.execute(
                    """SELECT proname FROM pg_proc
                       WHERE proname IN (
                         'platform_quota_reserve',
                         'platform_quota_reserve_split',
                         'platform_quota_settle',
                         'platform_quota_release',
                         'platform_quota_renew',
                         'platform_quota_expire'
                       )"""
                )
            ).fetchall()
            tables = await (
                await conn.execute(
                    """SELECT tablename FROM pg_tables
                       WHERE schemaname='public' AND tablename IN (
                         'platform_quota_budgets',
                         'platform_quota_reservations',
                         'platform_usage_ledger',
                         'platform_usage_reconciliation'
                       )"""
                )
            ).fetchall()
            columns = await (
                await conn.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_name='platform_quota_reservations' AND column_name IN ('heartbeat_at', 'expires_at')"""
                )
            ).fetchall()
        self.assertEqual({row[0] for row in rows}, {
            "platform_quota_reserve", "platform_quota_reserve_split",
            "platform_quota_settle", "platform_quota_release",
            "platform_quota_renew", "platform_quota_expire",
        })
        self.assertEqual({row[0] for row in tables}, {
            "platform_quota_budgets", "platform_quota_reservations",
            "platform_usage_ledger", "platform_usage_reconciliation",
        })
        self.assertEqual({row[0] for row in columns}, {"heartbeat_at", "expires_at"})

    async def test_reserve_enforces_hard_limits_with_empty_ledger_and_reservations(self):
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        tenant_id = f"quota-reserve-it-{uuid4().hex}"
        agent_id = f"agent-{uuid4().hex}"
        template_id = f"template-{uuid4().hex}"
        run_a, run_b = uuid4(), uuid4()
        reservation_a, reservation_b = uuid4(), uuid4()
        period_start = datetime.now(timezone.utc) - timedelta(minutes=1)
        period_end = period_start + timedelta(hours=1)

        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (tenant_id, "Quota Reservation Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id, agent_id, display_name, role_definition)
                   VALUES (%s, %s, 'Quota Agent', 'integration')""",
                (tenant_id, agent_id),
            )
            await conn.execute(
                """INSERT INTO platform_model_templates
                   (tenant_id, template_id, version, definition, status, created_by)
                   VALUES (%s, %s, 1, '{}'::jsonb, 'active', 'integration')""",
                (tenant_id, template_id),
            )
            for run_id in (run_a, run_b):
                await conn.execute(
                    """INSERT INTO platform_runs
                       (tenant_id, run_id, agent_id, template_id, template_version, status)
                       VALUES (%s, %s, %s, %s, 1, 'queued')""",
                    (tenant_id, run_id, agent_id, template_id),
                )
            await conn.execute(
                """INSERT INTO platform_quota_budgets
                   (tenant_id, scope_type, scope_id, period_start, period_end,
                    hard_tokens, hard_cost_usd, hard_concurrency)
                   VALUES (%s, 'tenant', %s, %s, %s, 5, 1.00, 2)""",
                (tenant_id, tenant_id, period_start, period_end),
            )

        async with await AsyncConnection.connect(DSN) as conn:
            with self.assertRaises(Exception) as raised:
                await conn.execute(
                    """SELECT * FROM platform_quota_reserve
                       (%s, %s, %s, 'tenant', %s, %s, 6, 0.10)""",
                    (reservation_a, tenant_id, run_a, tenant_id, period_start),
                )
            self.assertIn("hard token budget exceeded", str(raised.exception))
            await conn.rollback()

        async with await AsyncConnection.connect(DSN) as conn:
            cursor = await conn.execute(
                """SELECT * FROM platform_quota_reserve
                   (%s, %s, %s, 'tenant', %s, %s, 5, 0.10)""",
                (reservation_a, tenant_id, run_a, tenant_id, period_start),
            )
            self.assertEqual((await cursor.fetchone())[0], reservation_a)
            await conn.commit()

        async with await AsyncConnection.connect(DSN) as conn:
            with self.assertRaises(Exception) as raised:
                await conn.execute(
                    """SELECT * FROM platform_quota_reserve
                       (%s, %s, %s, 'tenant', %s, %s, 1, 0.10)""",
                    (reservation_b, tenant_id, run_b, tenant_id, period_start),
                )
            self.assertIn("hard token budget exceeded", str(raised.exception))
            await conn.rollback()


    async def test_split_reservation_enforces_input_and_output_limits(self):
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        tenant_id = f"quota-split-it-{uuid4().hex}"
        agent_id = f"agent-{uuid4().hex}"
        template_id = f"template-{uuid4().hex}"
        run_id = uuid4()
        period_start = datetime.now(timezone.utc) - timedelta(minutes=1)
        period_end = period_start + timedelta(hours=1)
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (tenant_id, "Quota Split Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id, agent_id, display_name, role_definition)
                   VALUES (%s, %s, 'Quota Agent', 'integration')""",
                (tenant_id, agent_id),
            )
            await conn.execute(
                """INSERT INTO platform_model_templates
                   (tenant_id, template_id, version, definition, status, created_by)
                   VALUES (%s, %s, 1, '{}'::jsonb, 'active', 'integration')""",
                (tenant_id, template_id),
            )
            await conn.execute(
                """INSERT INTO platform_runs
                   (tenant_id, run_id, agent_id, template_id, template_version, status)
                   VALUES (%s, %s, %s, %s, 1, 'queued')""",
                (tenant_id, run_id, agent_id, template_id),
            )
            await conn.execute(
                """INSERT INTO platform_quota_budgets
                   (tenant_id, scope_type, scope_id, period_start, period_end,
                    hard_tokens, hard_input_tokens, hard_output_tokens)
                   VALUES (%s, 'tenant', %s, %s, %s, 100, 40, 20)""",
                (tenant_id, tenant_id, period_start, period_end),
            )
        async with await AsyncConnection.connect(DSN) as conn:
            with self.assertRaises(Exception) as raised:
                await conn.execute(
                    """SELECT * FROM platform_quota_reserve_split
                       (%s, %s, %s, 'tenant', %s, %s, 60, 0.10, 41, 19)""",
                    (uuid4(), tenant_id, run_id, tenant_id, period_start),
                )
            self.assertIn("hard input token budget exceeded", str(raised.exception))
            await conn.rollback()
        async with await AsyncConnection.connect(DSN) as conn:
            cursor = await conn.execute(
                """SELECT * FROM platform_quota_reserve_split
                   (%s, %s, %s, 'tenant', %s, %s, 60, 0.10, 40, 20)""",
                (uuid4(), tenant_id, run_id, tenant_id, period_start),
            )
            self.assertIsNotNone(await cursor.fetchone())
            await conn.commit()



    async def test_queue_concurrency_scope_is_shared_and_released_atomically(self):
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        tenant_id = f"quota-queue-it-{uuid4().hex}"
        agent_id = f"agent-{uuid4().hex}"
        template_id = f"template-{uuid4().hex}"
        run_a, run_b = uuid4(), uuid4()
        reservation_a, reservation_b = uuid4(), uuid4()
        period_start = datetime.now(timezone.utc) - timedelta(minutes=1)
        period_end = period_start + timedelta(hours=1)
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (tenant_id, "Queue Concurrency Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id, agent_id, display_name, role_definition)
                   VALUES (%s, %s, 'Queue Agent', 'integration')""",
                (tenant_id, agent_id),
            )
            await conn.execute(
                """INSERT INTO platform_model_templates
                   (tenant_id, template_id, version, definition, status, created_by)
                   VALUES (%s, %s, 1, '{}'::jsonb, 'active', 'integration')""",
                (tenant_id, template_id),
            )
            for run_id in (run_a, run_b):
                await conn.execute(
                    """INSERT INTO platform_runs
                       (tenant_id, run_id, agent_id, template_id, template_version, status)
                       VALUES (%s, %s, %s, %s, 1, 'queued')""",
                    (tenant_id, run_id, agent_id, template_id),
                )
            await conn.execute(
                """INSERT INTO platform_quota_budgets
                   (tenant_id, scope_type, scope_id, period_start, period_end, hard_concurrency)
                   VALUES (%s, 'queue', 'priority', %s, %s, 1)""",
                (tenant_id, period_start, period_end),
            )
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                """SELECT * FROM platform_quota_reserve_split
                   (%s, %s, %s, 'queue', 'priority', %s, 0, 0, 0, 0)""",
                (reservation_a, tenant_id, run_a, period_start),
            )
            await conn.commit()
        async with await AsyncConnection.connect(DSN) as conn:
            with self.assertRaises(Exception) as raised:
                await conn.execute(
                    """SELECT * FROM platform_quota_reserve_split
                       (%s, %s, %s, 'queue', 'priority', %s, 0, 0, 0, 0)""",
                    (reservation_b, tenant_id, run_b, period_start),
                )
            self.assertIn("concurrency quota exceeded", str(raised.exception))
            await conn.rollback()
        async with await AsyncConnection.connect(DSN) as conn:
            cursor = await conn.execute(
                "SELECT platform_quota_release(%s)", (reservation_a,),
            )
            self.assertTrue((await cursor.fetchone())[0])
            await conn.commit()
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                """SELECT * FROM platform_quota_reserve_split
                   (%s, %s, %s, 'queue', 'priority', %s, 0, 0, 0, 0)""",
                (reservation_b, tenant_id, run_b, period_start),
            )
            await conn.commit()

    async def test_cost_reconciliation_is_idempotent_and_rejects_invoice_drift(self):
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal
        from uuid import uuid4

        from agent_platform.quotas.postgres import PostgresQuotaStore

        tenant_id = f"quota-recon-it-{uuid4().hex}"
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = start + timedelta(hours=1)
        store = PostgresQuotaStore.from_dsn(DSN)
        first = await store.reconcile_cost(
            tenant_id, start, end, source="invoice-001", actual_cost_usd=Decimal("2.50"),
        )
        second = await store.reconcile_cost(
            tenant_id, start, end, source="invoice-001", actual_cost_usd=Decimal("2.50"),
        )
        self.assertEqual(first["reconciliation_id"], second["reconciliation_id"])
        self.assertEqual(Decimal(first["variance_usd"]), Decimal("2.50"))
        with self.assertRaises(ValueError):
            await store.reconcile_cost(
                tenant_id, start, end, source="invoice-001", actual_cost_usd=Decimal("3.00"),
            )

    async def test_reservation_lease_renewal_and_expiration_are_terminal_and_idempotent(self):
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        tenant_id = f"quota-lease-it-{uuid4().hex}"
        agent_id = f"agent-{uuid4().hex}"
        template_id = f"template-{uuid4().hex}"
        run_id = uuid4()
        reservation_id = uuid4()
        period_start = datetime.now(timezone.utc) - timedelta(minutes=1)
        period_end = period_start + timedelta(hours=1)

        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (tenant_id, "Quota Reservation Lease Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id, agent_id, display_name, role_definition)
                   VALUES (%s, %s, 'Lease Agent', 'integration')""",
                (tenant_id, agent_id),
            )
            await conn.execute(
                """INSERT INTO platform_model_templates
                   (tenant_id, template_id, version, definition, status, created_by)
                   VALUES (%s, %s, 1, '{}'::jsonb, 'active', 'integration')""",
                (tenant_id, template_id),
            )
            await conn.execute(
                """INSERT INTO platform_runs
                   (tenant_id, run_id, agent_id, template_id, template_version, status)
                   VALUES (%s, %s, %s, %s, 1, 'queued')""",
                (tenant_id, run_id, agent_id, template_id),
            )
            await conn.execute(
                """INSERT INTO platform_quota_budgets
                   (tenant_id, scope_type, scope_id, period_start, period_end, hard_concurrency)
                   VALUES (%s, 'tenant', %s, %s, %s, 1)""",
                (tenant_id, tenant_id, period_start, period_end),
            )
            await conn.execute(
                """SELECT * FROM platform_quota_reserve_split
                   (%s, %s, %s, 'tenant', %s, %s, 0, 0, 0, 0)""",
                (reservation_id, tenant_id, run_id, tenant_id, period_start),
            )
            await conn.execute(
                """UPDATE platform_quota_reservations
                   SET expires_at=now() - interval '1 second'
                   WHERE reservation_id=%s""",
                (reservation_id,),
            )

        async with await AsyncConnection.connect(DSN) as conn:
            cursor = await conn.execute(
                "SELECT platform_quota_renew(%s, 900)", (reservation_id,),
            )
            self.assertFalse((await cursor.fetchone())[0])
            cursor = await conn.execute("SELECT platform_quota_expire(10)")
            self.assertEqual((await cursor.fetchone())[0], 1)
            await conn.commit()

        async with await AsyncConnection.connect(DSN) as conn:
            cursor = await conn.execute(
                "SELECT status FROM platform_quota_reservations WHERE reservation_id=%s",
                (reservation_id,),
            )
            self.assertEqual((await cursor.fetchone())[0], "expired")
            cursor = await conn.execute("SELECT platform_quota_expire(10)")
            self.assertEqual((await cursor.fetchone())[0], 0)
            await conn.commit()
