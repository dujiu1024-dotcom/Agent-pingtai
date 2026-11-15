"""PostgreSQL authorization revalidation for machine principals."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")

if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from agent_platform.identity.models import ResourceRef
    from agent_platform.identity.service_accounts import (
        PostgresServiceIdentityRepository,
        ServiceIdentityError,
        ServiceIdentityService,
        ServicePrincipal,
    )


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class ServiceIdentityAuthorizationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"machine-auth-{uuid4().hex}"
        self.service_account_id = f"svc-{uuid4().hex}"
        self.agent_id = f"agent-{uuid4().hex}"
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            migration_dir = Path("agent_platform/storage/postgres")
            for migration_path in sorted(migration_dir.glob("*.sql")):
                await conn.execute(migration_path.read_text(), prepare=False)

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.connect = connect
        async with await self.connect() as conn:
            await conn.execute(
                "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                (self.tenant_id, "Machine Authorization Integration"),
            )
            await conn.execute(
                """INSERT INTO platform_service_accounts
                   (tenant_id, service_account_id, name)
                   VALUES (%s, %s, 'Integration Worker')""",
                (self.tenant_id, self.service_account_id),
            )
            await conn.execute(
                """INSERT INTO platform_agents
                   (tenant_id, agent_id, display_name, role_definition)
                   VALUES (%s, %s, 'Integration Agent', 'test agent')""",
                (self.tenant_id, self.agent_id),
            )
            await conn.execute(
                """INSERT INTO platform_agent_identities
                   (tenant_id, agent_id, service_account_id)
                   VALUES (%s, %s, %s)""",
                (self.tenant_id, self.agent_id, self.service_account_id),
            )
            await conn.execute(
                """INSERT INTO platform_permissions (permission_key, description)
                   VALUES ('agent.run', 'Run agents') ON CONFLICT DO NOTHING"""
            )
            await conn.execute(
                """INSERT INTO platform_roles (tenant_id, role_id, name)
                   VALUES (%s, 'machine-runner', 'Machine Runner')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_permissions
                   (tenant_id, role_id, permission_key)
                   VALUES (%s, 'machine-runner', 'agent.run')""",
                (self.tenant_id,),
            )
            await conn.execute(
                """INSERT INTO platform_role_bindings
                   (binding_id, tenant_id, subject_type, subject_id,
                    resource_type, resource_id, role_id, created_by)
                   VALUES (%s, %s, 'agent', %s, 'agent', %s,
                           'machine-runner', 'integration')""",
                (uuid4(), self.tenant_id, self.agent_id, self.agent_id),
            )
            await conn.commit()

        self.service = ServiceIdentityService(
            PostgresServiceIdentityRepository(self.connect)
        )
        issued = await self.service.issue_credential(
            tenant_id=self.tenant_id,
            service_account_id=self.service_account_id,
            agent_id=self.agent_id,
            name="authorization integration credential",
            scopes=("agent.run",),
        )
        self.principal = await self.service.authenticate(issued.token)

    async def test_authorization_revalidates_agent_status_after_authentication(self):
        await self.service.authorize(
            principal=self.principal,
            resource=ResourceRef("agent", self.agent_id),
            permission_key="agent.run",
        )
        async with await self.connect() as conn:
            await conn.execute(
                """UPDATE platform_agent_identities SET status='disabled'
                   WHERE tenant_id=%s AND agent_id=%s""",
                (self.tenant_id, self.agent_id),
            )
            await conn.commit()

        with self.assertRaises(ServiceIdentityError):
            await self.service.authorize(
                principal=self.principal,
                resource=ResourceRef("agent", self.agent_id),
                permission_key="agent.run",
            )

    async def test_authorization_rejects_principal_agent_mismatch(self):
        forged = ServicePrincipal(
            tenant_id=self.principal.tenant_id,
            service_account_id=self.principal.service_account_id,
            agent_id="another-agent",
            credential_id=self.principal.credential_id,
            scopes=self.principal.scopes,
        )
        with self.assertRaises(ServiceIdentityError):
            await self.service.authorize(
                principal=forged,
                resource=ResourceRef("agent", self.agent_id),
                permission_key="agent.run",
            )


if __name__ == "__main__":
    unittest.main()
