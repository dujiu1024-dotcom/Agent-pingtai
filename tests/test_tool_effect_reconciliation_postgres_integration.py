"""PostgreSQL schema checks for operator reconciliation of tool effects."""

import os
import unittest
from pathlib import Path

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class ToolEffectReconciliationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconciliation_migration_installs_state_and_audit_columns(self):
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)
            columns = await (
                await conn.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_name='platform_tool_effects'
                         AND column_name IN (
                           'resolution','reconciled_by','reconciliation_note','reconciled_at'
                         )"""
                )
            ).fetchall()
            audit_table = await (
                await conn.execute(
                    """SELECT to_regclass('public.platform_tool_effect_reconciliations')"""
                )
            ).fetchone()
            constraint = await (
                await conn.execute(
                    """SELECT pg_get_constraintdef(oid) FROM pg_constraint
                       WHERE conrelid='platform_tool_effects'::regclass
                         AND conname='platform_tool_effects_state_check'"""
                )
            ).fetchone()
        self.assertEqual(
            {row[0] for row in columns},
            {"resolution","reconciled_by","reconciliation_note","reconciled_at"},
        )
        self.assertIsNotNone(audit_table[0])
        self.assertIn("retryable", constraint[0])
        self.assertIn("compensated", constraint[0])


if __name__ == "__main__":
    unittest.main()
