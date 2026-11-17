"""PostgreSQL-backed quota isolation and concurrency reliability tests."""

import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class QuotaPostgresSecurityReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_tenant_isolation_and_concurrent_reserve_limit(self):
        tenant_id = f"quota-security-{uuid4().hex}"
        agent_id = f"agent-{uuid4().hex}"
        template_id = f"template-{uuid4().hex}"
        run_ids = (uuid4(), uuid4(), uuid4())
        reservation_ids = (uuid4(), uuid4(), uuid4())
        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        end = start + timedelta(hours=1)

        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(migration.read_text(), prepare=False)
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (tenant_id, "Quota Security Integration"),
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
            for run_id in run_ids:
                await conn.execute(
                    """INSERT INTO platform_runs
                       (tenant_id, run_id, agent_id, template_id, template_version, status)
                       VALUES (%s, %s, %s, %s, 1, 'queued')""",
                    (tenant_id, run_id, agent_id, template_id),
                )
            await conn.execute(
                """INSERT INTO platform_quota_budgets
                   (tenant_id, scope_type, scope_id, period_start, period_end,
                    hard_tokens, hard_concurrency)
                   VALUES (%s, 'tenant', %s, %s, %s, 100, 1)""",
                (tenant_id, tenant_id, start, end),
            )

        async def reserve(index: int):
            async with await AsyncConnection.connect(DSN) as conn:
                try:
                    result = await conn.execute(
                        """SELECT * FROM platform_quota_reserve
                           (%s, %s, %s, 'tenant', %s, %s, 1, 0.01)""",
                        (reservation_ids[index], tenant_id, run_ids[index], tenant_id, start),
                    )
                    await conn.commit()
                    return (await result.fetchone())[0]
                except Exception as exc:
                    await conn.rollback()
                    return exc

        outcomes = await asyncio.gather(reserve(0), reserve(1))
        self.assertEqual(sum(not isinstance(item, Exception) for item in outcomes), 1)
        self.assertEqual(sum(isinstance(item, Exception) for item in outcomes), 1)
        async with await AsyncConnection.connect(DSN) as conn:
            rows = await (
                await conn.execute(
                    """SELECT tenant_id, COUNT(*) FROM platform_quota_reservations
                       WHERE tenant_id=%s AND status='active' GROUP BY tenant_id""",
                    (tenant_id,),
                )
            ).fetchall()
        self.assertEqual(rows, [(tenant_id, 1)])
