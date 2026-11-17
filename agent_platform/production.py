"""Repository-owned production ASGI composition root.

Import as `agent_platform.production:app`. Configuration is validated during
import so an incorrectly configured process never becomes a healthy API.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI

from agent_platform.api.app import create_platform_app
from agent_platform.identity.models import ResourceRef
from agent_platform.identity.postgres import PostgresIdentityRepository
from agent_platform.identity.service import AuthorizationDenied, IdentityService
from agent_platform.knowledge.postgres_repository import PostgresKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.postgres_repository import PostgresMemoryRepository
from agent_platform.memory.service import MemoryService
from agent_platform.production_config import ProductionSettings, load_production_settings
from agent_platform.runtime.lifecycle import RunLifecycleManager
from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore
from agent_platform.quotas.concurrency import QuotaReservationMaintenanceWorker
from agent_platform.quotas.postgres import PostgresQuotaStore
from agent_platform.quotas.service import QuotaService
from agent_platform.security.audit import PostgresAuditWriter


def _postgres_connection_factory(dsn: str) -> Callable[[], Any]:
    async def connect() -> Any:
        try:
            from psycopg import AsyncConnection
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "production API requires psycopg[binary,pool] from requirements-platform.txt"
            ) from exc
        return await AsyncConnection.connect(dsn, row_factory=dict_row)

    return connect


class _QuotaAuditAdapter:
    """Adapt quota route events to the append-only production audit contract."""

    def __init__(self, writer: PostgresAuditWriter) -> None:
        self._writer = writer

    async def __call__(self, event: dict[str, Any]) -> None:
        await self._writer.record(
            tenant_id=str(event["tenant_id"]),
            actor_id=str(event["actor_id"]),
            action=str(event["action"]),
            subject_id=str(event["resource_id"]),
            detail={
                key: value for key, value in event.items()
                if key not in {"tenant_id", "actor_id", "action", "resource_id"}
            },
        )


def create_production_app(
    *,
    settings: ProductionSettings | None = None,
    connection_factory: Any | None = None,
    redis_client: Any | None = None,
) -> FastAPI:
    """Build the production API graph; optional arguments are test seams only."""
    configured = settings or load_production_settings()
    if connection_factory is None:
        connection_factory = _postgres_connection_factory(configured.postgres_dsn)

    if redis_client is None:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RuntimeError(
                "production API requires redis>=5 from requirements-platform.txt"
            ) from exc
        redis_client = Redis.from_url(configured.redis_url, decode_responses=True)
        close_redis_on_shutdown = True
    else:
        close_redis_on_shutdown = False

    identity_repository = PostgresIdentityRepository(connection_factory)
    identity_service = IdentityService(identity_repository)
    knowledge_service = KnowledgeService(
        PostgresKnowledgeRepository(connection_factory),
    )
    memory_service = MemoryService(PostgresMemoryRepository(connection_factory))
    run_store = PostgresRunStateStore(connection_factory)
    run_lifecycle = RunLifecycleManager(run_store)

    # Quota decisions and usage reporting must share the same transactional
    # PostgreSQL store across API processes. Never fall back to in-memory state.
    quota_store = PostgresQuotaStore(connection_factory)
    quota_service = QuotaService(quota_store)
    quota_maintenance_worker = QuotaReservationMaintenanceWorker(quota_service)
    quota_audit_writer = _QuotaAuditAdapter(PostgresAuditWriter(connection_factory))

    async def resolve_run_lifecycle(_principal: Any) -> RunLifecycleManager:
        return run_lifecycle

    async def authorize_run(
        principal: Any, _resource_type: str, run_id: str, _action: str,
    ) -> bool:
        run_record = await run_store.get_run_record(principal.tenant_id, run_id)
        if run_record is None:
            return False
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("agent", str(run_record["agent_id"])),
                permission_key="agent.run",
            )
        except AuthorizationDenied:
            return False
        return True

    async def authorize_quota_manage(principal: Any) -> bool:
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key="quota.manage",
            )
        except AuthorizationDenied:
            return False
        return True

    async def authorize_quota_scope(principal: Any, scope_type: str, scope_id: str) -> bool:
        resource_type = {
            "project": "project",
            "agent": "agent",
            "run": "run",
            "model": "model",
            "queue": "queue",
            "worker_pool": "worker_pool",
        }.get(scope_type)
        if resource_type is None or not scope_id:
            return False
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef(resource_type, scope_id),
                permission_key="quota.manage",
            )
        except AuthorizationDenied:
            return False
        return True

    async def postgres_ready() -> bool:
        connection = await connection_factory()
        async with connection:
            await connection.execute("SELECT 1")
        return True

    app = create_platform_app(
        identity=identity_service,
        identity_repository=identity_repository,
        knowledge=knowledge_service,
        memory=memory_service,
        redis=redis_client,
        allowed_origins=configured.allowed_origins,
        run_lifecycle_resolver=resolve_run_lifecycle,
        run_authorizer=authorize_run,
        quota_reporting_store=quota_store,
        quota_service=quota_service,
        quota_manage_authorizer=authorize_quota_manage,
        quota_scope_authorizer=authorize_quota_scope,
        quota_audit_writer=quota_audit_writer,
        quota_reservation_worker=quota_maintenance_worker,
        readiness_checks={
            "postgres": postgres_ready,
            "redis": redis_client.ping,
        },
    )
    app.state.production_settings = configured
    app.state.identity_repository = identity_repository
    app.state.knowledge_service = knowledge_service
    app.state.memory_service = memory_service
    app.state.run_state_store = run_store
    app.state.run_lifecycle = run_lifecycle
    app.state.quota_store = quota_store
    app.state.quota_service = quota_service
    app.state.quota_reservation_worker = quota_maintenance_worker
    app.state.quota_audit_writer = quota_audit_writer
    if close_redis_on_shutdown:
        async def close_production_resources() -> None:
            await redis_client.aclose()
        app.router.on_shutdown.append(close_production_resources)
    return app


app = create_production_app()
