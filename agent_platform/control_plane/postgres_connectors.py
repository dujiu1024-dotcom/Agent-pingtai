"""PostgreSQL repository for tenant-scoped external data connections."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from agent_platform.control_plane.data_connectors import DataConnection

ConnectionFactory = Callable[[], Any]


class PostgresConnectorRepository:
    def __init__(self, *, tenant_id: str, connection_factory: ConnectionFactory) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        self.tenant_id = tenant_id
        self._connection_factory = connection_factory

    async def create(self, connection: DataConnection, *, created_by: str) -> None:
        if connection.tenant_id != self.tenant_id:
            raise PermissionError("connector tenant mismatch")
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        db = await self._connection_factory()
        async with db:
            await db.execute(
                """INSERT INTO platform_data_connections
                   (tenant_id,connection_id,provider,display_name,secret_ref,scopes,project_id,
                    status,cursor,created_by,version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (self.tenant_id, connection.connection_id, connection.provider,
                 connection.display_name or connection.connection_id, connection.secret_ref, Jsonb(list(connection.scopes)),
                 connection.project_id, connection.status, Jsonb(dict(connection.cursor)) if connection.cursor is not None else None,
                 created_by, connection.version),
            )

    async def get(self, tenant_id: str, connection_id: str) -> DataConnection | None:
        if tenant_id != self.tenant_id:
            return None
        db = await self._connection_factory()
        async with db:
            cursor = await db.execute(
                """SELECT tenant_id,connection_id,provider,display_name,secret_ref,scopes,status,
                          cursor,version,last_synced_at,revoked_at,last_error,project_id
                   FROM platform_data_connections
                   WHERE tenant_id=%s AND connection_id=%s""",
                (self.tenant_id, connection_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return DataConnection(
            tenant_id=row["tenant_id"], connection_id=row["connection_id"],
            provider=row["provider"], secret_ref=row["secret_ref"],
            scopes=tuple(row["scopes"] or ()), display_name=row["display_name"], status=row["status"],
            cursor=dict(row["cursor"]) if row["cursor"] is not None else None,
            version=row["version"], last_synced_at=row["last_synced_at"],
            revoked_at=row["revoked_at"], last_error=row["last_error"], project_id=row["project_id"],
        )

    async def save_cursor(
        self, *, tenant_id: str, connection_id: str, expected_version: int,
        cursor: Mapping[str, Any] | None,
    ) -> bool:
        if tenant_id != self.tenant_id:
            raise PermissionError("connector tenant mismatch")
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        db = await self._connection_factory()
        async with db:
            result = await db.execute(
                """UPDATE platform_data_connections
                   SET cursor=%s,version=version+1,last_synced_at=now(),last_error=NULL
                   WHERE tenant_id=%s AND connection_id=%s
                     AND status='active' AND version=%s
                   RETURNING version""",
                (Jsonb(dict(cursor)) if cursor is not None else None,
                 self.tenant_id, connection_id, expected_version),
            )
            return await result.fetchone() is not None

    async def mark_error(self, *, tenant_id: str, connection_id: str, error: str) -> None:
        if tenant_id != self.tenant_id:
            raise PermissionError("connector tenant mismatch")
        db = await self._connection_factory()
        async with db:
            await db.execute(
                """UPDATE platform_data_connections SET last_error=%s
                   WHERE tenant_id=%s AND connection_id=%s AND status IN ('active','revoked')""",
                (error[:500], self.tenant_id, connection_id),
            )

    async def revoke(self, *, tenant_id: str, connection_id: str) -> DataConnection | None:
        if tenant_id != self.tenant_id:
            raise PermissionError("connector tenant mismatch")
        db = await self._connection_factory()
        async with db:
            async with db.transaction():
                result = await db.execute(
                    """UPDATE platform_data_connections
                       SET status='revoked',revoked_at=now(),version=version+1
                       WHERE tenant_id=%s AND connection_id=%s AND status='active'
                       RETURNING tenant_id,connection_id,provider,display_name,secret_ref,scopes,
                                 status,cursor,version,last_synced_at,revoked_at,last_error,project_id""",
                    (self.tenant_id, connection_id),
                )
                row = await result.fetchone()
        if row is None:
            return None
        return DataConnection(
            tenant_id=row["tenant_id"], connection_id=row["connection_id"],
            provider=row["provider"], secret_ref=row["secret_ref"],
            scopes=tuple(row["scopes"] or ()), display_name=row["display_name"],
            status=row["status"],
            cursor=dict(row["cursor"]) if row["cursor"] is not None else None,
            version=row["version"], last_synced_at=row["last_synced_at"],
            revoked_at=row["revoked_at"], last_error=row["last_error"],
        )
