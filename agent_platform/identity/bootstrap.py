"""One-time bootstrap for the first tenant owner.

Run with AGENT_PLATFORM_DSN, AGENT_PLATFORM_TENANT_ID and
AGENT_PLATFORM_OWNER_EMAIL set. The password is read interactively.
"""

from __future__ import annotations

import asyncio
import getpass
import os
from uuid import uuid4

from agent_platform.identity.security import PasswordHasher
from agent_platform.identity.service import IdentityService

DEFAULT_PERMISSIONS = {
    "tenant.manage": "Manage tenant settings",
    "user.manage": "Manage tenant users and invitations",
    "identity.role.manage": "Grant and revoke role bindings",
    "project.read": "Read projects",
    "project.write": "Modify projects",
    "agent.run": "Run agents",
    "agent.manage": "Manage agent definitions",
    "tool.execute": "Execute registered tools",
    "knowledge.read": "Retrieve authorized knowledge",
    "knowledge.manage": "Manage knowledge sources",
    "audit.read": "Read audit records",
}

DEFAULT_ROLE_PERMISSIONS = {
    "tenant_admin": tuple(DEFAULT_PERMISSIONS),
    "member": ("project.read", "project.write", "agent.run", "knowledge.read"),
    "viewer": ("project.read", "knowledge.read"),
}


class BootstrapError(RuntimeError):
    """Raised when a tenant cannot be safely bootstrapped."""


async def bootstrap_tenant_owner(
    connection,
    *,
    tenant_id: str,
    email: str,
    display_name: str,
    password: str,
) -> str:
    """Create the first tenant owner and seed tenant roles atomically."""
    normalized_email = IdentityService.normalize_email(email)
    if not tenant_id.strip() or not display_name.strip():
        raise ValueError("tenant_id and display_name are required")
    password_hash = PasswordHasher.hash(password)
    user_id = uuid4()
    binding_id = uuid4()

    async with connection.transaction():
        cursor = await connection.execute(
            "SELECT status FROM platform_tenants WHERE tenant_id=%s FOR UPDATE",
            (tenant_id,),
        )
        tenant = await cursor.fetchone()
        if tenant is None or tenant["status"] != "active":
            raise BootstrapError("tenant does not exist or is not active")

        cursor = await connection.execute(
            "SELECT 1 FROM platform_tenant_memberships WHERE tenant_id=%s LIMIT 1",
            (tenant_id,),
        )
        if await cursor.fetchone() is not None:
            raise BootstrapError("tenant already has membership records; bootstrap refused")

        await connection.execute(
            """INSERT INTO platform_users
               (user_id, email, email_normalized, display_name, password_hash)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, email.strip(), normalized_email, display_name.strip(), password_hash),
        )
        await connection.execute(
            """INSERT INTO platform_tenant_memberships
               (tenant_id, user_id, status, joined_at)
               VALUES (%s, %s, 'active', now())""",
            (tenant_id, user_id),
        )

        for permission_key, description in DEFAULT_PERMISSIONS.items():
            await connection.execute(
                """INSERT INTO platform_permissions (permission_key, description)
                   VALUES (%s, %s) ON CONFLICT (permission_key) DO NOTHING""",
                (permission_key, description),
            )

        for role_id in DEFAULT_ROLE_PERMISSIONS:
            await connection.execute(
                """INSERT INTO platform_roles
                   (tenant_id, role_id, name, description, is_system)
                   VALUES (%s, %s, %s, %s, true)
                   ON CONFLICT (tenant_id, role_id) DO NOTHING""",
                (tenant_id, role_id, role_id.replace("_", " ").title(),
                 "Built-in platform role"),
            )
            for permission_key in DEFAULT_ROLE_PERMISSIONS[role_id]:
                await connection.execute(
                    """INSERT INTO platform_role_permissions
                       (tenant_id, role_id, permission_key)
                       VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                    (tenant_id, role_id, permission_key),
                )

        await connection.execute(
            """INSERT INTO platform_role_bindings
               (binding_id, tenant_id, subject_type, subject_id,
                resource_type, resource_id, role_id, created_by)
               VALUES (%s, %s, 'user', %s, 'tenant', %s, 'tenant_admin', 'bootstrap')""",
            (binding_id, tenant_id, str(user_id), tenant_id),
        )
        await connection.execute(
            """INSERT INTO platform_identity_audit
               (tenant_id, actor_id, action, subject_id, outcome, detail)
               VALUES (%s, 'bootstrap', 'tenant.owner_bootstrapped', %s,
                       'succeeded', jsonb_build_object('email', %s))""",
            (tenant_id, str(user_id), normalized_email),
        )

    return str(user_id)


async def _main() -> None:
    dsn = os.environ.get("AGENT_PLATFORM_DSN", "").strip()
    tenant_id = os.environ.get("AGENT_PLATFORM_TENANT_ID", "").strip()
    email = os.environ.get("AGENT_PLATFORM_OWNER_EMAIL", "").strip()
    display_name = os.environ.get("AGENT_PLATFORM_OWNER_NAME", "").strip() or email
    if not dsn or not tenant_id or not email:
        raise SystemExit(
            "Set AGENT_PLATFORM_DSN, AGENT_PLATFORM_TENANT_ID and "
            "AGENT_PLATFORM_OWNER_EMAIL before running bootstrap."
        )
    password = getpass.getpass("New tenant owner password (min 12 chars): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    try:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise SystemExit("Install requirements-platform.txt first.") from exc

    async with await AsyncConnection.connect(dsn, row_factory=dict_row) as connection:
        user_id = await bootstrap_tenant_owner(
            connection, tenant_id=tenant_id, email=email,
            display_name=display_name, password=password,
        )
    print("Tenant owner created:", user_id)


if __name__ == "__main__":
    asyncio.run(_main())
