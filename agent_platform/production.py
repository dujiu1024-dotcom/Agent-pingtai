"""Repository-owned production ASGI composition root.

Import as `agent_platform.production:app`. Configuration is validated during
import so an incorrectly configured process never becomes a healthy API.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from uuid import UUID

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from agent_platform.api.app import create_platform_app
from agent_platform.api.graph import GraphAccessScope
from agent_platform.api.office import OfficeStateService, create_office_router
from agent_platform.api.scheduler import create_scheduler_router
from agent_platform.approval.policy import RiskLevel
from agent_platform.approval.postgres import PostgresAuditReader
from agent_platform.approval.service import ApprovalService, PostgresApprovalRepository
from agent_platform.batch.executor import BatchTemplate
from agent_platform.observability.alert_store import PostgresAlertEventStore
from agent_platform.batch.service import (
    BatchJobRunner,
    PostgresBatchRepository,
    PostgresBatchService,
    await_task_terminal,
    recover_interrupted_batches,
)
from agent_platform.blackboard.postgres_repository import PostgresBlackboardRepository
from agent_platform.blackboard.replay import PostgresBlackboardEventReplay
from agent_platform.contracts import Scope
from agent_platform.connectors.providers import build_default_providers
from agent_platform.control_plane.data_connectors import DataConnectorService
from agent_platform.control_plane.postgres import (
    PostgresRegistryRepository,
    PostgresSessionRepository,
)
from agent_platform.control_plane.postgres_connectors import (
    PostgresConnectorRepository,
    PostgresConnectorSink,
)
from agent_platform.control_plane.registry import RegistryService
from agent_platform.control_plane.sessions_assets import SessionAssetService
from agent_platform.events.admin import WebhookSubscriptionService
from agent_platform.events.outbox import OutboxRelay, PostgresOutboxStore
from agent_platform.events.outbox_worker import OutboxRelayWorker
from agent_platform.events.redis_streams import RedisStreamsTransport
from agent_platform.events.webhook_replay import WebhookDeadLetterReplayService
from agent_platform.events.webhook_worker import build_production_webhook_delivery_worker
from agent_platform.events.webhooks import PostgresWebhookStore, WebhookPayload
from agent_platform.extensions.postgres import PostgresTrustedPublisherKeyRepository
from agent_platform.extensions.publishers import TrustedPublisherKeyRegistry
from agent_platform.graph.governance import GraphGovernanceService
from agent_platform.graph.postgres_governance import PostgresGraphGovernanceRepository
from agent_platform.graph.postgres_repository import PostgresGraphRepository
from agent_platform.graph.query import GraphQueryService
from agent_platform.graph.schema import default_graph_schema
from agent_platform.identity.models import Principal, ResourceRef
from agent_platform.identity.postgres import PostgresIdentityRepository
from agent_platform.i18n.preferences import (
    PostgresLocalePreferenceStore,
    TenantLocalePreferenceService,
)
from agent_platform.identity.service import AuthorizationDenied, IdentityService
from agent_platform.knowledge.ollama import (
    OllamaChatGenerator,
    OllamaEmbedder,
    ollama_config_from_env,
    ollama_embedding_config_from_env,
)
from agent_platform.knowledge.postgres_repository import PostgresKnowledgeRepository
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.postgres_repository import PostgresMemoryRepository
from agent_platform.memory.service import MemoryService
from agent_platform.observability.evaluation_store import PostgresEvaluationRepository
from agent_platform.observability.operational_health import OperationalHealthMonitor
from agent_platform.observability.prometheus import PrometheusClientMetricsRecorder
from agent_platform.observability.prompt_store import PostgresPromptRegistry
from agent_platform.observability.run_trace import PostgresRunTraceRepository
from agent_platform.production_config import ProductionSettings, load_production_settings
from agent_platform.quotas.concurrency import QuotaReservationMaintenanceWorker
from agent_platform.quotas.policies import PostgresQuotaPolicyRepository, QuotaPolicyService
from agent_platform.quotas.postgres import PostgresQuotaStore
from agent_platform.quotas.service import Budget, QuotaService
from agent_platform.runtime.interventions import RunInterventionService
from agent_platform.runtime.lifecycle import RunLifecycleManager
from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore
from agent_platform.runtime.replay import RunReplayService
from agent_platform.tools.idempotency import (
    PostgresToolEffectStore,
    ToolEffectReconciliationService,
)
from agent_platform.runtime.run_context import (
    PostgresRunContextSnapshotStore,
    RunContextSnapshotService,
)
from agent_platform.runtime.production_composition import (
    ProductionRunExecution,
    load_trusted_run_execution_builder,
)
from agent_platform.scheduling.feishu import FeishuNotifier, load_feishu_config
from agent_platform.scheduling.service import PostgresSchedulerService
from agent_platform.security.audit import PostgresAuditWriter, PostgresIdentityAuditReader
from agent_platform.security.local_store import LocalSecretStore
from agent_platform.web.board_page import create_board_page_router
from agent_platform.web.console_pages import create_console_pages_router


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
    run_execution_builder: Callable[..., ProductionRunExecution] | None = None,
    session_storage: Any | None = None,
    local_owner_email: str | None = None,
    local_tenant_id: str | None = None,
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
    # 知识库入库与检索统一使用本地 bge-m3 向量，不再依赖外部 embedding 服务。
    knowledge_embedder = OllamaEmbedder(**ollama_embedding_config_from_env())
    knowledge_service = KnowledgeService(
        PostgresKnowledgeRepository(
            connection_factory,
            embedding_model=knowledge_embedder.storage_key,
            embedding_model_version=knowledge_embedder.model_version,
            embedding_dimensions=knowledge_embedder.dimensions,
        ),
        embedder=knowledge_embedder,
    )
    memory_service = MemoryService(PostgresMemoryRepository(connection_factory))
    run_store = PostgresRunStateStore(connection_factory)
    run_lifecycle = RunLifecycleManager(run_store)
    run_trace_repository = PostgresRunTraceRepository(connection_factory=connection_factory)

    # 回放需要同一租户下共享的上下文快照服务；快照在 Run 开始执行前写入。
    run_context_services: dict[str, RunContextSnapshotService] = {}

    def run_context_service_for(tenant_id: str) -> RunContextSnapshotService:
        service = run_context_services.get(tenant_id)
        if service is None:
            service = RunContextSnapshotService(
                tenant_id=tenant_id,
                store=PostgresRunContextSnapshotStore(
                    connection_factory=connection_factory,
                ),
            )
            run_context_services[tenant_id] = service
        return service

    async def resolve_run_context_service(tenant_id: str) -> RunContextSnapshotService:
        return run_context_service_for(tenant_id)

    locale_preference_service = TenantLocalePreferenceService(
        store=PostgresLocalePreferenceStore(connection_factory),
    )


    # Quota decisions and usage reporting must share the same transactional
    # PostgreSQL store across API processes. Never fall back to in-memory state.
    quota_store = PostgresQuotaStore(connection_factory)
    quota_service = QuotaService(quota_store)
    quota_policy_repository = PostgresQuotaPolicyRepository(connection_factory)
    quota_policy_service = QuotaPolicyService(quota_policy_repository)
    quota_reservation_worker = QuotaReservationMaintenanceWorker(quota_service)
    audit_writer = PostgresAuditWriter(connection_factory)
    audit_reader = PostgresAuditReader(connection_factory)
    identity_audit_reader = PostgresIdentityAuditReader(connection_factory)
    quota_audit_writer = _QuotaAuditAdapter(audit_writer)
    prompt_registry = PostgresPromptRegistry(connection_factory)
    evaluation_repository = PostgresEvaluationRepository(connection_factory)
    publisher_key_registry = TrustedPublisherKeyRegistry(
        PostgresTrustedPublisherKeyRepository(connection_factory)
    )
    metrics_exporter = PrometheusClientMetricsRecorder()

    # ------------------------------------------------------------------ observability wiring
    import os as _os
    import shutil as _shutil

    class _InMemoryAlertStore:
        def __init__(self) -> None:
            self._open: dict[tuple[str, str], dict[str, Any]] = {}

        async def list_open(self, *, scope_key: str, limit: int = 100) -> list[dict[str, Any]]:
            rows = [v for (sk, _), v in self._open.items() if sk == scope_key]
            rows.sort(key=lambda r: r.get("occurred_at", ""), reverse=True)
            return rows[:limit]

        async def acknowledge(self, event_id: str, *, scope_key: str, actor_id: str, now: datetime) -> bool:
            key = (scope_key, event_id)
            row = self._open.get(key)
            if row is None:
                return False
            row["status"] = "acknowledged"
            row["acknowledged_by"] = actor_id
            row["acknowledged_at"] = now.isoformat()
            self._open.pop(key, None)
            return True

        async def resolve(self, event_id: str, *, scope_key: str, actor_id: str, now: datetime) -> bool:
            key = (scope_key, event_id)
            row = self._open.get(key)
            if row is None:
                return False
            row["status"] = "resolved"
            row["resolved_by"] = actor_id
            row["resolved_at"] = now.isoformat()
            self._open.pop(key, None)
            return True

    alert_store: Any = PostgresAlertEventStore(connection_factory)

    def _probe_fs_disk(path: str = ".") -> dict[str, float]:
        try:
            usage = _shutil.disk_usage(path)
            return {"used": float(usage.used), "capacity": float(usage.total)}
        except Exception:
            return {"used": 0.0, "capacity": 1.0}

    def _probe_cpu() -> dict[str, float]:
        try:
            load1 = float(_os.sysconf(_os.sysconf_names.get("SC_LOADAVG", 0))) if "SC_LOADAVG" in _os.sysconf_names else 0.0
            nproc = float(_os.cpu_count() or 1)
            return {"used": min(load1, nproc), "capacity": nproc}
        except Exception:
            return {"used": 0.0, "capacity": 1.0}

    def _probe_uptime() -> dict[str, float]:
        try:
            stat = _os.statvfs("/") if hasattr(_os, "statvfs") else None
            if stat is None:
                return {"used": 0.0, "capacity": 1.0}
            return {"used": stat.f_blocks - stat.f_bavail, "capacity": stat.f_blocks}
        except Exception:
            return {"used": 0.0, "capacity": 1.0}

    operational_health_monitor = OperationalHealthMonitor(
        capacity_probes={
            "disk": lambda: _probe_fs_disk(),
            "cpu_load": lambda: _probe_cpu(),
        },
        certificate_expiry_probes={},
    )
    # -----------------------------------------------------------------------------------------

    async def authorize_eval_manage(principal: Any) -> bool:
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key="knowledge.manage",
            )
        except AuthorizationDenied:
            return False
        return True

    async def authorize_eval_review(principal: Any) -> bool:
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key="knowledge.read",
            )
        except AuthorizationDenied:
            return False
        return True

    async def resolve_registry(principal: Any) -> RegistryService:
        return RegistryService(PostgresRegistryRepository(
            tenant_id=principal.tenant_id, connection_factory=connection_factory,
        ))

    async def authorize_registry(
        principal: Any, _kind: str, _item_id: str, action: str,
    ) -> bool:
        permission_key = "agent.manage" if action in {"write", "publish"} else "agent.run"
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key=permission_key,
            )
        except AuthorizationDenied:
            return False
        return True

    if session_storage is not None:
        async def resolve_sessions(principal: Any) -> SessionAssetService:
            return SessionAssetService(
                PostgresSessionRepository(
                    tenant_id=principal.tenant_id, connection_factory=connection_factory,
                ),
                session_storage,
            )

        async def authorize_session(
            principal: Any, resource_type: str, resource_id: str, action: str,
        ) -> bool:
            permission_key = "project.read" if action == "read" else "project.write"
            try:
                await identity_service.authorize(
                    principal=principal,
                    resource=ResourceRef(resource_type, resource_id),
                    permission_key=permission_key,
                )
            except AuthorizationDenied:
                return False
            return True
    else:
        resolve_sessions = None
        authorize_session = None

    approval_service = ApprovalService(PostgresApprovalRepository(connection_factory))

    async def authorize_approval(principal: Any, _action: str, _resource: str) -> bool:
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key="tool.execute",
            )
        except AuthorizationDenied:
            return False
        return True

    def resolve_approval_risk(_actor: Any, _action: str, _resource: str) -> RiskLevel:
        # Fail-safe default: human approval is treated as the highest risk tier.
        return RiskLevel.EXTERNAL_OR_IRREVERSIBLE

    local_principal_resolver = None
    if local_owner_email:
        # Loopback-only single-user mode: no password login, but every
        # authorization check still runs against the bootstrapped owner.
        owner_email = IdentityService.normalize_email(local_owner_email)
        owner_tenant = (local_tenant_id or "fde").strip()

        async def resolve_local_principal() -> Principal:
            user = await identity_repository.get_user_by_email(owner_email)
            if user is None or user.status != "active":
                raise RuntimeError("local owner account is not bootstrapped")
            if not await identity_repository.is_active_member(
                tenant_id=owner_tenant, user_id=user.user_id,
            ):
                raise RuntimeError("local owner is not an active member of the tenant")
            return Principal(
                user_id=user.user_id, tenant_id=owner_tenant, session_id="local-owner",
                email=user.email, display_name=user.display_name,
            )

        local_principal_resolver = resolve_local_principal

    run_execution = None
    if run_execution_builder is not None:
        if not callable(run_execution_builder):
            raise ValueError("run_execution_builder must be a trusted callable")
        run_execution = run_execution_builder(
            run_store=run_store,
            lifecycle=run_lifecycle,
            quota_store=quota_store,
            quota_service=quota_service,
            identity_service=identity_service,
            identity_repository=identity_repository,
            audit_writer=audit_writer,
            connection_factory=connection_factory,
            redis_client=redis_client,
            settings=configured,
            run_context_resolver=resolve_run_context_service,
        )
        if not isinstance(run_execution, ProductionRunExecution):
            raise TypeError("run_execution_builder must return ProductionRunExecution")
        if run_execution.run_store is not run_store:
            raise ValueError("production Run execution must share the API PostgreSQL RunStateStore")

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

    async def authorize_quota_default_policy(principal: Any) -> bool:
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key="quota.manage",
            )
        except AuthorizationDenied:
            return False
        return True

    async def authorize_quota_policy_scope(principal: Any, scope_type: str, scope_id: str) -> bool:
        resource_type = {
            "project": "project",
            "subject": "agent",
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

    async def authorize_audit_read(principal: Any) -> bool:
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key="audit.read",
            )
        except AuthorizationDenied:
            return False
        return True

    async def authorize_prompt_management(
        principal: Any, _prompt_id: str, action: str,
    ) -> bool:
        permission_key = "knowledge.read" if action in {"read", "read_audit"} else "knowledge.manage"
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key=permission_key,
            )
        except AuthorizationDenied:
            return False
        return True

    async def authorize_publisher_key(
        principal: Any, _extension_id: str, _action: str,
    ) -> bool:
        # 发布者信任根属于租户级管理操作，沿用 tenant.manage 权限。
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key="tenant.manage",
            )
        except AuthorizationDenied:
            return False
        return True

    async def authorize_locale_manage(principal: Any) -> bool:
        # 语言与地区偏好按租户生效，属于租户级管理操作。
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key="tenant.manage",
            )
        except AuthorizationDenied:
            return False
        return True

    # 回放会话保存在服务内存中，必须全进程共享同一个实例。
    run_replay_service = RunReplayService(
        lifecycle_store=run_store,
        trace_repository=run_trace_repository,
        resolve_context_service=resolve_run_context_service,
    )

    async def resolve_run_replay(principal: Any) -> RunReplayService:
        return run_replay_service

    async def authorize_blackboard_scope(
        principal: Any, scope: Any, scope_id: str, action: str,
    ) -> bool:
        # 项目范围按项目资源鉴权，其余范围（租户/任务/Run）按租户授权收敛。
        resource = (
            ResourceRef("project", scope_id)
            if scope is Scope.PROJECT else ResourceRef("tenant", principal.tenant_id)
        )
        permission_key = "knowledge.read" if action == "read" else "knowledge.manage"
        try:
            await identity_service.authorize(
                principal=principal, resource=resource, permission_key=permission_key,
            )
        except AuthorizationDenied:
            return False
        return True

    async def resolve_blackboard_repository(principal: Any) -> Any:
        return PostgresBlackboardRepository(
            tenant_id=principal.tenant_id, connection_factory=connection_factory,
        )

    async def resolve_blackboard_event_replay(principal: Any) -> Any:
        return PostgresBlackboardEventReplay(
            tenant_id=principal.tenant_id, connection_factory=connection_factory,
        )

    async def authorize_graph_read(principal: Any) -> GraphAccessScope:
        # 图谱读取沿用知识域 RBAC：knowledge.read 可读项目图谱，
        # knowledge.manage 额外允许租户级（无项目归属）实体。
        context = await identity_repository.get_authorization_context(
            tenant_id=principal.tenant_id, user_id=principal.user_id,
        )
        if context is None:
            return GraphAccessScope()
        permissions = context.tenant_permissions
        if not ({"knowledge.read", "knowledge.manage"} & permissions):
            return GraphAccessScope()
        return GraphAccessScope(
            project_ids=tuple(sorted(context.project_ids)),
            allow_tenant_scope="knowledge.manage" in permissions,
        )

    async def resolve_graph_query_service(principal: Any) -> GraphQueryService:
        repository = PostgresGraphRepository(
            tenant_id=principal.tenant_id, connection_factory=connection_factory,
        )
        return GraphQueryService(tenant_id=principal.tenant_id, repository=repository)

    async def resolve_graph_governance(principal: Any) -> GraphGovernanceService:
        repository = PostgresGraphGovernanceRepository(
            tenant_id=principal.tenant_id, connection_factory=connection_factory,
        )
        return GraphGovernanceService(repository, schema_resolver=default_graph_schema)

    # 本地 RAG：检索仍由知识库完成，生成交给按需启动、短时保活的 Ollama。
    rag_service = RAGService(
        knowledge=knowledge_service,
        generator=OllamaChatGenerator(**ollama_config_from_env()),
    )

    async def resolve_rag_playground(
        _principal: Any, _model: str, _rerank: bool,
    ) -> RAGService:
        # 本地部署只有一套本地生成器，模型选择只影响检索配置展示。
        return rag_service

    batch_jobs = BatchJobRunner() if run_execution is not None else None

    async def resolve_batch_service(principal: Any) -> PostgresBatchService:
        # 批次模板语义：每行数据 → 一个工作台 Agent 任务（agent_id + input）。
        task_service = await run_execution.task_service_resolver(principal)

        def template_resolver(template_id: str) -> BatchTemplate:
            if template_id != "agent-task":
                raise ValueError(f"unknown batch template: {template_id}")

            async def run_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
                agent_id = str(row.get("agent_id") or "").strip()
                task_input = row.get("input")
                if not agent_id:
                    raise ValueError("dataset row requires agent_id")
                try:
                    UUID(agent_id)
                except (ValueError, TypeError) as exc:
                    raise ValueError("dataset row agent_id must be a UUID") from exc
                if not isinstance(task_input, Mapping) or not task_input:
                    raise ValueError("dataset row requires a non-empty input object")
                submitted = await task_service.submit_task(
                    tenant_id=principal.tenant_id, actor_id=principal.user_id,
                    agent_id=agent_id,
                    project_id=(str(row["project_id"]) if row.get("project_id") else None),
                    session_id=(str(row["session_id"]) if row.get("session_id") else None),
                    input=dict(task_input),
                )
                terminal = await await_task_terminal(
                    task_service, tenant_id=principal.tenant_id,
                    actor_id=principal.user_id, task_id=str(submitted["task_id"]),
                    timeout_seconds=280.0,
                )
                if terminal.get("status") != "succeeded":
                    raise RuntimeError(
                        f"task ended with {terminal.get('status')}: "
                        f"{terminal.get('last_error') or 'no error detail'}"
                    )
                return terminal

            return BatchTemplate("agent-task", run_row)

        return PostgresBatchService(
            tenant_id=principal.tenant_id, actor_id=principal.user_id,
            repository=PostgresBatchRepository(
                tenant_id=principal.tenant_id, connection_factory=connection_factory,
            ),
            template_resolver=template_resolver,
            jobs=batch_jobs,
        )

    async def authorize_batch(principal: Any, _batch_id: str, action: str) -> bool:
        # 读取沿用项目只读权限；提交/取消/重试按 Agent 执行权限收敛。
        permission_key = "project.read" if action == "read" else "agent.run"
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key=permission_key,
            )
        except AuthorizationDenied:
            return False
        return True

    # 内置只读图谱页；授权、租户隔离仍由 /api/platform/v1/graph 负责。
    graph_visualization_url = (
        os.environ.get("GRAPH_VISUALIZATION_URL", "https://127.0.0.1:8000/graph-view").strip()
        or None
    )

    # ---------------------------------------------------- 本地密钥库 / Webhook / 连接器
    local_secret_store = LocalSecretStore(os.environ.get("LOCAL_SECRET_DIR"))
    webhook_store = PostgresWebhookStore(connection_factory)
    webhook_admin_service = WebhookSubscriptionService(
        store=webhook_store, secrets=local_secret_store,
    )

    async def authorize_tenant_manage(principal: Any) -> bool:
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key="tenant.manage",
            )
        except AuthorizationDenied:
            return False
        return True

    async def authorize_webhook_replay(tenant_id: str, actor_id: str) -> bool:
        context = await identity_repository.get_authorization_context(
            tenant_id=tenant_id, user_id=actor_id,
        )
        return bool(context and "tenant.manage" in context.tenant_permissions)

    webhook_replay_service = WebhookDeadLetterReplayService(
        store=webhook_store, audit=audit_writer, authorize=authorize_webhook_replay,
    )
    webhook_delivery_worker = build_production_webhook_delivery_worker(
        store=webhook_store,
        resolve_secret=local_secret_store.resolve_bytes,
        audit_sink=audit_writer,
    )

    # Outbox relay: 保证平台事件（memory.extraction、blackboard 等）不会永远积压在
    # platform_outbox 表。独立进程部署时由 agent_platform.worker 承担；这里把它合
    # 并进 uvicorn lifespan，避免本地/单实例部署出现"有事件没人发"的静默问题。
    outbox_relay_transport = RedisStreamsTransport(configured.redis_url)
    outbox_relay = OutboxRelay(
        store=PostgresOutboxStore(connection_factory=connection_factory),
        transport=outbox_relay_transport,
        audit_sink=audit_writer,
    )
    outbox_relay_worker = OutboxRelayWorker(
        relay=outbox_relay, interval_seconds=1.0, batch_limit=100,
    )

    async def enqueue_webhook(
        tenant_id: str, event_id: str, topic: str, body: Mapping[str, Any],
    ) -> int:
        # 订阅表要求 event_id 是 UUID；用稳定命名空间保证同一事件重复入队可去重。
        from uuid import NAMESPACE_URL, uuid5
        stable_id = str(uuid5(NAMESPACE_URL, f"{tenant_id}:{event_id}"))
        return await webhook_store.enqueue(WebhookPayload(
            tenant_id=tenant_id, event_id=stable_id, topic=topic, body=dict(body),
        ))

    connector_providers = build_default_providers()
    connector_services: dict[str, DataConnectorService] = {}

    def connector_service_for(tenant_id: str) -> DataConnectorService:
        service = connector_services.get(tenant_id)
        if service is None:
            service = DataConnectorService(
                repository=PostgresConnectorRepository(
                    tenant_id=tenant_id, connection_factory=connection_factory,
                ),
                providers=connector_providers,
                secrets=local_secret_store,
                sink=PostgresConnectorSink(
                    tenant_id=tenant_id, connection_factory=connection_factory,
                ),
                secret_writer=local_secret_store,
            )
            connector_services[tenant_id] = service
        return service

    async def resolve_connector_service(principal: Any) -> DataConnectorService:
        return connector_service_for(principal.tenant_id)

    async def authorize_connector(
        principal: Any, _resource_type: str, _connection_id: str, action: str,
    ) -> bool:
        permission_key = "project.read" if action in {"read", "list"} else "tenant.manage"
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef("tenant", principal.tenant_id),
                permission_key=permission_key,
            )
        except AuthorizationDenied:
            return False
        return True

    async def run_connector_sync(
        tenant_id: str, job_id: str,
    ) -> tuple[str, str, dict[str, Any]]:
        service = connector_service_for(tenant_id)
        target = job_id.partition(":")[2] if ":" in job_id else ""
        connections = await service.list_connections(tenant_id=tenant_id)
        selected = [
            connection for connection in connections
            if connection.status == "active" and (not target or connection.connection_id == target)
        ]
        if target and not selected:
            return "failed", f"连接器不存在或未启用：{target}", {"target": target}
        if not selected:
            return "succeeded", "没有启用的数据连接器", {"connections": 0}
        synced = 0
        failures: list[str] = []
        for connection in selected:
            try:
                await service.sync_all_pages(
                    tenant_id=tenant_id, connection_id=connection.connection_id,
                )
                synced += 1
            except Exception as exc:
                # 只保留错误类型，避免把凭据或数据内容写进作业历史。
                failures.append(f"{connection.connection_id}:{type(exc).__name__}")
        detail = {"connections": len(selected), "succeeded": synced, "failed": failures}
        if failures:
            return "failed", f"{synced}/{len(selected)} 个连接器同步成功", detail
        return "succeeded", f"已同步 {synced} 个连接器", detail

    async def postgres_ready() -> bool:
        connection = await connection_factory()
        async with connection:
            await connection.execute("SELECT 1")
        return True

    async def quota_schema_ready() -> bool:
        connection = await connection_factory()
        async with connection:
            cursor = await connection.execute(
                "SELECT to_regclass('public.platform_quota_budgets') IS NOT NULL AS ready"
            )
            row = await cursor.fetchone()
            return bool(row and row["ready"])

    async def _resolve_run_interventions(principal):
        return RunInterventionService(
            tenant_id=principal.tenant_id,
            connection_factory=connection_factory,
        )

    async def _resolve_tool_effects(principal):
        return ToolEffectReconciliationService(
            store=PostgresToolEffectStore(connection_factory=connection_factory),
            authorize_operator=lambda *a, **kw: None,
            audit_sink=None,
        )

    app = create_platform_app(
        identity=identity_service,
        identity_repository=identity_repository,
        knowledge=knowledge_service,
        memory=memory_service,
        redis=redis_client,
        allowed_origins=configured.allowed_origins,
        run_lifecycle_resolver=resolve_run_lifecycle,
        run_authorizer=authorize_run,
        run_intervention_resolver=_resolve_run_interventions,
        tool_effect_reconciliation_resolver=_resolve_tool_effects,
        run_replay_resolver=resolve_run_replay,
        locale_preference_service=locale_preference_service,
        locale_manage_authorizer=authorize_locale_manage,
        registry_service_resolver=resolve_registry,
        registry_authorizer=authorize_registry,
        session_service_resolver=resolve_sessions,
        session_authorizer=authorize_session,
        approval_service=approval_service,
        approval_authorizer=authorize_approval,
        approval_risk_resolver=resolve_approval_risk,
        local_principal_resolver=local_principal_resolver,
        run_executor_resolver=(run_execution.run_executor_resolver if run_execution else None),
        task_service_resolver=(run_execution.task_service_resolver if run_execution else None),
        task_authorizer=(run_execution.task_authorizer if run_execution else None),
        task_worker=(run_execution.task_worker if run_execution else None),
        rag=rag_service,
        rag_playground_resolver=resolve_rag_playground,
        rag_playground_embedding_models=("bge-m3",),
        batch_service_resolver=(resolve_batch_service if run_execution else None),
        batch_authorizer=(authorize_batch if run_execution else None),
        graph_visualization_url=graph_visualization_url,
        quota_reporting_store=quota_store,
        quota_service=quota_service,
        quota_manage_authorizer=authorize_quota_manage,
        quota_scope_authorizer=authorize_quota_scope,
        quota_audit_writer=quota_audit_writer,
        quota_reservation_worker=quota_reservation_worker,
        quota_policy_service=quota_policy_service,
        quota_default_policy_authorizer=authorize_quota_default_policy,
        quota_policy_scope_authorizer=authorize_quota_policy_scope,
        connector_service_resolver=resolve_connector_service,
        connector_authorizer=authorize_connector,
        webhook_admin_service=webhook_admin_service,
        webhook_admin_authorizer=authorize_tenant_manage,
        webhook_delivery_store=webhook_store,
        webhook_delivery_authorizer=authorize_tenant_manage,
        webhook_delivery_replay_service=webhook_replay_service,
        webhook_delivery_worker=webhook_delivery_worker,
        outbox_relay_worker=outbox_relay_worker,
        blackboard_repository_resolver=resolve_blackboard_repository,
        blackboard_authorizer=authorize_blackboard_scope,
        blackboard_event_replay_resolver=resolve_blackboard_event_replay,
        graph_query_service_resolver=resolve_graph_query_service,
        graph_read_authorizer=authorize_graph_read,
        graph_governance_resolver=resolve_graph_governance,
        metrics_exporter=metrics_exporter,
        operational_health_monitor=operational_health_monitor,
        alert_store=alert_store,
        audit_reader=audit_reader,
        identity_audit_reader=identity_audit_reader,
        audit_authorizer=authorize_audit_read,
        prompt_registry=prompt_registry,
        prompt_manage_authorizer=authorize_prompt_management,
        evaluation_repository=evaluation_repository,
        evaluation_manage_authorizer=authorize_eval_manage,
        evaluation_review_authorizer=authorize_eval_review,
        publisher_key_registry=publisher_key_registry,
        publisher_key_authorizer=authorize_publisher_key,
        resource_audit_writer=audit_writer,
        connection_factory=connection_factory,
        readiness_checks={
            "postgres": postgres_ready,
            "redis": redis_client.ping,
            "quota_schema": quota_schema_ready,
        },
        # 这些路由在 create_platform_app 返回后单独挂载，
        # 不能被 auto_capabilities 推断，显式告知 bootstrap。
        bootstrap_capabilities=(
            "scheduler", "board", "console",
        ),
    )
    app.state.production_settings = configured
    app.state.redis_client = redis_client
    app.state.identity_repository = identity_repository
    app.state.knowledge_service = knowledge_service
    app.state.memory_service = memory_service
    app.state.run_state_store = run_store
    app.state.run_lifecycle = run_lifecycle
    app.state.run_execution = run_execution
    app.state.quota_store = quota_store
    app.state.quota_service = quota_service
    app.state.quota_policy_repository = quota_policy_repository
    app.state.quota_policy_service = quota_policy_service
    app.state.quota_reservation_worker = quota_reservation_worker
    app.state.quota_audit_writer = quota_audit_writer

    async def ensure_run_trace_schema() -> None:
        # 回放依赖持久化 trace 事件；本地部署首次启动时幂等建表。
        await run_trace_repository.ensure_schema()

    app.router.on_startup.append(ensure_run_trace_schema)

    # 启动 RunExecutionWorker——没有它 queued Runs 永远不会被 claim
    async def start_run_worker() -> None:
        enabled_value = os.environ.get("AGENT_PLATFORM_RUN_WORKER_ENABLED", "true").strip().lower()
        if enabled_value not in {"true", "false", "1", "0", "yes", "no"}:
            raise RuntimeError("AGENT_PLATFORM_RUN_WORKER_ENABLED must be a boolean")
        if enabled_value not in {"true", "1", "yes"}:
            return
        task_worker = getattr(run_execution, "task_worker", None)
        if task_worker is not None:
            await task_worker.start()
            import logging
            logging.getLogger(__name__).info(
                "RunExecutionWorker started (worker_id=%s)", getattr(task_worker, "worker_id", "?")
            )

    async def stop_run_worker() -> None:
        task_worker = getattr(run_execution, "task_worker", None)
        if task_worker is not None:
            await task_worker.stop()

    app.router.on_startup.append(start_run_worker)
    app.router.on_shutdown.append(stop_run_worker)

    if close_redis_on_shutdown:
        async def close_production_resources() -> None:
            await redis_client.aclose()
        app.router.on_shutdown.append(close_production_resources)

    if batch_jobs is not None:
        async def recover_batch_jobs() -> None:
            # 单实例本地部署：启动时把上次进程遗留的作业收敛为 interrupted。
            await recover_interrupted_batches(connection_factory)

        app.router.on_startup.append(recover_batch_jobs)
        app.router.on_shutdown.append(batch_jobs.shutdown)

    # 旧系统的 tick/due_scan/daily_brief 等定时作业与飞书通知在新平台承接。
    # SCHEDULER_ENABLED=false 时只保留管理接口，不启动后台调度循环。
    scheduler_service = PostgresSchedulerService(
        connection_factory, notifier=FeishuNotifier(load_feishu_config()),
        connector_sync=run_connector_sync,
        webhook_enqueue=enqueue_webhook,
    )
    app.include_router(create_scheduler_router(service=scheduler_service))
    app.state.scheduler_service = scheduler_service

    # 3D 办公室页面保留，但数据改由新平台 PostgreSQL 提供（只读）。
    app.include_router(create_office_router(service=OfficeStateService(connection_factory)))
    scheduler_switch = os.environ.get("SCHEDULER_ENABLED", "true").strip().lower()
    if scheduler_switch not in {"false", "0", "no"}:
        app.router.on_startup.append(scheduler_service.start)
        app.router.on_shutdown.append(scheduler_service.stop)

    async def ensure_local_worker_budget() -> None:
        """本地单用户模式补齐 worker_pool/local 并发预算。

        批次扇出与任务 worker 共用同一并发池；缺少预算时两者都会被
        “concurrency budget not configured” 直接拒绝，因此这里只在该本地
        部署首次启动时补一条最长有效期的并发预算。
        """
        if not local_owner_email:
            return
        tenant_id = (local_tenant_id or "fde").strip()
        now = datetime.now(timezone.utc)
        if await quota_store.get_budget(tenant_id, "worker_pool", "local", now) is not None:
            return
        await quota_store.put_budget(Budget(
            tenant_id=tenant_id, scope_type="worker_pool", scope_id="local",
            period_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            period_end=datetime(2100, 1, 1, tzinfo=timezone.utc),
            hard_concurrency=4,
        ))

    app.router.on_startup.append(ensure_local_worker_budget)

    async def ensure_default_quota_policies() -> None:
        """本地单用户模式补齐默认 quota policy。

        quota_policies 为空时 /quotas/policies/effective 返回空 limits，
        前端看板和运行时 budget 决策都会受限。此处幂等写入 default scope +
        本地 tenant 各一条宽松策略，线上部署由运维显式配置策略，不走此
        本地兜底逻辑。
        """
        if not local_owner_email:
            return
        tenant_id = (local_tenant_id or "fde").strip()
        conn = await connection_factory()
        try:
            async with conn:
                await conn.execute(
                    """INSERT INTO platform_quota_policies
                       (policy_id, tenant_id, scope_type, scope_id, limits,
                        priority, enabled, version, created_by)
                       VALUES
                        ('default-tenant-limits','','default','*',
                         jsonb_build_object(
                           'tokens',100000000,'cost_usd',1000,'concurrency',16,
                           'requests',1000000,'tool_calls',5000000),
                         100, true, 1, 'system'),
                        (%s,%s,'tenant',%s,
                         jsonb_build_object(
                           'tokens',500000000,'cost_usd',5000,'concurrency',32,
                           'requests',5000000,'tool_calls',20000000),
                         50, true, 1, 'system')
                       ON CONFLICT (scope_type, tenant_id, scope_id, policy_id)
                       DO NOTHING""",
                    (f"{tenant_id}-tenant-limits", tenant_id, tenant_id),
                )
        finally:
            await conn.close()

    app.router.on_startup.append(ensure_default_quota_policies)

    # 运营看板由平台自身提供；旧系统页面与接口不再挂载。
    app.include_router(create_board_page_router())
    app.include_router(create_console_pages_router())

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/app", status_code=307)

    return app


def _production_app() -> FastAPI:
    required_value = os.environ.get("AGENT_PLATFORM_REQUIRE_RUN_EXECUTION", "false").strip().lower()
    if required_value not in {"true", "false", "1", "0", "yes", "no"}:
        raise RuntimeError("AGENT_PLATFORM_REQUIRE_RUN_EXECUTION must be a boolean")
    required = required_value in {"true", "1", "yes"}
    builder = load_trusted_run_execution_builder(required=False)
    if builder is None:
        # Dev fallback: entry point not registered, try importing deploy package directly.
        try:
            from deploy.local_runtime import build_run_execution as _fallback
            builder = _fallback
        except ImportError:
            if required:
                raise RuntimeError(
                    "AGENT_PLATFORM_REQUIRE_RUN_EXECUTION=true but neither entry point nor "
                    "deploy.local_runtime.build_run_execution is available"
                )
    return create_production_app(run_execution_builder=builder)


app = _production_app()
