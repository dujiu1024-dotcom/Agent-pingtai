"""PostgreSQL integration checks for append-only audit triggers."""

import os
import unittest
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class AuditAppendOnlyPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"audit-it-{uuid4().hex}"
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (self.tenant_id, "Audit Integration"),
            )

    async def test_audit_rows_reject_update_delete_and_truncate(self):
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                """INSERT INTO platform_tool_audit
                   (tenant_id, actor_id, action, resource, action_digest, outcome)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (self.tenant_id, "test-actor", "sandbox.execute", "run:test", "a" * 64, "started"),
            )
            await conn.execute(
                """INSERT INTO platform_identity_audit
                   (tenant_id, actor_id, action, subject_id, outcome)
                   VALUES (%s, %s, %s, %s, %s)""",
                (self.tenant_id, "test-actor", "identity.test", "subject:test", "succeeded"),
            )
            await conn.commit()

        statements = (
            ("UPDATE platform_tool_audit SET detail='tampered' WHERE tenant_id=%s", True),
            ("DELETE FROM platform_tool_audit WHERE tenant_id=%s", True),
            ("TRUNCATE platform_tool_audit", False),
            ("UPDATE platform_identity_audit SET detail='{}'::jsonb WHERE tenant_id=%s", True),
            ("DELETE FROM platform_identity_audit WHERE tenant_id=%s", True),
            ("TRUNCATE platform_identity_audit", False),
        )
        for statement, needs_tenant in statements:
            async with await AsyncConnection.connect(DSN) as conn:
                with self.assertRaises(Exception):
                    if needs_tenant:
                        await conn.execute(statement, (self.tenant_id,))
                    else:
                        await conn.execute(statement)
                await conn.rollback()

        async with await AsyncConnection.connect(DSN) as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM platform_tool_audit WHERE tenant_id=%s", (self.tenant_id,)
            )
            self.assertEqual((await cur.fetchone())[0], 1)
            cur = await conn.execute(
                "SELECT count(*) FROM platform_identity_audit WHERE tenant_id=%s", (self.tenant_id,)
            )
            self.assertEqual((await cur.fetchone())[0], 1)


if __name__ == "__main__":
    unittest.main()
