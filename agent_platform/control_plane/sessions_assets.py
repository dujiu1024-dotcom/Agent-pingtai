"""Tenant-scoped conversation and file-asset domain services.

Binary payloads live behind ObjectStorage; PostgreSQL repositories own metadata.
This module deliberately does not reuse legacy conversation tables as authority.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol
from uuid import uuid4


class SessionError(ValueError):
    """Invalid session or asset operation."""


class SessionNotFound(SessionError):
    """Resource is not visible to the current tenant/actor."""


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    AGENT = "agent"


class AssetStatus(str, Enum):
    PENDING = "pending"
    SCANNING = "scanning"
    AVAILABLE = "available"
    REJECTED = "rejected"
    DELETED = "deleted"


@dataclass(frozen=True)
class ConversationSession:
    tenant_id: str
    session_id: str
    owner_id: str
    title: str = ""
    project_id: str | None = None
    status: str = "active"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ConversationMessage:
    tenant_id: str
    message_id: str
    session_id: str
    role: MessageRole
    content: str
    actor_id: str
    run_id: str | None = None
    task_id: str | None = None
    asset_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class FileAsset:
    tenant_id: str
    asset_id: str
    owner_id: str
    object_key: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    status: AssetStatus = AssetStatus.PENDING
    project_id: str | None = None
    session_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionRepository(Protocol):
    async def create_session(self, session: ConversationSession) -> None: ...
    async def get_session(self, tenant_id: str, session_id: str) -> ConversationSession | None: ...
    async def append_message(self, message: ConversationMessage) -> None: ...
    async def list_messages(self, tenant_id: str, session_id: str, limit: int = 100) -> tuple[ConversationMessage, ...]: ...
    async def create_asset(self, asset: FileAsset) -> None: ...
    async def get_asset(self, tenant_id: str, asset_id: str) -> FileAsset | None: ...
    async def set_asset_status(self, tenant_id: str, asset_id: str, status: AssetStatus) -> None: ...



class PostgresSessionRepository:
    """PostgreSQL-backed session/message/asset metadata repository.

    Every lookup and mutation is tenant-qualified. The database composite keys
    are the final isolation boundary; callers must never use an unscoped ID.
    """

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    @staticmethod
    def _session(row: Mapping[str, Any]) -> ConversationSession:
        return ConversationSession(
            tenant_id=row["tenant_id"], session_id=str(row["session_id"]),
            owner_id=row["owner_id"], title=row["title"],
            project_id=row["project_id"], status=row["status"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _message(row: Mapping[str, Any]) -> ConversationMessage:
        return ConversationMessage(
            tenant_id=row["tenant_id"], message_id=str(row["message_id"]),
            session_id=str(row["session_id"]), role=MessageRole(row["role"]),
            content=row["content"], actor_id=row["actor_id"],
            task_id=row["task_id"], run_id=row["run_id"],
            asset_ids=tuple(row["asset_ids"] or ()),
            metadata=dict(row["metadata"] or {}), created_at=row["created_at"],
        )

    @staticmethod
    def _asset(row: Mapping[str, Any]) -> FileAsset:
        return FileAsset(
            tenant_id=row["tenant_id"], asset_id=str(row["asset_id"]),
            owner_id=row["owner_id"], object_key=row["object_key"],
            filename=row["filename"], media_type=row["media_type"],
            size_bytes=row["size_bytes"], sha256=row["sha256"],
            status=AssetStatus(row["status"]), project_id=row["project_id"],
            session_id=str(row["session_id"]) if row["session_id"] else None,
            created_at=row["created_at"],
        )

    async def create_session(self, session: ConversationSession) -> None:
        conn = await self._connect()
        async with conn:
            await conn.execute(
                """INSERT INTO platform_sessions
                   (tenant_id, session_id, owner_id, project_id, title, status)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (session.tenant_id, session.session_id, session.owner_id,
                 session.project_id, session.title, session.status),
            )

    async def get_session(self, tenant_id: str, session_id: str) -> ConversationSession | None:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT * FROM platform_sessions
                   WHERE tenant_id=%s AND session_id=%s AND status<>'deleted'""",
                (tenant_id, session_id),
            )
            row = await cur.fetchone()
        return self._session(row) if row else None

    async def append_message(self, message: ConversationMessage) -> None:
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """INSERT INTO platform_messages
                       (tenant_id,message_id,session_id,role,content,actor_id,
                        task_id,run_id,asset_ids,metadata)
                       SELECT %s,%s,s.session_id,%s,%s,%s,%s,%s,%s,%s
                       FROM platform_sessions s
                       WHERE s.tenant_id=%s AND s.session_id=%s AND s.status='active'
                       AND NOT EXISTS (
                         SELECT 1 FROM jsonb_array_elements_text(%s::jsonb) requested(asset_id)
                         WHERE NOT EXISTS (
                           SELECT 1 FROM platform_file_assets a
                           WHERE a.tenant_id=s.tenant_id
                             AND a.asset_id=requested.asset_id::uuid
                             AND a.status='available'
                         )
                       )
                       RETURNING message_id""",
                    (message.tenant_id, message.message_id, message.role.value,
                     message.content, message.actor_id, message.task_id,
                     message.run_id, _session_jsonb(list(message.asset_ids)),
                     _session_jsonb(dict(message.metadata)), message.tenant_id,
                     message.session_id, _session_jsonb(list(message.asset_ids))),
                )
                if await cur.fetchone() is None:
                    raise SessionNotFound("session or referenced file asset not found")

    async def list_messages(
        self, tenant_id: str, session_id: str, limit: int = 100,
    ) -> tuple[ConversationMessage, ...]:
        if limit < 1 or limit > 1000:
            raise SessionError("message limit must be between 1 and 1000")
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT m.* FROM platform_messages m
                   JOIN platform_sessions s
                     ON s.tenant_id=m.tenant_id AND s.session_id=m.session_id
                   WHERE m.tenant_id=%s AND m.session_id=%s
                     AND s.status<>'deleted'
                   ORDER BY m.created_at DESC,m.message_id DESC LIMIT %s""",
                (tenant_id, session_id, limit),
            )
            rows = await cur.fetchall()
        return tuple(self._message(row) for row in reversed(rows))

    async def create_asset(self, asset: FileAsset) -> None:
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """INSERT INTO platform_file_assets
                       (tenant_id,asset_id,owner_id,project_id,session_id,
                        object_key,filename,media_type,size_bytes,sha256,status)
                       SELECT %s,%s,%s,%s,s.session_id,%s,%s,%s,%s,%s,%s
                       FROM (SELECT %s::uuid AS session_id) requested
                       LEFT JOIN platform_sessions s
                         ON s.tenant_id=%s AND s.session_id=requested.session_id
                        AND s.status='active'
                       WHERE (%s::uuid IS NULL OR s.session_id IS NOT NULL)
                       RETURNING asset_id""",
                    (asset.tenant_id, asset.asset_id, asset.owner_id,
                     asset.project_id, asset.session_id, asset.object_key,
                     asset.filename, asset.media_type, asset.size_bytes,
                     asset.sha256, asset.status.value, asset.session_id,
                     asset.tenant_id, asset.session_id),
                )
                if await cur.fetchone() is None:
                    raise SessionNotFound("session not found in tenant")

    async def get_asset(self, tenant_id: str, asset_id: str) -> FileAsset | None:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT * FROM platform_file_assets
                   WHERE tenant_id=%s AND asset_id=%s AND status<>'deleted'""",
                (tenant_id, asset_id),
            )
            row = await cur.fetchone()
        return self._asset(row) if row else None

    async def set_asset_status(
        self, tenant_id: str, asset_id: str, status: AssetStatus,
    ) -> None:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """UPDATE platform_file_assets SET status=%s
                   WHERE tenant_id=%s AND asset_id=%s AND status<>'deleted'
                   RETURNING asset_id""",
                (status.value, tenant_id, asset_id),
            )
            if await cur.fetchone() is None:
                raise SessionNotFound("asset not found")


def _session_jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary,pool]") from exc
    return Jsonb(value)


class ObjectStorage(Protocol):
    async def put(self, object_key: str, content: bytes, *, media_type: str) -> None: ...
    async def get(self, object_key: str) -> bytes: ...
    async def delete(self, object_key: str) -> None: ...


class AssetScanner(Protocol):
    async def scan(self, filename: str, media_type: str, content: bytes) -> bool: ...


class InMemorySessionRepository:
    """Test adapter only; production must use tenant-bound PostgreSQL storage."""
    def __init__(self) -> None:
        self.sessions: dict[tuple[str, str], ConversationSession] = {}
        self.messages: dict[tuple[str, str], list[ConversationMessage]] = {}
        self.assets: dict[tuple[str, str], FileAsset] = {}

    async def create_session(self, session: ConversationSession) -> None:
        key = (session.tenant_id, session.session_id)
        if key in self.sessions:
            raise SessionError("session already exists")
        self.sessions[key] = session

    async def get_session(self, tenant_id: str, session_id: str) -> ConversationSession | None:
        return self.sessions.get((tenant_id, session_id))

    async def append_message(self, message: ConversationMessage) -> None:
        key = (message.tenant_id, message.session_id)
        if key not in self.sessions:
            raise SessionNotFound("session not found")
        self.messages.setdefault(key, []).append(message)

    async def list_messages(self, tenant_id: str, session_id: str, limit: int = 100) -> tuple[ConversationMessage, ...]:
        if (tenant_id, session_id) not in self.sessions:
            raise SessionNotFound("session not found")
        return tuple(self.messages.get((tenant_id, session_id), [])[-limit:])

    async def create_asset(self, asset: FileAsset) -> None:
        key = (asset.tenant_id, asset.asset_id)
        if key in self.assets:
            raise SessionError("asset already exists")
        self.assets[key] = asset

    async def get_asset(self, tenant_id: str, asset_id: str) -> FileAsset | None:
        return self.assets.get((tenant_id, asset_id))

    async def set_asset_status(self, tenant_id: str, asset_id: str, status: AssetStatus) -> None:
        key = (tenant_id, asset_id)
        asset = self.assets.get(key)
        if asset is None:
            raise SessionNotFound("asset not found")
        from dataclasses import replace
        self.assets[key] = replace(asset, status=status)


class InMemoryObjectStorage:
    """Test adapter. Never use as production durable storage."""
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, object_key: str, content: bytes, *, media_type: str) -> None:
        self.objects[object_key] = bytes(content)

    async def get(self, object_key: str) -> bytes:
        if object_key not in self.objects:
            raise SessionNotFound("object not found")
        return self.objects[object_key]

    async def delete(self, object_key: str) -> None:
        self.objects.pop(object_key, None)


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class SessionAssetService:
    def __init__(
        self, repository: SessionRepository, storage: ObjectStorage, *,
        max_upload_bytes: int = 25 * 1024 * 1024,
        allowed_media_types: frozenset[str] | None = None,
        scanner: AssetScanner | None = None,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be positive")
        self.repository = repository
        self.storage = storage
        self.max_upload_bytes = max_upload_bytes
        self.allowed_media_types = allowed_media_types
        self.scanner = scanner

    async def create_session(self, *, tenant_id: str, owner_id: str, title: str = "", project_id: str | None = None) -> ConversationSession:
        if not tenant_id.strip() or not owner_id.strip():
            raise SessionError("tenant_id and owner_id are required")
        session = ConversationSession(tenant_id, str(uuid4()), owner_id, title[:200], project_id)
        await self.repository.create_session(session)
        return session

    async def append_message(
        self, *, tenant_id: str, actor_id: str, session_id: str,
        role: MessageRole, content: str, run_id: str | None = None,
        task_id: str | None = None, asset_ids: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> ConversationMessage:
        session = await self.repository.get_session(tenant_id, session_id)
        if session is None or session.status != "active":
            raise SessionNotFound("session not found or inactive")
        if not actor_id.strip() or not content.strip():
            raise SessionError("actor_id and message content are required")
        for asset_id in asset_ids:
            asset = await self.repository.get_asset(tenant_id, asset_id)
            if asset is None or asset.status != AssetStatus.AVAILABLE:
                raise SessionNotFound("referenced file asset is not available")
        message = ConversationMessage(
            tenant_id, str(uuid4()), session_id, role, content, actor_id,
            run_id, task_id, tuple(asset_ids), dict(metadata or {}),
        )
        await self.repository.append_message(message)
        return message

    async def upload_asset(
        self, *, tenant_id: str, owner_id: str, filename: str,
        media_type: str, content: bytes, project_id: str | None = None,
        session_id: str | None = None,
    ) -> FileAsset:
        if not tenant_id.strip() or not owner_id.strip():
            raise SessionError("tenant_id and owner_id are required")
        if not content or len(content) > self.max_upload_bytes:
            raise SessionError("file is empty or exceeds upload size limit")
        if self.allowed_media_types is not None and media_type not in self.allowed_media_types:
            raise SessionError("media type is not allowed")
        if session_id is not None:
            session = await self.repository.get_session(tenant_id, session_id)
            if session is None:
                raise SessionNotFound("session not found")
        clean_name = _SAFE_FILENAME.sub("_", filename.replace(chr(92), "/").rsplit("/", 1)[-1])[:180]
        if not clean_name or clean_name in {".", ".."}:
            raise SessionError("invalid filename")
        asset_id = str(uuid4())
        digest = hashlib.sha256(content).hexdigest()
        object_key = f"tenant/{tenant_id}/assets/{asset_id}/{digest}"
        await self.storage.put(object_key, content, media_type=media_type)
        asset = FileAsset(
            tenant_id, asset_id, owner_id, object_key, clean_name, media_type,
            len(content), digest, AssetStatus.SCANNING if self.scanner else AssetStatus.AVAILABLE,
            project_id, session_id,
        )
        try:
            await self.repository.create_asset(asset)
        except Exception:
            await self.storage.delete(object_key)
            raise
        if self.scanner is not None:
            try:
                accepted = await self.scanner.scan(clean_name, media_type, content)
            except Exception as exc:
                await self.repository.set_asset_status(tenant_id, asset_id, AssetStatus.REJECTED)
                await self.storage.delete(object_key)
                raise SessionError("file safety scan failed") from exc
            status = AssetStatus.AVAILABLE if accepted else AssetStatus.REJECTED
            await self.repository.set_asset_status(tenant_id, asset_id, status)
            if not accepted:
                await self.storage.delete(object_key)
            asset = FileAsset(
                asset.tenant_id, asset.asset_id, asset.owner_id, asset.object_key,
                asset.filename, asset.media_type, asset.size_bytes, asset.sha256,
                status, asset.project_id, asset.session_id, asset.created_at,
            )
        return asset

    async def download_asset(self, *, tenant_id: str, asset_id: str) -> tuple[FileAsset, bytes]:
        asset = await self.repository.get_asset(tenant_id, asset_id)
        if asset is None or asset.status != AssetStatus.AVAILABLE:
            raise SessionNotFound("file asset not found or unavailable")
        content = await self.storage.get(asset.object_key)
        if hashlib.sha256(content).hexdigest() != asset.sha256:
            raise SessionError("stored object integrity check failed")
        return asset, content
