"""PostgreSQL identity repository with tenant-scoped session and RBAC queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from agent_platform.identity.models import SessionRecord, UserRecord

ConnectionFactory = Callable[[], Any]

class PostgresIdentityRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> "PostgresIdentityRepository":
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("Install psycopg[binary,pool] for identity storage") from exc
            return await AsyncConnection.connect(dsn, row_factory=dict_row)
        return cls(connect)

    async def get_user_by_email(self, normalized_email: str) -> UserRecord | None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute("SELECT user_id, email, display_name, password_hash, status FROM platform_users WHERE email_normalized=%s", (normalized_email,))
            row = await cursor.fetchone()
        if row is None:
            return None
        return UserRecord(user_id=str(row["user_id"]), email=row["email"], display_name=row["display_name"], password_hash=row["password_hash"], status=row["status"])

    async def is_active_member(self, *, tenant_id: str, user_id: str) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute("SELECT 1 FROM platform_tenant_memberships WHERE tenant_id=%s AND user_id=%s AND status='active'", (tenant_id, user_id))
            return await cursor.fetchone() is not None

    async def create_session(self, *, session_id: str, tenant_id: str, user_id: str, token_hash: str, expires_at: datetime) -> None:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute("""INSERT INTO platform_sessions (session_id, tenant_id, user_id, token_hash, expires_at)
                    SELECT %s, %s, %s, %s, %s WHERE EXISTS (
                      SELECT 1 FROM platform_tenant_memberships WHERE tenant_id=%s AND user_id=%s AND status='active'
                    ) RETURNING session_id""", (session_id, tenant_id, user_id, token_hash, expires_at, tenant_id, user_id))
                if await cursor.fetchone() is None:
                    raise PermissionError("active tenant membership required")

    async def get_session_by_token_hash(self, token_hash: str) -> SessionRecord | None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute("""SELECT s.session_id, s.tenant_id, s.user_id, s.expires_at, s.revoked_at,
                    u.email, u.display_name, u.status AS user_status, m.status AS membership_status
                FROM platform_sessions s JOIN platform_users u ON u.user_id=s.user_id
                JOIN platform_tenant_memberships m ON m.tenant_id=s.tenant_id AND m.user_id=s.user_id
                WHERE s.token_hash=%s""", (token_hash,))
            row = await cursor.fetchone()
        if row is None:
            return None
        return SessionRecord(session_id=str(row["session_id"]), tenant_id=row["tenant_id"], user_id=str(row["user_id"]), email=row["email"], display_name=row["display_name"], expires_at=row["expires_at"], revoked_at=row["revoked_at"], user_status=row["user_status"], membership_status=row["membership_status"])

    async def touch_session(self, *, session_id: str, tenant_id: str) -> None:
        connection = await self._connection_factory()
        async with connection:
            await connection.execute("UPDATE platform_sessions SET last_seen_at=now() WHERE session_id=%s AND tenant_id=%s AND revoked_at IS NULL AND expires_at > now()", (session_id, tenant_id))

    async def revoke_session(self, *, session_id: str, tenant_id: str, user_id: str, revoked_at: datetime) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute("UPDATE platform_sessions SET revoked_at=%s WHERE session_id=%s AND tenant_id=%s AND user_id=%s AND revoked_at IS NULL RETURNING session_id", (revoked_at, session_id, tenant_id, user_id))
            return await cursor.fetchone() is not None

    async def has_permission(self, *, tenant_id: str, user_id: str, resource_type: str, resource_id: str, permission_key: str) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute("""SELECT EXISTS (
                SELECT 1 FROM platform_role_bindings b
                JOIN platform_role_permissions rp ON rp.tenant_id=b.tenant_id AND rp.role_id=b.role_id
                WHERE b.tenant_id=%s AND b.revoked_at IS NULL AND rp.permission_key=%s
                  AND ((b.subject_type='user' AND b.subject_id=%s)
                    OR (b.subject_type='team' AND b.subject_id IN (
                      SELECT team_id FROM platform_team_memberships WHERE tenant_id=%s AND user_id=%s AND status='active'
                    )))
                  AND ((b.resource_type=%s AND b.resource_id=%s)
                    OR (b.resource_type='tenant' AND b.resource_id=%s))
            ) AS allowed""", (tenant_id, permission_key, user_id, tenant_id, user_id, resource_type, resource_id, tenant_id))
            row = await cursor.fetchone()
            return bool(row["allowed"])
