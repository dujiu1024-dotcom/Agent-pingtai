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
                         'platform_quota_settle',
                         'platform_quota_release'
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
                       WHERE table_name='platform_usage_ledger' AND column_name='reservation_id'"""
                )
            ).fetchall()
        self.assertEqual({row[0] for row in rows}, {
            "platform_quota_reserve", "platform_quota_settle", "platform_quota_release",
        })
        self.assertEqual({row[0] for row in tables}, {
            "platform_quota_budgets", "platform_quota_reservations",
            "platform_usage_ledger", "platform_usage_reconciliation",
        })
        self.assertEqual(len(columns), 1)

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


    async def test_split_reservation_enforces_input_and_output_limits(self):
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4
        tenant_id = f"quota-split-it-{uuid4().hex}"
        agent_id, template_id, run_id = f"agent-{uuid4().hex}", f"template-{uuid4().hex}", uuid4()
        period_start = datetime.now(timezone.utc) - timedelta(minutes=1)
        period_end = period_start + timedelta(hours=1)
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
            await conn.execute("INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)", (tenant_id, "Quota Split Integration"))
            await conn.execute("""INSERT INTO platform_agents (tenant_id, agent_id, display_name, role_definition) VALUES (%s, %s, 'Quota Agent', 'integration')""", (tenant_id, agent_id))
            await conn.execute("""INSERT INTO platform_model_templates (tenant_id, template_id, version, definition, status, created_by) VALUES (%s, %s, 1, '{}'::jsonb, 'active', 'integration')""", (tenant_id, template_id))
            await conn.execute("""INSERT INTO platform_runs (tenant_id, run_id, agent_id, template_id, template_version, status) VALUES (%s, %s, %s, %s, 1, 'queued')""", (tenant_id, run_id, agent_id, template_id))
            await conn.execute("""INSERT INTO platform_quota_budgets (tenant_id, scope_type, scope_id, period_start, period_end, hard_tokens, hard_input_tokens, hard_output_tokens) VALUES (%s, 'tenant', %s, %s, %s, 100, 40, 20)""", (tenant_id, tenant_id, period_start, period_end))
        async with await AsyncConnection.connect(DSN) as conn:
            with self.assertRaises(Exception) as raised:
                await conn.execute("""SELECT * FROM platform_quota_reserve_split(%s, %s, %s, 'tenant', %s, %s, 60, 0.10, 41, 19)""", (uuid4(), tenant_id, run_id, tenant_id, period_start))
            self.assertIn("hard input token budget exceeded", str(raised.exception))
            await conn.rollback()
        async with await AsyncConnection.connect(DSN) as conn:
            cursor = await conn.execute("""SELECT * FROM platform_quota_reserve_split(%s, %s, %s, 'tenant', %s, %s, 60, 0.10, 40, 20)""", (uuid4(), tenant_id, run_id, tenant_id, period_start))
            self.assertIsNotNone(await cursor.fetchone())
            await conn.commit()

