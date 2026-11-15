"""Tenant creation transaction integration tests (PostgreSQL)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")

if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from agent_platform.identity.tenants import create_tenant_with_owner


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class TenantCreationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"tenant-create-{uuid4().hex}"
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)

    async def test_tenant_and_first_owner_are_created_atomically(self):
        async with await AsyncConnection.connect(DSN, row_factory=dict_row) as conn:
            user_id = await create_tenant_with_owner(
                conn,
                tenant_id=self.tenant_id,
                display_name="Atomic Tenant",
                owner_email=f"owner-{uuid4().hex}@example.com",
                owner_display_name="Tenant Owner",
                owner_password="correct horse battery staple",
            )
            tenant = await (await conn.execute(
                "SELECT status FROM platform_tenants WHERE tenant_id=%s",
                (self.tenant_id,),
            )).fetchone()
            membership = await (await conn.execute(
                """SELECT status FROM platform_tenant_memberships
                   WHERE tenant_id=%s AND user_id=%s""",
                (self.tenant_id, user_id),
            )).fetchone()
            binding = await (await conn.execute(
                """SELECT role_id FROM platform_role_bindings
                   WHERE tenant_id=%s AND subject_id=%s
                     AND resource_type='tenant' AND resource_id=%s
                     AND revoked_at IS NULL""",
                (self.tenant_id, user_id, self.tenant_id),
            )).fetchone()
            audit = await (await conn.execute(
                """SELECT action, outcome FROM platform_identity_audit
                   WHERE tenant_id=%s AND subject_id=%s""",
                (self.tenant_id, user_id),
            )).fetchone()

        self.assertEqual(tenant["status"], "active")
        self.assertEqual(membership["status"], "active")
        self.assertEqual(binding["role_id"], "tenant_admin")
        self.assertEqual(
            (audit["action"], audit["outcome"]),
            ("tenant.owner_bootstrapped", "succeeded"),
        )

    async def test_owner_creation_failure_rolls_back_new_tenant(self):
        duplicate_email = f"duplicate-{uuid4().hex}@example.com"
        async with await AsyncConnection.connect(DSN, row_factory=dict_row) as conn:
            await conn.execute(
                """INSERT INTO platform_users
                   (user_id, email, email_normalized, display_name, password_hash)
                   VALUES (%s, %s, %s, 'Existing User', 'integration-hash')""",
                (uuid4(), duplicate_email, duplicate_email),
            )
            await conn.commit()

        from psycopg.errors import UniqueViolation
        with self.assertRaises(UniqueViolation):
            async with await AsyncConnection.connect(DSN, row_factory=dict_row) as conn:
                await create_tenant_with_owner(
                    conn,
                    tenant_id=self.tenant_id,
                    display_name="Must Roll Back",
                    owner_email=duplicate_email,
                    owner_display_name="Duplicate Owner",
                    owner_password="correct horse battery staple",
                )
                await conn.commit()

        async with await AsyncConnection.connect(DSN, row_factory=dict_row) as conn:
            tenant = await (await conn.execute(
                "SELECT 1 FROM platform_tenants WHERE tenant_id=%s",
                (self.tenant_id,),
            )).fetchone()
        self.assertIsNone(tenant)


if __name__ == "__main__":
    unittest.main()
