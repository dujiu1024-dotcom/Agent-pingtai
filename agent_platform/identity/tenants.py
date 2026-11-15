"""Atomic tenant creation with first-owner bootstrap."""
from __future__ import annotations

from agent_platform.identity.bootstrap import bootstrap_tenant_owner


async def create_tenant_with_owner(
    connection,
    *,
    tenant_id: str,
    display_name: str,
    owner_email: str,
    owner_display_name: str,
    owner_password: str,
) -> str:
    """Create a tenant and its first owner in one database transaction.

    Tenant creation and Owner bootstrap intentionally share the same connection
    and outer transaction. Any bootstrap or audit failure rolls back the tenant.
    """
    clean_tenant_id = tenant_id.strip()
    clean_display_name = display_name.strip()
    if not clean_tenant_id or not clean_display_name:
        raise ValueError("tenant_id and display_name are required")

    async with connection.transaction():
        await connection.execute(
            """INSERT INTO platform_tenants (tenant_id, display_name, status)
               VALUES (%s, %s, 'active')""",
            (clean_tenant_id, clean_display_name),
        )
        user_id = await bootstrap_tenant_owner(
            connection,
            tenant_id=clean_tenant_id,
            email=owner_email,
            display_name=owner_display_name,
            password=owner_password,
        )
    return user_id
