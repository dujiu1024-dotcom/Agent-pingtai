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
from agent_platform.api.scheduler import create_scheduler_router
from agent_platform.api.office import OfficeStateService, create_office_router
from agent_platform.batch.executor import BatchTemplate
from agent_platform.batch.service import (
    BatchJobRunner,
    PostgresBatchRepository,
    PostgresBatchService,
    await_task_terminal,
    recover_interrupted_batches,
)
from agent_platform.approval.policy import RiskLevel
from agent_platform.approval.postgres import PostgresAuditReader
from agent_platform.approval.service import ApprovalService, PostgresApprovalRepository
from agent_platform.blackboard.postgres_repository import PostgresBlackboardRepository
from agent_platform.blackboard.replay import PostgresBlackboardEventReplay
from agent_platform.contracts import Scope
from agent_platform.control_plane.postgres import PostgresRegistryRepository, PostgresSessionRepository
from agent_platform.control_plane.registry import RegistryService
from agent_platform.control_plane.sessions_assets import SessionAssetService
from agent_platform.graph.governance import GraphGovernanceService
from agent_platform.graph.postgres_governance import PostgresGraphGovernanceRepository
from agent_platform.graph.postgres_repository import PostgresGraphRepository
from agent_platform.graph.query import GraphQueryService
from agent_platform.graph.schema import default_graph_schema
from agent_platform.identity.models import Principal, ResourceRef
from agent_platform.identity.postgres import PostgresIdentityRepository
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
from agent_platform.observability.prompt_store import PostgresPromptRegistry
from agent_platform.observability.evaluation_store import PostgresEvaluationRepository
from agent_platform.observability.prometheus import PrometheusClientMetricsRecorder
from agent_platform.production_config import ProductionSettings, load_production_settings
from agent_platform.runtime.lifecycle import RunLifecycleManager
from agent_platform.runtime.postgres_lifecycle import PostgresRunStateStore
from agent_platform.runtime.production_composition import (
    ProductionRunExecution,
    load_trusted_run_execution_builder,
)
from agent_platform.quotas.concurrency import QuotaReservationMaintenanceWorker
from agent_platform.quotas.policies import PostgresQuotaPolicyRepository, QuotaPolicyService
from agent_platform.quotas.postgres import PostgresQuotaStore
from agent_platform.quotas.service import Budget, QuotaService
from agent_platform.security.audit import PostgresAuditWriter, PostgresIdentityAuditReader
from agent_platform.scheduling.feishu import FeishuNotifier, load_feishu_config
from agent_platform.scheduling.service import PostgresSchedulerService


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

    # Quota decisions and usage reporting must share the same transactional
    # PostgreSQL store across API processes. Never fall back to in-memory state.
    quota_store = PostgresQuotaStore(connection_factory)
    quota_service = QuotaService(quota_store)
    quota_policy_repository = PostgresQuotaPolicyRepository(connection_factory)
    quota_policy_service = QuotaPolicyService(quota_policy_repository)
    quota_maintenance_worker = QuotaReservationMaintenanceWorker(quota_service)
    audit_writer = PostgresAuditWriter(connection_factory)
    audit_reader = PostgresAuditReader(connection_factory)
    identity_audit_reader = PostgresIdentityAuditReader(connection_factory)
    quota_audit_writer = _QuotaAuditAdapter(audit_writer)
    prompt_registry = PostgresPromptRegistry(connection_factory)
    evaluation_repository = PostgresEvaluationRepository(connection_factory)
    metrics_exporter = PrometheusClientMetricsRecorder()

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
        quota_reservation_worker=quota_maintenance_worker,
        blackboard_repository_resolver=resolve_blackboard_repository,
        blackboard_authorizer=authorize_blackboard_scope,
        blackboard_event_replay_resolver=resolve_blackboard_event_replay,
        graph_query_service_resolver=resolve_graph_query_service,
        graph_read_authorizer=authorize_graph_read,
        graph_governance_resolver=resolve_graph_governance,
        metrics_exporter=metrics_exporter,
        audit_reader=audit_reader,
        identity_audit_reader=identity_audit_reader,
        audit_authorizer=authorize_audit_read,
        prompt_registry=prompt_registry,
        prompt_manage_authorizer=authorize_prompt_management,
        evaluation_repository=evaluation_repository,
        evaluation_manage_authorizer=authorize_eval_manage,
        evaluation_review_authorizer=authorize_eval_review,
        connection_factory=connection_factory,
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
    app.state.run_execution = run_execution
    app.state.quota_store = quota_store
    app.state.quota_service = quota_service
    app.state.quota_policy_repository = quota_policy_repository
    app.state.quota_policy_service = quota_policy_service
    app.state.quota_reservation_worker = quota_maintenance_worker
    app.state.quota_audit_writer = quota_audit_writer
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

    # 老系统已停用；3D 办公室与运营看板以只读兼容桥的形式继续由新平台提供。
    if configured.legacy_office_enabled:
        from agent_platform.web.legacy_bridge import create_legacy_bridge_router

        app.include_router(create_legacy_bridge_router())

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/app", status_code=307)

    return app


def _production_app() -> FastAPI:
    required_value = os.environ.get("AGENT_PLATFORM_REQUIRE_RUN_EXECUTION", "false").strip().lower()
    if required_value not in {"true", "false", "1", "0", "yes", "no"}:
        raise RuntimeError("AGENT_PLATFORM_REQUIRE_RUN_EXECUTION must be a boolean")
    required = required_value in {"true", "1", "yes"}
    builder = load_trusted_run_execution_builder(required=required)
    return create_production_app(run_execution_builder=builder)


app = _production_app()
