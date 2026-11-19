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
            context_columns = await (
                await conn.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_name='platform_quota_budgets'
                         AND column_name='hard_context_window_tokens'"""
                )
            ).fetchall()
            context_columns = await (
                await conn.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_name='platform_quota_budgets'
                         AND column_name='hard_context_window_tokens'"""
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
        self.assertEqual(len(context_columns), 1)

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

    async def test_root_run_budget_aggregates_child_run_usage(self):
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal
        from uuid import uuid4

        from agent_platform.quotas.postgres import PostgresQuotaStore
        from agent_platform.quotas.service import Budget, BudgetExceeded, QuotaService

        tenant_id = f"quota-root-run-it-{uuid4().hex}"
        agent_id = f"agent-{uuid4().hex}"
        template_id = f"template-{uuid4().hex}"
        root_run_id, child_run_id = uuid4(), uuid4()
        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        end = start + timedelta(hours=1)
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration_path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (tenant_id, "Root Run Quota Integration"),
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
            for run_id in (root_run_id, child_run_id):
                await conn.execute(
                    """INSERT INTO platform_runs
                       (tenant_id, run_id, agent_id, template_id, template_version, status)
                       VALUES (%s, %s, %s, %s, 1, 'queued')""",
                    (tenant_id, run_id, agent_id, template_id),
                )
        service = QuotaService(PostgresQuotaStore.from_dsn(DSN))
        await service.configure(Budget(
            tenant_id=tenant_id, scope_type="run", scope_id=str(root_run_id),
            period_start=start, period_end=end, hard_tokens=100,
        ))
        reservation, _ = await service.reserve_run(
            tenant_id=tenant_id, run_id=str(child_run_id), root_run_id=str(root_run_id),
            scope_type="run", scope_id=str(root_run_id), estimated_tokens=80,
            estimated_cost_usd=Decimal("0.20"),
        )
        event = await service.settle_run(
            reservation, model_key="model-child", input_tokens=30, output_tokens=20,
            cost_usd=Decimal("0.10"),
        )
        self.assertEqual(event.root_run_id, str(root_run_id))
        tokens, cost, concurrency = await service.store.usage(
            tenant_id, "run", str(root_run_id), start, end,
        )
        self.assertEqual((tokens, cost, concurrency), (50, Decimal("0.10"), 0))
        with self.assertRaises(BudgetExceeded):
            await service.reserve_run(
                tenant_id=tenant_id, run_id=str(uuid4()), root_run_id=str(root_run_id),
                scope_type="run", scope_id=str(root_run_id), estimated_tokens=51,
                estimated_cost_usd=Decimal("0.10"),
            )


    async def test_versioned_pricing_cards_persist_and_remain_immutable(self):
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal

        from psycopg.rows import dict_row

        from agent_platform.quotas.pricing import PricingCard, PricingFeed
        from agent_platform.quotas.pricing_store import (
            PostgresPricingCardRepository,
            PricingCardConflict,
        )

        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration_path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        repository = PostgresPricingCardRepository(connect)
        now = datetime.now(timezone.utc)
        feed = PricingFeed(
            source="integration-provider", revision=f"revision-{now.isoformat()}",
            fetched_at=now,
            cards=(PricingCard(
                "integration-model", f"version-{now.timestamp()}",
                Decimal("1.25"), Decimal("4.50"), effective_at=now,
            ),),
        )
        self.assertEqual(await repository.save_feed(feed, created_by="integration"), 1)
        self.assertEqual(await repository.save_feed(feed, created_by="integration"), 1)
        cards = await repository.list_current(as_of=now, source=feed.source)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].input_usd_per_million, Decimal("1.25"))

        changed = PricingFeed(
            source=feed.source, revision=f"revision-next-{now.isoformat()}",
            fetched_at=now + timedelta(seconds=1),
            cards=(PricingCard(
                feed.cards[0].model_key, feed.cards[0].version,
                Decimal("9.99"), Decimal("9.99"), effective_at=now,
            ),),
        )
        with self.assertRaises(PricingCardConflict):
            await repository.save_feed(changed, created_by="integration")



    async def test_reservation_lease_heartbeat_and_crash_recovery(self):
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        from agent_platform.quotas.postgres import PostgresQuotaStore

        tenant_id = f"quota-lease-it-{uuid4().hex}"
        agent_id, template_id, run_id = f"agent-{uuid4().hex}", f"template-{uuid4().hex}", uuid4()
        reservation_id = uuid4()
        period_start = datetime.now(timezone.utc) - timedelta(minutes=1)
        period_end = period_start + timedelta(hours=1)

        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (tenant_id, "Quota Lease Integration"),
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
                   VALUES (%s, %s, %s, %s, 1, 'running')""",
                (tenant_id, run_id, agent_id, template_id),
            )
            await conn.execute(
                """INSERT INTO platform_quota_budgets
                   (tenant_id, scope_type, scope_id, period_start, period_end, hard_concurrency)
                   VALUES (%s, 'worker_pool', 'lease-test', %s, %s, 1)""",
                (tenant_id, period_start, period_end),
            )
            await conn.execute(
                """INSERT INTO platform_quota_reservations
                   (reservation_id, tenant_id, run_id, scope_type, scope_id, period_start,
                    reserved_tokens, reserved_cost_usd, status)
                   VALUES (%s, %s, %s, 'worker_pool', 'lease-test', %s, 0, 0, 'active')""",
                (reservation_id, tenant_id, run_id, period_start),
            )

        store = PostgresQuotaStore.from_dsn(DSN)
        self.assertTrue(await store.heartbeat_reservation(str(reservation_id), lease_seconds=60))
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            await conn.execute(
                "UPDATE platform_quota_reservations SET lease_expires_at=now()-interval '1 second' WHERE reservation_id=%s",
                (reservation_id,),
            )
        self.assertFalse(await store.heartbeat_reservation(str(reservation_id), lease_seconds=60))
        self.assertEqual(await store.expire_stale_reservations(limit=10), 1)
        async with await AsyncConnection.connect(DSN) as conn:
            cursor = await conn.execute(
                "SELECT status FROM platform_quota_reservations WHERE reservation_id=%s",
                (reservation_id,),
            )
            self.assertEqual((await cursor.fetchone())[0], "expired")
