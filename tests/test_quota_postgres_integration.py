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
