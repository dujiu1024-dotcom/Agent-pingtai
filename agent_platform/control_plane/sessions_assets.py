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
    version: int = 1


@dataclass(frozen=True)
class FileAssetVersion:
    tenant_id: str
    asset_id: str
    version: int
    object_key: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    status: AssetStatus
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionRepository(Protocol):
    async def create_session(self, session: ConversationSession) -> None: ...
    async def get_session(self, tenant_id: str, session_id: str) -> ConversationSession | None: ...
    async def list_sessions(self, tenant_id: str, owner_id: str, limit: int = 100, project_id: str | None = None) -> tuple[ConversationSession, ...]: ...
    async def append_message(self, message: ConversationMessage) -> None: ...
    async def list_messages(self, tenant_id: str, session_id: str, limit: int = 100) -> tuple[ConversationMessage, ...]: ...
    async def create_asset(self, asset: FileAsset) -> None: ...
    async def get_asset(self, tenant_id: str, asset_id: str) -> FileAsset | None: ...
    async def list_asset_versions(self, tenant_id: str, asset_id: str) -> tuple[FileAssetVersion, ...]: ...
    async def get_asset_version(self, tenant_id: str, asset_id: str, version: int) -> FileAssetVersion | None: ...
    async def create_asset_version(self, version: FileAssetVersion, *, expected_version: int) -> None: ...
    async def list_assets(self, tenant_id: str, owner_id: str, session_id: str | None = None, limit: int = 100, project_id: str | None = None) -> tuple[FileAsset, ...]: ...
    async def set_asset_status(self, tenant_id: str, asset_id: str, status: AssetStatus) -> None: ...
    async def set_asset_status_if_not_deleted(
        self, tenant_id: str, asset_id: str, status: AssetStatus
    ) -> bool: ...



class ObjectStorage(Protocol):
    async def put(self, object_key: str, content: bytes, *, media_type: str) -> None: ...
    async def get(self, object_key: str) -> bytes: ...
    async def delete(self, object_key: str) -> None: ...


class AssetScanner(Protocol):
    async def scan(self, filename: str, media_type: str, content: bytes) -> bool: ...


class AssetProcessingQueue(Protocol):
    async def enqueue(self, *, tenant_id: str, asset_id: str) -> None: ...


class InMemorySessionRepository:
    """Test adapter only; production must use tenant-bound PostgreSQL storage."""
    def __init__(self) -> None:
        self.sessions: dict[tuple[str, str], ConversationSession] = {}
        self.messages: dict[tuple[str, str], list[ConversationMessage]] = {}
        self.assets: dict[tuple[str, str], FileAsset] = {}
        self.asset_versions: dict[tuple[str, str], list[FileAssetVersion]] = {}

    async def create_session(self, session: ConversationSession) -> None:
        key = (session.tenant_id, session.session_id)
        if key in self.sessions:
            raise SessionError("session already exists")
        self.sessions[key] = session

    async def get_session(self, tenant_id: str, session_id: str) -> ConversationSession | None:
        return self.sessions.get((tenant_id, session_id))

    async def list_sessions(self, tenant_id: str, owner_id: str, limit: int = 100, project_id: str | None = None) -> tuple[ConversationSession, ...]:
        if not 1 <= limit <= 500:
            raise SessionError("limit must be between 1 and 500")
        rows = [row for (tenant, _), row in self.sessions.items()
                if tenant == tenant_id and row.owner_id == owner_id and row.status != "deleted"
                and (project_id is None or row.project_id == project_id)]
        return tuple(sorted(rows, key=lambda row: row.updated_at, reverse=True)[:limit])

    async def append_message(self, message: ConversationMessage) -> None:
        key = (message.tenant_id, message.session_id)
        if key not in self.sessions:
            raise SessionNotFound("session not found")
        self.messages.setdefault(key, []).append(message)

    async def list_messages(self, tenant_id: str, session_id: str, limit: int = 100) -> tuple[ConversationMessage, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if (tenant_id, session_id) not in self.sessions:
            raise SessionNotFound("session not found")
        return tuple(self.messages.get((tenant_id, session_id), [])[-limit:])

    async def create_asset(self, asset: FileAsset) -> None:
        key = (asset.tenant_id, asset.asset_id)
        if key in self.assets:
            raise SessionError("asset already exists")
        self.assets[key] = asset
        self.asset_versions[key] = [FileAssetVersion(
            asset.tenant_id, asset.asset_id, asset.version, asset.object_key,
            asset.filename, asset.media_type, asset.size_bytes, asset.sha256,
            asset.status, asset.created_at,
        )]

    async def get_asset(self, tenant_id: str, asset_id: str) -> FileAsset | None:
        return self.assets.get((tenant_id, asset_id))

    async def list_asset_versions(self, tenant_id: str, asset_id: str) -> tuple[FileAssetVersion, ...]:
        return tuple(self.asset_versions.get((tenant_id, asset_id), ()))

    async def get_asset_version(self, tenant_id: str, asset_id: str, version: int) -> FileAssetVersion | None:
        return next((item for item in self.asset_versions.get((tenant_id, asset_id), ()) if item.version == version), None)

    async def create_asset_version(self, version: FileAssetVersion, *, expected_version: int) -> None:
        key = (version.tenant_id, version.asset_id)
        current = self.assets.get(key)
        if current is None or current.status is not AssetStatus.AVAILABLE:
            raise SessionNotFound("asset not found or unavailable")
        if current.version != expected_version or version.version != expected_version + 1:
            raise SessionError("file asset version changed; retry from the latest version")
        from dataclasses import replace
        self.asset_versions.setdefault(key, []).append(version)
        self.assets[key] = replace(
            current, object_key=version.object_key, filename=version.filename,
            media_type=version.media_type, size_bytes=version.size_bytes,
            sha256=version.sha256, status=version.status,
            created_at=version.created_at, version=version.version,
        )

    async def list_assets(self, tenant_id: str, owner_id: str, session_id: str | None = None, limit: int = 100, project_id: str | None = None) -> tuple[FileAsset, ...]:
        if not 1 <= limit <= 500:
            raise SessionError("limit must be between 1 and 500")
        rows = [row for (tenant, _), row in self.assets.items()
                if tenant == tenant_id and row.owner_id == owner_id
                and (session_id is None or row.session_id == session_id)
                and (project_id is None or row.project_id == project_id)
                and row.status is not AssetStatus.DELETED]
        return tuple(sorted(rows, key=lambda row: row.created_at, reverse=True)[:limit])

    async def set_asset_status(self, tenant_id: str, asset_id: str, status: AssetStatus) -> None:
        key = (tenant_id, asset_id)
        asset = self.assets.get(key)
        if asset is None:
            raise SessionNotFound("asset not found")
        from dataclasses import replace
        self.assets[key] = replace(asset, status=status)
        versions = self.asset_versions.get(key, [])
        if status is AssetStatus.DELETED:
            self.asset_versions[key] = [replace(item, status=status) for item in versions]
        elif versions:
            versions[-1] = replace(versions[-1], status=status)

    async def set_asset_status_if_not_deleted(
        self, tenant_id: str, asset_id: str, status: AssetStatus
    ) -> bool:
        key = (tenant_id, asset_id)
        asset = self.assets.get(key)
        if asset is None or asset.status is AssetStatus.DELETED:
            return False
        from dataclasses import replace
        self.assets[key] = replace(asset, status=status)
        versions = self.asset_versions.get(key, [])
        if versions:
            versions[-1] = replace(versions[-1], status=status)
        return True

    async def set_asset_status_if_not_deleted(
        self, tenant_id: str, asset_id: str, status: AssetStatus
    ) -> bool:
        key = (tenant_id, asset_id)
        asset = self.assets.get(key)
        if asset is None or asset.status is AssetStatus.DELETED:
            return False
        from dataclasses import replace
        self.assets[key] = replace(asset, status=status)
        versions = self.asset_versions.get(key, [])
        if versions:
            versions[-1] = replace(versions[-1], status=status)
        return True


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
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")^_.+-]*/[a-z0-9][a-z0-9!#_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")^_.+-]*$")


def _normalize_media_type(value: str) -> str:
    """Validate and canonicalize an HTTP media type before storing asset metadata."""
    if not isinstance(value, str) or not value or len(value) > 127 or value != value.strip():
        raise SessionError("invalid media type")
    normalized = value.lower()
    if not _MEDIA_TYPE.fullmatch(normalized):
        raise SessionError("invalid media type")
    return normalized


class SessionAssetService:
    def __init__(
        self, repository: SessionRepository, storage: ObjectStorage, *,
        max_upload_bytes: int = 25 * 1024 * 1024,
        allowed_media_types: frozenset[str] | None = None,
        scanner: AssetScanner | None = None,
        processing_queue: AssetProcessingQueue | None = None,
        knowledge_for_tenant: Any | None = None,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be positive")
        self.repository = repository
        self.storage = storage
        self.max_upload_bytes = max_upload_bytes
        self.allowed_media_types = allowed_media_types
        self.scanner = scanner
        self.processing_queue = processing_queue
        self.knowledge_for_tenant = knowledge_for_tenant

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
            if asset.project_id != session.project_id:
                raise SessionError("referenced file must belong to the same project as its session")
            if asset.session_id is not None and asset.session_id != session_id:
                raise SessionError("referenced file is already linked to another session")
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
        media_type = _normalize_media_type(media_type)
        if self.allowed_media_types is not None and media_type not in {
            item.lower() for item in self.allowed_media_types
        }:
            raise SessionError("media type is not allowed")
        if session_id is not None:
            session = await self.repository.get_session(tenant_id, session_id)
            if session is None:
                raise SessionNotFound("session not found")
            if project_id is None:
                project_id = session.project_id
            elif project_id != session.project_id:
                raise SessionError("file project must match its linked session")
        clean_name = _SAFE_FILENAME.sub("_", filename.replace(chr(92), "/").rsplit("/", 1)[-1])[:180]
        if not clean_name or clean_name in {".", ".."}:
            raise SessionError("invalid filename")
        asset_id = str(uuid4())
        digest = hashlib.sha256(content).hexdigest()
        object_key = f"tenant/{tenant_id}/assets/{asset_id}/{digest}"
        await self.storage.put(object_key, content, media_type=media_type)
        asset = FileAsset(
            tenant_id, asset_id, owner_id, object_key, clean_name, media_type,
            len(content), digest,
            AssetStatus.PENDING if self.processing_queue else (
                AssetStatus.SCANNING if self.scanner else AssetStatus.AVAILABLE
            ),
            project_id, session_id,
        )
        try:
            await self.repository.create_asset(asset)
        except Exception:
            await self.storage.delete(object_key)
            raise
        if self.processing_queue is not None:
            try:
                await self.processing_queue.enqueue(tenant_id=tenant_id, asset_id=asset_id)
            except Exception as exc:
                await self.repository.set_asset_status(tenant_id, asset_id, AssetStatus.REJECTED)
                await self.storage.delete(object_key)
                raise SessionError("file processing could not be scheduled") from exc
        elif self.scanner is not None:
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

    async def create_asset_version(
        self, *, tenant_id: str, asset_id: str, content: bytes,
        filename: str | None = None, media_type: str | None = None,
        expected_version: int | None = None,
    ) -> FileAssetVersion:
        asset = await self.repository.get_asset(tenant_id, asset_id)
        if asset is None or asset.status is not AssetStatus.AVAILABLE:
            raise SessionNotFound("file asset not found or unavailable")
        if not content or len(content) > self.max_upload_bytes:
            raise SessionError("file is empty or exceeds upload size limit")
        if expected_version is not None and expected_version != asset.version:
            raise SessionError("file asset version changed; reload before updating")
        clean_name = _SAFE_FILENAME.sub("_", (filename or asset.filename).replace(chr(92), "/").rsplit("/", 1)[-1])[:180]
        if not clean_name or clean_name in {".", ".."}:
            raise SessionError("invalid filename")
        selected_type = _normalize_media_type(media_type or asset.media_type)
        if self.allowed_media_types is not None and selected_type not in {
            item.lower() for item in self.allowed_media_types
        }:
            raise SessionError("media type is not allowed")
        digest = hashlib.sha256(content).hexdigest()
        next_version = asset.version + 1
        object_key = f"tenant/{tenant_id}/assets/{asset_id}/versions/{next_version}/{digest}"
        await self.storage.put(object_key, content, media_type=selected_type)
        version = FileAssetVersion(
            tenant_id, asset_id, next_version, object_key, clean_name, selected_type,
            len(content), digest,
            AssetStatus.PENDING if self.processing_queue else (
                AssetStatus.SCANNING if self.scanner else AssetStatus.AVAILABLE
            ),
        )
        try:
            await self.repository.create_asset_version(version, expected_version=asset.version)
        except Exception:
            await self.storage.delete(object_key)
            raise
        if self.processing_queue is not None:
            try:
                await self.processing_queue.enqueue(tenant_id=tenant_id, asset_id=asset_id)
            except Exception as exc:
                await self.repository.set_asset_status(tenant_id, asset_id, AssetStatus.REJECTED)
                await self.storage.delete(object_key)
                raise SessionError("file processing could not be scheduled") from exc
        elif self.scanner is not None:
            try:
                accepted = await self.scanner.scan(clean_name, selected_type, content)
            except Exception as exc:
                await self.repository.set_asset_status(tenant_id, asset_id, AssetStatus.REJECTED)
                await self.storage.delete(object_key)
                raise SessionError("file safety scan failed") from exc
            status = AssetStatus.AVAILABLE if accepted else AssetStatus.REJECTED
            await self.repository.set_asset_status(tenant_id, asset_id, status)
            if not accepted:
                await self.storage.delete(object_key)
            version = FileAssetVersion(
                version.tenant_id, version.asset_id, version.version, version.object_key,
                version.filename, version.media_type, version.size_bytes, version.sha256,
                status, version.created_at,
            )
        return version

    async def list_asset_versions(self, *, tenant_id: str, asset_id: str) -> tuple[FileAssetVersion, ...]:
        asset = await self.repository.get_asset(tenant_id, asset_id)
        if asset is None:
            raise SessionNotFound("file asset not found")
        return await self.repository.list_asset_versions(tenant_id, asset_id)

    async def download_asset_version(
        self, *, tenant_id: str, asset_id: str, version: int,
    ) -> tuple[FileAssetVersion, bytes]:
        if version < 1:
            raise SessionError("version must be positive")
        record = await self.repository.get_asset_version(tenant_id, asset_id, version)
        if record is None or record.status is not AssetStatus.AVAILABLE:
            raise SessionNotFound("file asset version not found or unavailable")
        content = await self.storage.get(record.object_key)
        if hashlib.sha256(content).hexdigest() != record.sha256:
            raise SessionError("stored object integrity check failed")
        return record, content

    async def restore_asset_version(
        self, *, tenant_id: str, asset_id: str, version: int,
        expected_version: int | None = None,
    ) -> FileAssetVersion:
        """Restore historical bytes as a new current version; history remains append-only."""
        historical, content = await self.download_asset_version(
            tenant_id=tenant_id, asset_id=asset_id, version=version,
        )
        return await self.create_asset_version(
            tenant_id=tenant_id, asset_id=asset_id, content=content,
            filename=historical.filename, media_type=historical.media_type,
            expected_version=expected_version,
        )

    async def create_download_url(
        self, *, tenant_id: str, asset_id: str, expires_in: int = 300,
    ) -> tuple[FileAsset, str]:
        """Return a short-lived URL only for an available tenant-owned asset."""
        if not 1 <= expires_in <= 900:
            raise SessionError("download URL TTL must be between 1 and 900 seconds")
        asset = await self.repository.get_asset(tenant_id, asset_id)
        if asset is None or asset.status != AssetStatus.AVAILABLE:
            raise SessionNotFound("file asset not found or unavailable")
        presign = getattr(self.storage, "presign_get", None)
        if not callable(presign):
            raise SessionError("object storage does not support presigned downloads")
        try:
            url = presign(asset.object_key, expires_in=expires_in)
            if hasattr(url, "__await__"):
                url = await url
        except Exception as exc:
            raise SessionError("could not create file download URL") from exc
        if not isinstance(url, str) or not url.startswith("https://"):
            raise SessionError("object storage returned an invalid download URL")
        return asset, url

    async def download_asset(self, *, tenant_id: str, asset_id: str) -> tuple[FileAsset, bytes]:
        asset = await self.repository.get_asset(tenant_id, asset_id)
        if asset is None or asset.status != AssetStatus.AVAILABLE:
            raise SessionNotFound("file asset not found or unavailable")
        content = await self.storage.get(asset.object_key)
        if hashlib.sha256(content).hexdigest() != asset.sha256:
            raise SessionError("stored object integrity check failed")
        return asset, content

    async def delete_asset(self, *, tenant_id: str, asset_id: str) -> None:
        """Tombstone metadata before deleting bytes; retries safely finish storage cleanup."""
        asset = await self.repository.get_asset(tenant_id, asset_id)
        if asset is None:
            raise SessionNotFound("file asset not found")
        if asset.status is not AssetStatus.DELETED:
            await self.repository.set_asset_status(
                tenant_id, asset_id, AssetStatus.DELETED,
            )
        # The tombstone blocks downloads immediately. Retryable index cleanup
        # is performed before object cleanup so stale chunks cannot remain searchable.
        if self.knowledge_for_tenant is not None:
            try:
                knowledge = self.knowledge_for_tenant(tenant_id)
                await knowledge.repository.delete_document(tenant_id, asset_id)
            except Exception as exc:
                raise SessionError(
                    "file is tombstoned but knowledge index cleanup failed; retry deletion"
                ) from exc
        try:
            versions = await self.repository.list_asset_versions(tenant_id, asset_id)
            object_keys = {item.object_key for item in versions}
            object_keys.add(asset.object_key)
            for object_key in object_keys:
                await self.storage.delete(object_key)
        except Exception as exc:
            raise SessionError(
                "file is tombstoned but object cleanup failed; retry deletion"
            ) from exc
