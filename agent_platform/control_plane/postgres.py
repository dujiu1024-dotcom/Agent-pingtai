"""PostgreSQL adapters for the registry and session/asset metadata ports."""
from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from agent_platform.control_plane.registry import (
    RegistryConflict,
    RegistryNotFound,
    RegistryVersion,
)
from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    ConversationMessage,
    ConversationSession,
    FileAsset,
    FileAssetVersion,
    MessageRole,
    MessageTokenUsage,
    SessionIsolationScope,
    SessionError,
    SessionNotFound,
)

ConnectionFactory = Callable[[], Any]


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
    return Jsonb(value)


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("identifier must be a UUID") from exc


class PostgresRegistryRepository:
    """Tenant-bound implementation of RegistryRepository."""
    def __init__(self, *, tenant_id: str, connection_factory: ConnectionFactory) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        self.tenant_id = tenant_id
        self._connection_factory = connection_factory

    async def get(self, tenant_id: str, kind: str, item_id: str, version: int) -> RegistryVersion | None:
        if tenant_id != self.tenant_id:
            return None
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_registry_versions
                   WHERE tenant_id=%s AND kind=%s AND item_id=%s AND version=%s""",
                (self.tenant_id, kind, item_id, version),
            )
            row = await cursor.fetchone()
        return self._to_version(row) if row else None

    async def list_versions(self, tenant_id: str, kind: str, item_id: str) -> tuple[RegistryVersion, ...]:
        if tenant_id != self.tenant_id:
            return ()
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_registry_versions
                   WHERE tenant_id=%s AND kind=%s AND item_id=%s ORDER BY version""",
                (self.tenant_id, kind, item_id),
            )
            rows = await cursor.fetchall()
        return tuple(self._to_version(row) for row in rows)

    async def list_items(self, tenant_id: str, kind: str, *, limit: int = 100) -> tuple[RegistryVersion, ...]:
        if tenant_id != self.tenant_id:
            return ()
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT DISTINCT ON (item_id) *
                   FROM platform_registry_versions
                   WHERE tenant_id=%s AND kind=%s
                   ORDER BY item_id, version DESC
                   LIMIT %s""",
                (self.tenant_id, kind, limit),
            )
            rows = await cursor.fetchall()
        return tuple(self._to_version(row) for row in rows)

    async def put(self, item: RegistryVersion, *, expected_latest: int | None) -> None:
        if item.tenant_id != self.tenant_id:
            raise PermissionError("registry tenant mismatch")
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{self.tenant_id}:{item.kind}:{item.item_id}",),
                )
                cursor = await connection.execute(
                    """SELECT max(version) AS latest FROM platform_registry_versions
                       WHERE tenant_id=%s AND kind=%s AND item_id=%s""",
                    (self.tenant_id, item.kind, item.item_id),
                )
                row = await cursor.fetchone()
                latest = row["latest"] if row else None
                if latest != expected_latest:
                    raise RegistryConflict(f"latest version changed: expected {expected_latest}, got {latest}")
                await connection.execute(
                    """INSERT INTO platform_registry_versions
                       (tenant_id,kind,item_id,version,definition,status,created_by,created_at,published_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (self.tenant_id, item.kind, item.item_id, item.version,
                     _jsonb(dict(item.definition)), item.status, item.created_by,
                     item.created_at, item.published_at),
                )

    async def set_active(self, tenant_id: str, kind: str, item_id: str, version: int) -> None:
        if tenant_id != self.tenant_id:
            raise PermissionError("registry tenant mismatch")
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """INSERT INTO platform_registry_active
                       (tenant_id,kind,item_id,active_version,updated_at)
                       SELECT tenant_id,kind,item_id,version,now()
                       FROM platform_registry_versions
                       WHERE tenant_id=%s AND kind=%s AND item_id=%s
                         AND version=%s AND status='published'
                       ON CONFLICT (tenant_id,kind,item_id) DO UPDATE
                         SET active_version=EXCLUDED.active_version, updated_at=now()
                       RETURNING active_version""",
                    (self.tenant_id, kind, item_id, version),
                )
                if await cursor.fetchone() is None:
                    raise RegistryNotFound("published registry version not found")

    async def clear_active(self, tenant_id: str, kind: str, item_id: str) -> None:
        if tenant_id != self.tenant_id:
            raise PermissionError("registry tenant mismatch")
        connection = await self._connection_factory()
        async with connection:
            await connection.execute(
                """DELETE FROM platform_registry_active
                   WHERE tenant_id=%s AND kind=%s AND item_id=%s""",
                (self.tenant_id, kind, item_id),
            )

    async def get_active(self, tenant_id: str, kind: str, item_id: str) -> RegistryVersion | None:
        if tenant_id != self.tenant_id:
            return None
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT v.* FROM platform_registry_active a
                   JOIN platform_registry_versions v
                     ON v.tenant_id=a.tenant_id AND v.kind=a.kind
                    AND v.item_id=a.item_id AND v.version=a.active_version
                   WHERE a.tenant_id=%s AND a.kind=%s AND a.item_id=%s""",
                (self.tenant_id, kind, item_id),
            )
            row = await cursor.fetchone()
        return self._to_version(row) if row else None

    @staticmethod
    def _to_version(row: Any) -> RegistryVersion:
        return RegistryVersion(
            tenant_id=row["tenant_id"], kind=row["kind"], item_id=row["item_id"],
            version=row["version"], definition=dict(row["definition"]),
            status=row["status"], created_by=row["created_by"],
            created_at=row["created_at"], published_at=row["published_at"],
        )


class PostgresSessionRepository:
    """Tenant-bound metadata repository; binary content stays in ObjectStorage."""
    def __init__(self, *, tenant_id: str, connection_factory: ConnectionFactory) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        self.tenant_id = tenant_id
        self._connection_factory = connection_factory

    async def create_session(self, session: ConversationSession) -> None:
        self._check_tenant(session.tenant_id)
        connection = await self._connection_factory()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_conversation_sessions
                   (tenant_id,session_id,owner_id,project_id,agent_id,isolation_scope,title,status,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (self.tenant_id, _uuid(session.session_id), session.owner_id,
                 session.project_id, session.agent_id, session.isolation_scope.value,
                 session.title, session.status, session.created_at, session.updated_at),
            )

    async def get_session(self, tenant_id: str, session_id: str) -> ConversationSession | None:
        if tenant_id != self.tenant_id:
            return None
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                "SELECT * FROM platform_conversation_sessions WHERE tenant_id=%s AND session_id=%s",
                (self.tenant_id, _uuid(session_id)),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ConversationSession(
            tenant_id=row["tenant_id"], session_id=str(row["session_id"]),
            owner_id=row["owner_id"], title=row["title"], project_id=row["project_id"],
            status=row["status"], created_at=row["created_at"], updated_at=row["updated_at"],
            agent_id=row.get("agent_id"), isolation_scope=SessionIsolationScope(
                row.get("isolation_scope") or "user",
            ),
        )

    async def append_message(self, message: ConversationMessage) -> None:
        self._check_tenant(message.tenant_id)
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT 1 FROM platform_conversation_sessions
                       WHERE tenant_id=%s AND session_id=%s AND status='active' FOR UPDATE""",
                    (self.tenant_id, _uuid(message.session_id)),
                )
                if await cursor.fetchone() is None:
                    raise SessionNotFound("session not found or inactive")
                await connection.execute(
                    """INSERT INTO platform_messages
                       (tenant_id,message_id,session_id,role,content,actor_id,task_id,run_id,
                        asset_ids,metadata,token_usage,created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (self.tenant_id, _uuid(message.message_id), _uuid(message.session_id),
                     message.role.value, message.content, message.actor_id, message.task_id,
                     message.run_id, _jsonb(list(message.asset_ids)),
                     _jsonb(dict(message.metadata)),
                     _jsonb({
                         "prompt_tokens": message.token_usage.prompt_tokens,
                         "completion_tokens": message.token_usage.completion_tokens,
                         "total_tokens": message.token_usage.total_tokens,
                         "model": message.token_usage.model,
                     }) if message.token_usage is not None else _jsonb({}),
                     message.created_at),
                )
                await connection.execute(
                    "UPDATE platform_conversation_sessions SET updated_at=now() WHERE tenant_id=%s AND session_id=%s",
                    (self.tenant_id, _uuid(message.session_id)),
                )

    async def list_messages(self, tenant_id: str, session_id: str, limit: int = 100) -> tuple[ConversationMessage, ...]:
        if tenant_id != self.tenant_id:
            raise SessionNotFound("session not found")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM (
                     SELECT * FROM platform_messages
                     WHERE tenant_id=%s AND session_id=%s
                     ORDER BY created_at DESC, message_id DESC LIMIT %s
                   ) recent ORDER BY created_at, message_id""",
                (self.tenant_id, _uuid(session_id), limit),
            )
            rows = await cursor.fetchall()
        return tuple(ConversationMessage(
            tenant_id=row["tenant_id"], message_id=str(row["message_id"]),
            session_id=str(row["session_id"]), role=MessageRole(row["role"]),
            content=row["content"], actor_id=row["actor_id"], run_id=row["run_id"],
            task_id=row["task_id"], asset_ids=tuple(row["asset_ids"] or ()),
            metadata=dict(row["metadata"] or {}), created_at=row["created_at"],
            token_usage=MessageTokenUsage(**dict(row["token_usage"]))
            if row.get("token_usage") else None,
        ) for row in rows)

    async def list_sessions(self, tenant_id: str, owner_id: str, limit: int = 100, project_id: str | None = None) -> tuple[ConversationSession, ...]:
        if tenant_id != self.tenant_id:
            raise SessionNotFound("session not found")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        query = """SELECT * FROM platform_conversation_sessions
                   WHERE tenant_id=%s AND owner_id=%s AND status <> 'deleted'"""
        params: tuple[object, ...] = (self.tenant_id, owner_id)
        if project_id is not None:
            query += " AND project_id=%s"
            params += (project_id,)
        query += " ORDER BY updated_at DESC,session_id LIMIT %s"
        params += (limit,)
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(query, params)
            rows = await cursor.fetchall()
        return tuple(ConversationSession(
            tenant_id=row["tenant_id"], session_id=str(row["session_id"]),
            owner_id=row["owner_id"], title=row["title"], project_id=row["project_id"],
            status=row["status"], created_at=row["created_at"], updated_at=row["updated_at"],
            agent_id=row.get("agent_id"), isolation_scope=SessionIsolationScope(
                row.get("isolation_scope") or "user",
            ),
        ) for row in rows)

    async def create_asset(self, asset: FileAsset) -> None:
        self._check_tenant(asset.tenant_id)
        connection = await self._connection_factory()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_file_assets
                   (tenant_id,asset_id,owner_id,project_id,session_id,object_key,filename,
                    media_type,size_bytes,sha256,status,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (self.tenant_id, _uuid(asset.asset_id), asset.owner_id, asset.project_id,
                 _uuid(asset.session_id) if asset.session_id else None, asset.object_key,
                 asset.filename, asset.media_type, asset.size_bytes, asset.sha256,
                 asset.status.value, asset.created_at),
            )
            await connection.execute(
                """INSERT INTO platform_file_asset_versions
                   (tenant_id,asset_id,version,object_key,filename,media_type,size_bytes,sha256,status,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (self.tenant_id, _uuid(asset.asset_id), asset.version, asset.object_key,
                 asset.filename, asset.media_type, asset.size_bytes, asset.sha256,
                 asset.status.value, asset.created_at),
            )

    async def get_asset(self, tenant_id: str, asset_id: str) -> FileAsset | None:
        if tenant_id != self.tenant_id:
            return None
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                "SELECT * FROM platform_file_assets WHERE tenant_id=%s AND asset_id=%s",
                (self.tenant_id, _uuid(asset_id)),
            )
            row = await cursor.fetchone()
        return self._to_asset(row) if row else None

    async def list_asset_versions(self, tenant_id: str, asset_id: str) -> tuple[FileAssetVersion, ...]:
        if tenant_id != self.tenant_id:
            raise SessionNotFound("file not found")
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_file_asset_versions
                   WHERE tenant_id=%s AND asset_id=%s ORDER BY version""",
                (self.tenant_id, _uuid(asset_id)),
            )
            rows = await cursor.fetchall()
        return tuple(self._to_asset_version(row) for row in rows)

    async def get_asset_version(self, tenant_id: str, asset_id: str, version: int) -> FileAssetVersion | None:
        if tenant_id != self.tenant_id:
            return None
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_file_asset_versions
                   WHERE tenant_id=%s AND asset_id=%s AND version=%s""",
                (self.tenant_id, _uuid(asset_id), version),
            )
            row = await cursor.fetchone()
        return self._to_asset_version(row) if row else None

    async def create_asset_version(self, version: FileAssetVersion, *, expected_version: int) -> None:
        self._check_tenant(version.tenant_id)
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT current_version,status FROM platform_file_assets
                       WHERE tenant_id=%s AND asset_id=%s FOR UPDATE""",
                    (self.tenant_id, _uuid(version.asset_id)),
                )
                row = await cursor.fetchone()
                if row is None or row["status"] != AssetStatus.AVAILABLE.value:
                    raise SessionNotFound("asset not found or unavailable")
                if row["current_version"] != expected_version or version.version != expected_version + 1:
                    raise SessionError("file asset version changed; retry from the latest version")
                await connection.execute(
                    """INSERT INTO platform_file_asset_versions
                       (tenant_id,asset_id,version,object_key,filename,media_type,size_bytes,sha256,status,created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (self.tenant_id, _uuid(version.asset_id), version.version,
                     version.object_key, version.filename, version.media_type,
                     version.size_bytes, version.sha256, version.status.value,
                     version.created_at),
                )
                await connection.execute(
                    """UPDATE platform_file_assets SET current_version=%s,object_key=%s,
                       filename=%s,media_type=%s,size_bytes=%s,sha256=%s,status=%s,created_at=%s
                       WHERE tenant_id=%s AND asset_id=%s""",
                    (version.version, version.object_key, version.filename, version.media_type,
                     version.size_bytes, version.sha256, version.status.value, version.created_at,
                     self.tenant_id, _uuid(version.asset_id)),
                )

    async def list_assets(self, tenant_id: str, owner_id: str, session_id: str | None = None, limit: int = 100, project_id: str | None = None) -> tuple[FileAsset, ...]:
        if tenant_id != self.tenant_id:
            raise SessionNotFound("file not found")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        query = """SELECT * FROM platform_file_assets
                   WHERE tenant_id=%s AND owner_id=%s AND status <> 'deleted'"""
        params: tuple[object, ...] = (self.tenant_id, owner_id)
        if session_id:
            query += " AND session_id=%s"
            params += (_uuid(session_id),)
        if project_id is not None:
            query += " AND project_id=%s"
            params += (project_id,)
        query += " ORDER BY created_at DESC,asset_id LIMIT %s"
        params += (limit,)
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(query, params)
            rows = await cursor.fetchall()
        return tuple(self._to_asset(row) for row in rows)

    async def set_asset_status(self, tenant_id: str, asset_id: str, status: AssetStatus) -> None:
        if tenant_id != self.tenant_id:
            raise PermissionError("asset tenant mismatch")
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_file_assets SET status=%s
                   WHERE tenant_id=%s AND asset_id=%s RETURNING current_version""",
                (status.value, self.tenant_id, _uuid(asset_id)),
            )
            row = await cursor.fetchone()
            if row is None:
                raise SessionNotFound("asset not found")
            if status is AssetStatus.DELETED:
                await connection.execute(
                    """UPDATE platform_file_asset_versions SET status=%s
                       WHERE tenant_id=%s AND asset_id=%s""",
                    (status.value, self.tenant_id, _uuid(asset_id)),
                )
            else:
                await connection.execute(
                    """UPDATE platform_file_asset_versions SET status=%s
                       WHERE tenant_id=%s AND asset_id=%s AND version=%s""",
                    (status.value, self.tenant_id, _uuid(asset_id), row["current_version"]),
                )


    async def set_asset_status_if_not_deleted(
        self, tenant_id: str, asset_id: str, status: AssetStatus
    ) -> bool:
        if tenant_id != self.tenant_id:
            raise PermissionError("asset tenant mismatch")
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_file_assets SET status=%s
                   WHERE tenant_id=%s AND asset_id=%s AND status <> 'deleted'
                   RETURNING current_version""",
                (status.value, self.tenant_id, _uuid(asset_id)),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            await connection.execute(
                """UPDATE platform_file_asset_versions SET status=%s
                   WHERE tenant_id=%s AND asset_id=%s AND version=%s""",
                (status.value, self.tenant_id, _uuid(asset_id), row["current_version"]),
            )
            return True

    @staticmethod
    def _to_asset_version(row: Any) -> FileAssetVersion:
        return FileAssetVersion(
            tenant_id=row["tenant_id"], asset_id=str(row["asset_id"]),
            version=row["version"], object_key=row["object_key"],
            filename=row["filename"], media_type=row["media_type"],
            size_bytes=row["size_bytes"], sha256=row["sha256"],
            status=AssetStatus(row["status"]), created_at=row["created_at"],
        )


    async def set_asset_status_if_not_deleted(
        self, tenant_id: str, asset_id: str, status: AssetStatus
    ) -> bool:
        if tenant_id != self.tenant_id:
            raise PermissionError("asset tenant mismatch")
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_file_assets SET status=%s
                   WHERE tenant_id=%s AND asset_id=%s AND status <> 'deleted'
                   RETURNING current_version""",
                (status.value, self.tenant_id, _uuid(asset_id)),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            await connection.execute(
                """UPDATE platform_file_asset_versions SET status=%s
                   WHERE tenant_id=%s AND asset_id=%s AND version=%s""",
                (status.value, self.tenant_id, _uuid(asset_id), row["current_version"]),
            )
            return True

    @staticmethod
    def _to_asset_version(row: Any) -> FileAssetVersion:
        return FileAssetVersion(
            tenant_id=row["tenant_id"], asset_id=str(row["asset_id"]),
            version=row["version"], object_key=row["object_key"],
            filename=row["filename"], media_type=row["media_type"],
            size_bytes=row["size_bytes"], sha256=row["sha256"],
            status=AssetStatus(row["status"]), created_at=row["created_at"],
        )

    def _check_tenant(self, tenant_id: str) -> None:
        if tenant_id != self.tenant_id:
            raise PermissionError("session/asset tenant mismatch")

    @staticmethod
    def _to_asset(row: Any) -> FileAsset:
        return FileAsset(
            tenant_id=row["tenant_id"], asset_id=str(row["asset_id"]),
            owner_id=row["owner_id"], object_key=row["object_key"],
            filename=row["filename"], media_type=row["media_type"],
            size_bytes=row["size_bytes"], sha256=row["sha256"],
            status=AssetStatus(row["status"]), project_id=row["project_id"],
            session_id=str(row["session_id"]) if row["session_id"] else None,
            created_at=row["created_at"], version=row.get("current_version", 1),
        )
