"""Provider-neutral external data connector lifecycle.

Secrets are resolved only at execution time. Cursor advancement is compare-and-swap;
revocation wins over an in-flight sync and triggers the configured data disposition.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from agent_platform.connectors.base import BaseConnector


class ConnectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class DataConnection:
    tenant_id: str
    connection_id: str
    provider: str
    # OAuth access/refresh material stays in the secret manager; this is only its opaque reference.
    secret_ref: str
    scopes: tuple[str, ...]
    display_name: str = ""
    status: str = "active"
    cursor: Mapping[str, Any] | None = None
    version: int = 0
    last_synced_at: datetime | None = None
    revoked_at: datetime | None = None
    last_error: str | None = None
    project_id: str | None = None


@dataclass(frozen=True)
class ConnectorPage:
    items: tuple[Mapping[str, Any], ...]
    next_cursor: Mapping[str, Any] | None
    has_more: bool = False


class ConnectorProvider(Protocol):
    async def fetch_page(
        self, *, credential: str, scopes: tuple[str, ...],
        cursor: Mapping[str, Any] | None,
    ) -> ConnectorPage: ...

    async def revoke(self, *, credential: str) -> None: ...


class ConnectorRepository(Protocol):
    async def create(self, connection: DataConnection, *, created_by: str) -> None: ...
    async def get(self, tenant_id: str, connection_id: str) -> DataConnection | None: ...
    async def list(self, tenant_id: str) -> list[DataConnection]: ...
    async def update_secret_ref(
        self, *, tenant_id: str, connection_id: str, secret_ref: str,
    ) -> bool: ...
    async def save_cursor(
        self, *, tenant_id: str, connection_id: str, expected_version: int,
        cursor: Mapping[str, Any] | None,
    ) -> bool: ...
    async def mark_error(self, *, tenant_id: str, connection_id: str, error: str) -> None: ...
    async def clear_error(self, *, tenant_id: str, connection_id: str) -> None: ...
    async def revoke(self, *, tenant_id: str, connection_id: str) -> DataConnection | None: ...


@dataclass(frozen=True)
class ConnectorCleanupJob:
    tenant_id: str
    job_id: str
    connection_id: str
    disposition: str
    remote_revoked: bool
    sink_cleaned: bool
    attempts: int
    max_attempts: int
    lease_owner: str


class ConnectorCleanupQueue(Protocol):
    async def enqueue(
        self, *, tenant_id: str, connection_id: str, disposition: str,
    ) -> None: ...
    async def claim(self, *, worker_id: str, lease_seconds: int = 120) -> ConnectorCleanupJob | None: ...
    async def mark_remote_revoked(self, job: ConnectorCleanupJob) -> None: ...
    async def mark_sink_cleaned(self, job: ConnectorCleanupJob) -> None: ...
    async def complete(self, job: ConnectorCleanupJob) -> None: ...
    async def fail(self, job: ConnectorCleanupJob, *, error_code: str) -> bool: ...


class ConnectorSink(Protocol):
    async def upsert_page(
        self, *, tenant_id: str, connection_id: str,
        items: tuple[Mapping[str, Any], ...],
    ) -> None: ...
    async def revoke_connection(
        self, *, tenant_id: str, connection_id: str, disposition: str,
    ) -> None: ...


class SecretResolver(Protocol):
    """Resolve an opaque secret:// reference just in time; never persist the returned token."""

    async def resolve(self, secret_ref: str) -> str: ...


class InMemoryConnectorRepository:
    """Deterministic test adapter."""
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], DataConnection] = {}

    async def add(self, connection: DataConnection) -> None:
        await self.create(connection, created_by="test")

    async def create(self, connection: DataConnection, *, created_by: str) -> None:
        key = (connection.tenant_id, connection.connection_id)
        if key in self.records:
            raise ConnectorError("connection already exists")
        self.records[key] = copy.deepcopy(connection)

    async def get(self, tenant_id: str, connection_id: str) -> DataConnection | None:
        item = self.records.get((tenant_id, connection_id))
        return copy.deepcopy(item) if item else None

    async def list(self, tenant_id: str) -> list[DataConnection]:
        return [
            copy.deepcopy(item) for (owner, _), item in sorted(self.records.items())
            if owner == tenant_id
        ]

    async def update_secret_ref(
        self, *, tenant_id: str, connection_id: str, secret_ref: str,
    ) -> bool:
        key = (tenant_id, connection_id)
        item = self.records.get(key)
        if item is None or item.status != "active":
            return False
        from dataclasses import replace
        self.records[key] = replace(item, secret_ref=secret_ref, version=item.version + 1)
        return True

    async def save_cursor(
        self, *, tenant_id: str, connection_id: str, expected_version: int,
        cursor: Mapping[str, Any] | None,
    ) -> bool:
        key = (tenant_id, connection_id)
        item = self.records.get(key)
        if item is None or item.status != "active" or item.version != expected_version:
            return False
        from dataclasses import replace
        self.records[key] = replace(
            item, cursor=copy.deepcopy(cursor), version=item.version + 1,
            last_synced_at=datetime.now(timezone.utc), last_error=None,
        )
        return True

    async def mark_error(self, *, tenant_id: str, connection_id: str, error: str) -> None:
        key = (tenant_id, connection_id)
        item = self.records.get(key)
        if item is not None and item.status in {"active", "revoked"}:
            from dataclasses import replace
            self.records[key] = replace(item, last_error=error[:500])

    async def clear_error(self, *, tenant_id: str, connection_id: str) -> None:
        key = (tenant_id, connection_id)
        item = self.records.get(key)
        if item is not None:
            from dataclasses import replace
            self.records[key] = replace(item, last_error=None)

    async def revoke(self, *, tenant_id: str, connection_id: str) -> DataConnection | None:
        key = (tenant_id, connection_id)
        item = self.records.get(key)
        if item is None or item.status != "active":
            return None
        from dataclasses import replace
        revoked = replace(item, status="revoked", revoked_at=datetime.now(timezone.utc), version=item.version + 1)
        self.records[key] = revoked
        return copy.deepcopy(revoked)


class DataConnectorService:
    def __init__(
        self, *, repository: ConnectorRepository,
        providers: Mapping[str, ConnectorProvider], secrets: SecretResolver,
        sink: ConnectorSink, cleanup_queue: ConnectorCleanupQueue | None = None,
        sync_queue: Any | None = None, secret_writer: Any | None = None,
    ) -> None:
        self.repository = repository
        self.providers = dict(providers)
        self.secrets = secrets
        self.sink = sink
        self.cleanup_queue = cleanup_queue
        self.sync_queue = sync_queue
        # 本地部署允许把凭据直接写入本地密钥库；写入后只返回引用。
        self.secret_writer = secret_writer

    async def _resolve_provider_credential(
        self, connection: DataConnection, provider: ConnectorProvider,
    ) -> str:
        if isinstance(provider, BaseConnector) and provider.authenticator is not None:
            if provider.credential_ref != connection.secret_ref:
                raise ConnectorError("provider credential reference does not match connection")
            return await provider.authenticate(
                tenant_id=connection.tenant_id, required_scopes=connection.scopes,
            )
        return await self.secrets.resolve(connection.secret_ref)

    async def store_credential(
        self, *, tenant_id: str, credential: str | Mapping[str, Any],
    ) -> str:
        if self.secret_writer is None:
            raise ConnectorError("本地密钥库未配置，无法保存连接器凭据")
        if isinstance(credential, Mapping):
            return await self.secret_writer.store_json(
                tenant_id=tenant_id, payload=credential, kind="connector",
            )
        if not isinstance(credential, str) or not credential.strip():
            raise ConnectorError("凭据内容不能为空")
        return await self.secret_writer.store(
            tenant_id=tenant_id, secret=credential, kind="connector",
        )

    async def list_connections(self, *, tenant_id: str) -> list[DataConnection]:
        return await self.repository.list(tenant_id)

    async def rotate_credential(
        self, *, tenant_id: str, connection_id: str,
        credential: str | Mapping[str, Any],
    ) -> str:
        connection = await self.repository.get(tenant_id, connection_id)
        if connection is None or connection.status != "active":
            raise ConnectorError("connection is not active")
        secret_ref = await self.store_credential(tenant_id=tenant_id, credential=credential)
        if not await self.repository.update_secret_ref(
            tenant_id=tenant_id, connection_id=connection_id, secret_ref=secret_ref,
        ):
            raise ConnectorError("connection is not active")
        return secret_ref

    async def sync_all_pages(
        self, *, tenant_id: str, connection_id: str, max_pages: int = 20,
    ) -> tuple[int, bool]:
        """连续拉取直到没有下一页；返回 (写入条数, 是否仍有剩余)。"""
        total = 0
        for _ in range(max(1, max_pages)):
            page = await self.sync_page(tenant_id=tenant_id, connection_id=connection_id)
            total += len(page.items)
            if not page.has_more:
                return total, False
        return total, True

    async def request_sync(self, *, tenant_id: str, connection_id: str) -> None:
        connection = await self.repository.get(tenant_id, connection_id)
        if connection is None or connection.status != "active":
            raise ConnectorError("connection is not active")
        if self.sync_queue is None:
            raise ConnectorError("durable connector sync queue is not configured")
        await self.sync_queue.enqueue(tenant_id=tenant_id, connection_id=connection_id)

    async def create_connection(
        self, *, tenant_id: str, connection_id: str, provider: str,
        display_name: str, secret_ref: str, scopes: tuple[str, ...], actor_id: str,
        project_id: str | None = None,
    ) -> DataConnection:
        if (
            not tenant_id.strip() or not actor_id.strip()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", connection_id)
        ):
            raise ConnectorError("tenant, actor and valid connection identifiers are required")
        if provider not in self.providers:
            raise ConnectorError("connector provider is not registered")
        if not display_name.strip() or len(display_name) > 120:
            raise ConnectorError("display name must contain 1 to 120 characters")
        if (
            not secret_ref.startswith(f"secret://{tenant_id}/")
            or len(secret_ref) > 512
            or any(character.isspace() for character in secret_ref)
            or any(part in {".", ".."} for part in secret_ref.split("/")[3:])
        ):
            raise ConnectorError("credentials must use a tenant-scoped secret:// reference")
        normalized_scopes = tuple(sorted(set(scope.strip() for scope in scopes if scope.strip())))
        if not normalized_scopes or any(len(scope) > 128 for scope in normalized_scopes):
            raise ConnectorError("one or more valid connector scopes are required")
        connection = DataConnection(
            tenant_id=tenant_id, connection_id=connection_id, provider=provider,
            secret_ref=secret_ref, scopes=normalized_scopes, display_name=display_name.strip(),
            project_id=project_id,
        )
        await self.repository.create(connection, created_by=actor_id)
        return connection

    async def schedule_sync(self, *, tenant_id: str, connection_id: str) -> None:
        connection = await self.repository.get(tenant_id, connection_id)
        if connection is None or connection.status != "active":
            raise ConnectorError("connection is not active")
        if self.sync_queue is None:
            raise ConnectorError("connector sync queue is not configured")
        await self.sync_queue.enqueue(
            tenant_id=tenant_id, connection_id=connection_id,
            job_type="sync", disposition="retain",
        )

    async def sync_once(
        self, *, tenant_id: str, connection_id: str,
        revocation_disposition: str = "retain",
    ) -> int:
        page = await self.sync_page(
            tenant_id=tenant_id, connection_id=connection_id,
            revocation_disposition=revocation_disposition,
        )
        return len(page.items)

    async def sync_page(
        self, *, tenant_id: str, connection_id: str,
        revocation_disposition: str = "retain",
    ) -> ConnectorPage:
        connection = await self.repository.get(tenant_id, connection_id)
        if connection is None or connection.status != "active":
            raise ConnectorError("connection is not active")
        provider = self.providers.get(connection.provider)
        if provider is None:
            raise ConnectorError("connector provider is not registered")
        try:
            credential = await self._resolve_provider_credential(connection, provider)
            page = await provider.fetch_page(
                credential=credential, scopes=connection.scopes, cursor=connection.cursor,
            )
            if page.has_more and (
                page.next_cursor is None or page.next_cursor == connection.cursor
            ):
                raise ConnectorError("provider pagination did not advance its cursor")
            latest = await self.repository.get(tenant_id, connection_id)
            if latest is None or latest.status != "active":
                await self.sink.revoke_connection(
                    tenant_id=tenant_id, connection_id=connection_id,
                    disposition=revocation_disposition,
                )
                raise ConnectorError("connection was revoked during synchronization")
            if latest.version != connection.version:
                # Another active sync won the cursor CAS; replay is safe because
                # downstream writes are required to be idempotent.
                raise ConnectorError("connection cursor changed during synchronization")
            await self.sink.upsert_page(
                tenant_id=tenant_id, connection_id=connection_id, items=page.items,
            )
            advanced = await self.repository.save_cursor(
                tenant_id=tenant_id, connection_id=connection_id,
                expected_version=connection.version, cursor=page.next_cursor,
            )
            if not advanced:
                latest = await self.repository.get(tenant_id, connection_id)
                if latest is None or latest.status != "active":
                    await self.sink.revoke_connection(
                        tenant_id=tenant_id, connection_id=connection_id,
                        disposition=revocation_disposition,
                    )
                raise ConnectorError("cursor compare-and-swap failed; page may be replayed")
            return page
        except ConnectorError:
            raise
        except Exception as exc:
            await self.repository.mark_error(
                tenant_id=tenant_id, connection_id=connection_id,
                error=type(exc).__name__,
            )
            raise ConnectorError("connector synchronization failed") from exc

    async def revoke(
        self, *, tenant_id: str, connection_id: str,
        revocation_disposition: str = "retain", schedule_retry: bool = True,
    ) -> bool:
        if revocation_disposition not in {"retain", "delete"}:
            raise ValueError("revocation disposition must be retain or delete")
        connection = await self.repository.revoke(
            tenant_id=tenant_id, connection_id=connection_id,
        )
        if connection is None:
            connection = await self.repository.get(tenant_id, connection_id)
            if connection is None or connection.status != "revoked":
                return False
        if self.cleanup_queue is not None:
            # The local permission revocation is already durable. Remote OAuth
            # revocation and downstream deletion are retried by the cleanup worker.
            await self.cleanup_queue.enqueue(
                tenant_id=tenant_id, connection_id=connection_id,
                disposition=revocation_disposition,
            )
            return True
        cleanup_error: str | None = None
        provider = self.providers.get(connection.provider)
        if provider is not None:
            try:
                credential = await self._resolve_provider_credential(connection, provider)
                await provider.revoke(credential=credential)
            except Exception as exc:
                # Never persist provider responses or credential material.
                cleanup_error = f"remote revoke failed: {type(exc).__name__}"

        # Local access is revoked before either remote operation. Always attempt
        # downstream disposition even if the provider's revoke endpoint is down.
        try:
            await self.sink.revoke_connection(
                tenant_id=tenant_id, connection_id=connection_id,
                disposition=revocation_disposition,
            )
        except Exception as exc:
            await self.repository.mark_error(
                tenant_id=tenant_id, connection_id=connection_id,
                error=f"downstream cleanup failed: {type(exc).__name__}",
            )
            raise

        if cleanup_error is not None:
            await self.repository.mark_error(
                tenant_id=tenant_id, connection_id=connection_id, error=cleanup_error,
            )
            if schedule_retry and self.sync_queue is not None:
                await self.sync_queue.enqueue(
                    tenant_id=tenant_id, connection_id=connection_id,
                    job_type="revoke_cleanup", disposition=revocation_disposition,
                )
        else:
            await self.repository.clear_error(
                tenant_id=tenant_id, connection_id=connection_id,
            )
        return True

    async def process_cleanup(self, job: ConnectorCleanupJob) -> None:
        queue = self.cleanup_queue
        if queue is None:
            raise ConnectorError("connector cleanup queue is not configured")
        if job.tenant_id == "" or job.disposition not in {"retain", "delete"}:
            raise ConnectorError("invalid connector cleanup job")
        connection = await self.repository.get(job.tenant_id, job.connection_id)
        if connection is None or connection.status != "revoked":
            raise ConnectorError("cleanup requires a locally revoked connection")
        if not job.remote_revoked:
            provider = self.providers.get(connection.provider)
            if provider is None:
                raise ConnectorError("connector provider is not registered")
            credential = await self._resolve_provider_credential(connection, provider)
            await provider.revoke(credential=credential)
            await queue.mark_remote_revoked(job)
        if not job.sink_cleaned:
            await self.sink.revoke_connection(
                tenant_id=job.tenant_id, connection_id=job.connection_id,
                disposition=job.disposition,
            )
            await queue.mark_sink_cleaned(job)
        await queue.complete(job)
