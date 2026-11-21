"""Production composition factory for the platform HTTP API."""
from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agent_platform.api.approvals import create_approval_router
from agent_platform.api.audit import create_audit_router
from agent_platform.api.batch import create_batch_router
from agent_platform.api.blackboard import create_blackboard_router
from agent_platform.api.bootstrap import create_bootstrap_router
from agent_platform.api.connectors import create_connectors_router
from agent_platform.api.contract import (
    API_V1_PREFIX,
    ApiV1CompatibilityMiddleware,
    PublicApiContractASGIMiddleware,
    error_for_http_status,
)
from agent_platform.api.evaluation_rollout import create_evaluation_rollout_router
from agent_platform.api.extension_publishers import create_extension_publisher_router
from agent_platform.api.graph import create_graph_router
from agent_platform.api.graph_extraction import create_graph_extraction_router
from agent_platform.api.health import create_health_router
from agent_platform.graph.knowledge_extraction import (
    KnowledgeGraphExtractionService,
    build_extractor_from_env,
)
from agent_platform.api.identity import create_identity_router
from agent_platform.api.identity_context import IdentityContextResolver
from agent_platform.api.knowledge_catalog import create_knowledge_catalog_router
from agent_platform.api.knowledge_memory import create_knowledge_memory_router
from agent_platform.api.knowledge_upload import create_knowledge_upload_router
from agent_platform.api.localization import create_locale_preferences_router
from agent_platform.api.notifications import create_notifications_router
from agent_platform.api.observability import create_observability_router
from agent_platform.api.office import OfficeStateService, create_office_router
from agent_platform.api.prompts import create_prompt_registry_router
from agent_platform.api.quota_admin import create_quota_admin_router
from agent_platform.api.quotas import create_quota_reporting_router
from agent_platform.api.rag_playground import create_rag_playground_router
from agent_platform.api.registry import create_registry_router
from agent_platform.api.replay import create_run_replay_router
from agent_platform.api.runs import create_runs_router
from agent_platform.api.search import create_search_router
from agent_platform.api.sessions import create_sessions_router
from agent_platform.api.value_management import create_value_management_router
from agent_platform.api.webhook_deliveries import create_webhook_delivery_admin_router
from agent_platform.api.webhooks import create_webhook_admin_router
from agent_platform.api.workbench import create_workbench_router
from agent_platform.events.admin import WebhookSubscriptionService
from agent_platform.graph.knowledge_extraction import (
    KnowledgeGraphExtractionService,
    build_extractor_from_env,
)
from agent_platform.identity.http import (
    IdentityASGIMiddleware,
    RedisSlidingWindowRateLimiter,
)
from agent_platform.identity.service import IdentityService
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.extraction import MemoryExtractionService
from agent_platform.memory.service import MemoryService
from agent_platform.observability.trace_middleware import TraceContextASGIMiddleware
from agent_platform.security.audit_http import AuditRequestASGIMiddleware
from agent_platform.web.app import create_platform_web_router


async def _principal_from_request(request: Any) -> Any:
    from agent_platform.identity.models import Principal
    principal = getattr(request.state, "identity.principal", None)
    if not isinstance(principal, Principal):
        from fastapi import HTTPException
        raise HTTPException(401, "authentication required")
    return principal


def create_platform_app(
    *,
    identity: IdentityService,
    identity_repository: Any,
    knowledge: KnowledgeService,
    memory: MemoryService,
    redis: Any,
    allowed_origins: Sequence[str],
    service_identity: Any | None = None,
    rag: RAGService | None = None,
    rag_playground_resolver: Any | None = None,
    rag_playground_embedding_models: Sequence[str] = (),
    memory_extraction: MemoryExtractionService | None = None,
    memory_expiry_worker: Any | None = None,
    registry_service_resolver: Any | None = None,
    session_service_resolver: Any | None = None,
    registry_authorizer: Any | None = None,
    session_authorizer: Any | None = None,
    connector_service_resolver: Any | None = None,
    connector_authorizer: Any | None = None,
    connector_sync_worker: Any | None = None,
    connector_cleanup_worker: Any | None = None,
    run_lifecycle_resolver: Any | None = None,
    run_replay_resolver: Any | None = None,
    run_authorizer: Any | None = None,
    run_intervention_resolver: Any | None = None,
    run_detail_resolver: Any | None = None,
    run_budget_ledger_resolver: Any | None = None,
    run_budget_audit_reader: Any | None = None,
    run_executor_resolver: Any | None = None,
    tool_effect_reconciliation_resolver: Any | None = None,
    approval_service: Any | None = None,
    approval_authorizer: Any | None = None,
    approval_risk_resolver: Any | None = None,
    local_principal_resolver: Any | None = None,
    metrics_exporter: Any | None = None,
    operational_health_monitor: Any | None = None,
    alert_store: Any | None = None,
    alert_manage_authorizer: Any | None = None,
    evaluation_repository: Any | None = None,
    evaluation_dataset_repository: Any | None = None,
    evaluation_run_service: Any | None = None,
    evaluation_comparison_service: Any | None = None,
    evaluation_worker: Any | None = None,
    evaluation_manage_authorizer: Any | None = None,
    evaluation_review_authorizer: Any | None = None,
    connection_factory: Any | None = None,
    evaluation_rollout_bridge: Any | None = None,
    evaluation_rollout_authorizer: Any | None = None,
    prompt_registry: Any | None = None,
    prompt_manage_authorizer: Any | None = None,
    prompt_evaluation_gate: Any | None = None,
    alert_dispatch_worker: Any | None = None,
    alert_evaluation_worker: Any | None = None,
    audit_reader: Any | None = None,
    identity_audit_reader: Any | None = None,
    audit_authorizer: Any | None = None,
    audit_request_sink: Any | None = None,
    audit_actor_resolver: Any | None = None,
    audit_null_actor_tenant_resolver: Any | None = None,
    resource_audit_writer: Any | None = None,
    quota_store: Any | None = None,
    quota_reporting_store: Any | None = None,
    quota_service: Any | None = None,
    quota_reservation_worker: Any | None = None,
    quota_manage_authorizer: Any | None = None,
    quota_scope_authorizer: Any | None = None,
    quota_audit_writer: Any | None = None,
    quota_policy_service: Any | None = None,
    quota_default_policy_authorizer: Any | None = None,
    quota_policy_scope_authorizer: Any | None = None,
    webhook_admin_service: WebhookSubscriptionService | None = None,
    webhook_admin_authorizer: Any | None = None,
    webhook_delivery_store: Any | None = None,
    webhook_delivery_authorizer: Any | None = None,
    webhook_delivery_replay_service: Any | None = None,
    webhook_delivery_worker: Any | None = None,
    outbox_relay_worker: Any | None = None,
    memory_extraction_event_worker: Any | None = None,
    task_service_resolver: Any | None = None,
    task_authorizer: Any | None = None,
    batch_service_resolver: Any | None = None,
    batch_authorizer: Any | None = None,
    task_worker: Any | None = None,
    blackboard_repository_resolver: Any | None = None,
    blackboard_authorizer: Any | None = None,
    blackboard_evidence_verifier: Any | None = None,
    blackboard_event_replay_resolver: Any | None = None,
    graph_query_service_resolver: Any | None = None,
    graph_read_authorizer: Any | None = None,
    graph_governance_resolver: Any | None = None,
    graph_visualization_url: str | None = None,
    readiness_checks: Any | None = None,
    locale_preference_service: Any | None = None,
    locale_manage_authorizer: Any | None = None,
    publisher_key_registry: Any | None = None,
    publisher_key_authorizer: Any | None = None,
    auth_enabled: bool = True,
    office_state_service: OfficeStateService | None = None,
    office_tenant_resolver: Any | None = None,
    bootstrap_capabilities: Sequence[str] | None = None,
    knowledge_catalog: Any | None = None,
    value_management_resolver: Any | None = None,
    value_management_authorizer: Any | None = None,
) -> FastAPI:
    """Assemble auth middleware, identity routes, and tenant-aware business routes.

    A shared Redis limiter and an explicit HTTPS Origin allowlist are mandatory
    so multi-instance deployments do not silently fall back to process-local
    security controls.
    """
    if isinstance(allowed_origins, str):
        raise ValueError("allowed_origins must be a sequence of exact origins")
    origins = tuple(origin.strip() for origin in allowed_origins if origin.strip())
    if not origins:
        raise ValueError("at least one exact HTTPS allowed_origin is required")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in parsed.netloc
        ):
            raise ValueError("allowed_origins must contain exact HTTPS origins only")

    audit_request_configured = any(value is not None for value in (
        audit_request_sink, audit_actor_resolver, audit_null_actor_tenant_resolver,
    ))
    if audit_request_configured and (
        audit_request_sink is None
        or (audit_actor_resolver is None and audit_null_actor_tenant_resolver is None)
        or (auth_enabled and audit_actor_resolver is None)
    ):
        raise ValueError(
            "audit sink and a mode-appropriate actor/instance resolver must be configured"
        )

    app = FastAPI(
        title="Agent Platform API",
        version="1.0.0",
        description="Versioned tenant-scoped API. See docs/api-contract.md for the public contract.",
    )
    app.state.auth_enabled = auth_enabled
    app.state.api_version = API_V1_PREFIX  # "/api/platform/v1"
    app.add_middleware(ApiV1CompatibilityMiddleware)

    @app.exception_handler(HTTPException)
    async def public_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if not request.url.path.startswith(API_V1_PREFIX):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
        error = error_for_http_status(
            exc.status_code,
            "The request was rejected" if exc.status_code < 500 else "The request could not be completed",
            request_id=getattr(request.state, "api.request_id", None),
        )
        return JSONResponse(status_code=exc.status_code, content=error.to_dict(), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def public_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        if not request.url.path.startswith(API_V1_PREFIX):
            return JSONResponse(status_code=422, content={"detail": exc.errors()})
        error = error_for_http_status(
            422, "Request validation failed",
            request_id=getattr(request.state, "api.request_id", None),
        )
        return JSONResponse(status_code=422, content=error.to_dict())
    # Independent first-party UI; no dependency on legacy console/gui.
    app.include_router(create_platform_web_router())
    # Add request auditing before Identity middleware so it executes inside the
    # authenticated identity boundary and sees the verified principal.
    if audit_request_configured:
        app.add_middleware(
            AuditRequestASGIMiddleware,
            sink=audit_request_sink,
            resolve_actor=audit_actor_resolver or (lambda principal: None),
            resolve_null_actor_tenant=audit_null_actor_tenant_resolver,
        )
    app.add_middleware(
        IdentityASGIMiddleware,
        identity=identity,
        service_identity=service_identity,
        allowed_origins=origins,
        protected_paths=(("/api", "/auth") if auth_enabled else ("/auth",)),
        auth_enabled=auth_enabled,
        login_rate_limiter=RedisSlidingWindowRateLimiter(redis),
        local_principal=local_principal_resolver,
    )
    app.add_middleware(TraceContextASGIMiddleware)
    # Outer middleware also covers authentication failures returned before routing.
    app.add_middleware(
        PublicApiContractASGIMiddleware,
        rate_limiter=RedisSlidingWindowRateLimiter(redis, key_prefix="platform:api:rate:"),
        limit=120,
        window_seconds=60,
    )
    app.include_router(create_identity_router(
        identity=identity, connection_factory=connection_factory,
    ))
    auto_capabilities: set[str] = set()
    if office_state_service is not None:
        auto_capabilities.add("office")
    if knowledge is not None:
        auto_capabilities.add("knowledge")
        auto_capabilities.add("knowledge-memory")
        if knowledge_catalog is not None:
            auto_capabilities.add("knowledge-catalog")
    if memory is not None:
        auto_capabilities.add("memory")
    if rag is not None or rag_playground_resolver is not None:
        auto_capabilities.add("rag")
    if any(v is not None for v in (
        registry_service_resolver, session_service_resolver, run_lifecycle_resolver,
        task_service_resolver, blackboard_repository_resolver, graph_query_service_resolver,
    )):
        auto_capabilities.add("workbench")
    if any(v is not None for v in (quota_service, quota_store, quota_reporting_store)):
        auto_capabilities.add("quota")
    if any(v is not None for v in (alert_store, operational_health_monitor)):
        auto_capabilities.add("observability")
    # 以下模块由参数对共同决定是否注册 router
    if approval_service is not None and approval_authorizer is not None:
        auto_capabilities.add("approvals")
    if batch_service_resolver is not None:
        auto_capabilities.add("batch")
    if blackboard_repository_resolver is not None and blackboard_authorizer is not None:
        auto_capabilities.add("blackboard")
    if connector_service_resolver is not None and connector_authorizer is not None:
        auto_capabilities.add("connectors")
    if graph_query_service_resolver is not None and graph_read_authorizer is not None:
        auto_capabilities.add("graph")
    if webhook_admin_service is not None and webhook_admin_authorizer is not None:
        auto_capabilities.add("webhooks")
    if run_lifecycle_resolver is not None and run_authorizer is not None:
        auto_capabilities.add("runs")
    if run_replay_resolver is not None:
        auto_capabilities.add("run-replay")
    if session_service_resolver is not None and session_authorizer is not None:
        auto_capabilities.add("sessions")
    if registry_service_resolver is not None and registry_authorizer is not None:
        auto_capabilities.add("registry")
    if prompt_registry is not None and prompt_manage_authorizer is not None:
        auto_capabilities.add("prompts")
    if evaluation_repository is not None and evaluation_manage_authorizer is not None:
        auto_capabilities.add("evaluation")
    if publisher_key_registry is not None and publisher_key_authorizer is not None:
        auto_capabilities.add("extensions")
    if audit_reader is not None and audit_authorizer is not None:
        auto_capabilities.add("audit")
    if locale_preference_service is not None and locale_manage_authorizer is not None:
        auto_capabilities.add("localization")
    if connection_factory is not None:
        auto_capabilities.add("admin")
        auto_capabilities.add("notifications")
        auto_capabilities.add("search")
    # scheduler、board_page、console_pages 在 production.py 里单独挂载，
    # 不由 create_platform_app 参数控制；在 production.py 里通过
    # bootstrap_capabilities 显式注入。
    final_capabilities = set(bootstrap_capabilities or ()) | auto_capabilities
    app.include_router(create_bootstrap_router(
        auth_enabled=auth_enabled, capabilities=sorted(final_capabilities),
    ))
    if office_state_service is not None:
        app.include_router(create_office_router(
            service=office_state_service, resolve_tenant=office_tenant_resolver,
        ))
    app.include_router(
        create_health_router(
            readiness_checks=(
                readiness_checks
                if readiness_checks is not None
                else ({"redis": redis.ping} if callable(getattr(redis, "ping", None)) else {})
            )
        )
    )
    evaluation_configured = any(value is not None for value in (
        evaluation_repository, evaluation_dataset_repository, evaluation_run_service,
    ))
    if evaluation_configured and evaluation_manage_authorizer is None:
        raise ValueError(
            "evaluation services and evaluation manager authorizer must be configured together"
        )
    if evaluation_comparison_service is not None and evaluation_manage_authorizer is None:
        raise ValueError("evaluation comparison service requires the evaluation manager authorizer")
    observability_api_configured = any(value is not None for value in (
        metrics_exporter, operational_health_monitor, alert_store, evaluation_repository,
        evaluation_dataset_repository, evaluation_run_service, evaluation_comparison_service,
    ))
    evaluation_rollout_configured = any(value is not None for value in (
        evaluation_rollout_bridge, evaluation_rollout_authorizer,
    ))
    if evaluation_rollout_configured:
        if (
            evaluation_rollout_bridge is None or evaluation_rollout_authorizer is None
            or resource_audit_writer is None
        ):
            raise ValueError(
                "evaluation rollout bridge, authorizer and audit writer must be configured together"
            )
        app.include_router(create_evaluation_rollout_router(
            bridge=evaluation_rollout_bridge,
            authorize=evaluation_rollout_authorizer,
            audit_writer=resource_audit_writer,
        ))
    if observability_api_configured:
        app.include_router(create_observability_router(
            metrics=metrics_exporter,
            operational_health_monitor=operational_health_monitor,
            alert_store=alert_store,
            authorize_alert_manage=alert_manage_authorizer,
            evaluation_repository=evaluation_repository,
            evaluation_dataset_repository=evaluation_dataset_repository,
            evaluation_run_service=evaluation_run_service,
            evaluation_comparison_service=evaluation_comparison_service,
            authorize_eval_manage=evaluation_manage_authorizer,
            authorize_eval_review=evaluation_review_authorizer,
            connection_factory=connection_factory,
        ))
    if connection_factory is not None:
        from agent_platform.api.admin import create_admin_router
        app.include_router(create_admin_router(connection_factory=connection_factory))
        app.include_router(create_notifications_router(connection_factory=connection_factory))
        app.include_router(create_search_router(connection_factory=connection_factory))
    prompt_registry_configured = any(value is not None for value in (
        prompt_registry, prompt_manage_authorizer, prompt_evaluation_gate,
    ))
    if prompt_registry_configured:
        if prompt_registry is None or prompt_manage_authorizer is None:
            raise ValueError(
                "prompt registry and prompt manager authorizer must be configured together"
            )
        app.include_router(create_prompt_registry_router(
            registry=prompt_registry, authorize=prompt_manage_authorizer,
            evaluate_gate=prompt_evaluation_gate,
        ))
    app.include_router(create_knowledge_memory_router(
        knowledge=knowledge,
        memory=memory,
        rag=rag,
        memory_extraction=memory_extraction,
        identity_repository=identity_repository,
    ))
    app.include_router(create_knowledge_upload_router(
        knowledge=knowledge,
        resolve_principal=IdentityContextResolver(identity_repository).knowledge,
    ))
    if knowledge_catalog is not None:
        app.include_router(create_knowledge_catalog_router(
            catalog=knowledge_catalog,
            knowledge=knowledge,
            resolve_principal=IdentityContextResolver(identity_repository).knowledge,
        ))
    rag_playground_configured = rag_playground_resolver is not None
    if rag_playground_configured:
        if not rag_playground_embedding_models:
            raise ValueError("RAG playground requires configured embedding model IDs")
        knowledge_principal_resolver = IdentityContextResolver(identity_repository).knowledge
        app.include_router(create_rag_playground_router(
            resolve_principal=knowledge_principal_resolver,
            resolve_rag=rag_playground_resolver,
            embedding_models=rag_playground_embedding_models,
        ))
    elif rag_playground_embedding_models:
        raise ValueError("RAG playground embedding models require a RAG resolver")
    publisher_keys_configured = any(value is not None for value in (
        publisher_key_registry, publisher_key_authorizer,
    ))
    if publisher_keys_configured:
        if publisher_key_registry is None or publisher_key_authorizer is None:
            raise ValueError("publisher key registry and authorizer must be configured together")
        if not callable(getattr(publisher_key_registry, "resolve", None)):
            raise ValueError("publisher key registry must expose resolve() for extension runtime use")
        publisher_audit_writer = resource_audit_writer or audit_request_sink
        if publisher_audit_writer is None:
            raise ValueError("publisher key management requires an append-only audit writer")
        app.state.trusted_publisher_key_resolver = publisher_key_registry
        app.include_router(create_extension_publisher_router(
            registry=publisher_key_registry,
            authorize_manage=publisher_key_authorizer,
            audit_writer=publisher_audit_writer,
        ))
    else:
        app.state.trusted_publisher_key_resolver = None

    registry_configured = any(value is not None for value in (registry_service_resolver, registry_authorizer))
    sessions_configured = any(value is not None for value in (session_service_resolver, session_authorizer))
    if registry_configured:
        if registry_service_resolver is None or registry_authorizer is None:
            raise ValueError("registry service resolver and authorizer must be configured together")
        app.include_router(create_registry_router(
            resolve_principal=_principal_from_request,
            resolve_service=registry_service_resolver,
            authorize=registry_authorizer,
        ))
    if sessions_configured:
        if session_service_resolver is None or session_authorizer is None:
            raise ValueError("session service resolver and authorizer must be configured together")
        memory_context_resolver = IdentityContextResolver(identity_repository)
        app.include_router(create_sessions_router(
            resolve_principal=_principal_from_request,
            resolve_service=session_service_resolver,
            authorize=session_authorizer,
            memory_extraction=memory_extraction,
            resolve_memory_principal=(
                memory_context_resolver.memory if memory_extraction is not None else None
            ),
            audit_writer=resource_audit_writer or audit_request_sink,
        ))
    task_center_configured = any(value is not None for value in (
        task_service_resolver, task_authorizer, task_worker,
    ))
    if task_center_configured and any(value is None for value in (
        task_service_resolver, task_authorizer, task_worker,
    )):
        raise ValueError(
            "task service resolver, authorizer and durable task worker must be configured together"
        )
    if task_center_configured:
        if not callable(getattr(task_worker, "start", None)) or not callable(
            getattr(task_worker, "stop", None)
        ):
            raise ValueError("task_worker must expose async start() and stop() lifecycle methods")
        app.router.on_startup.append(task_worker.start)
        app.router.on_shutdown.append(task_worker.stop)
    if memory_expiry_worker is not None:
        if not callable(getattr(memory_expiry_worker, "start", None)) or not callable(
            getattr(memory_expiry_worker, "stop", None)
        ):
            raise ValueError("memory_expiry_worker must expose async start() and stop()")
        app.router.on_startup.append(memory_expiry_worker.start)
        app.router.on_shutdown.append(memory_expiry_worker.stop)
    if quota_reservation_worker is not None:
        if not callable(getattr(quota_reservation_worker, "start", None)) or not callable(
            getattr(quota_reservation_worker, "stop", None)
        ):
            raise ValueError("quota_reservation_worker must expose async start() and stop()")
        app.router.on_startup.append(quota_reservation_worker.start)
        app.router.on_shutdown.append(quota_reservation_worker.stop)
    if alert_dispatch_worker is not None:
        if not callable(getattr(alert_dispatch_worker, "start", None)) or not callable(
            getattr(alert_dispatch_worker, "stop", None)
        ):
            raise ValueError("alert_dispatch_worker must expose async start() and stop()")
        app.router.on_startup.append(alert_dispatch_worker.start)
        app.router.on_shutdown.append(alert_dispatch_worker.stop)
    if evaluation_worker is not None:
        if not callable(getattr(evaluation_worker, "start", None)) or not callable(
            getattr(evaluation_worker, "stop", None)
        ):
            raise ValueError("evaluation_worker must expose async start() and stop()")
        app.router.on_startup.append(evaluation_worker.start)
        app.router.on_shutdown.append(evaluation_worker.stop)
    if alert_evaluation_worker is not None:
        if not callable(getattr(alert_evaluation_worker, "start", None)) or not callable(
            getattr(alert_evaluation_worker, "stop", None)
        ):
            raise ValueError("alert_evaluation_worker must expose async start() and stop()")
        app.router.on_startup.append(alert_evaluation_worker.start)
        app.router.on_shutdown.append(alert_evaluation_worker.stop)
    blackboard_configured = any(value is not None for value in (
        blackboard_repository_resolver, blackboard_authorizer,
        blackboard_evidence_verifier, blackboard_event_replay_resolver,
    ))
    if blackboard_configured and any(value is None for value in (
        blackboard_repository_resolver, blackboard_authorizer,
    )):
        raise ValueError("Blackboard repository resolver and authorizer must be configured together")
    batch_api_configured = batch_service_resolver is not None
    if batch_api_configured and task_authorizer is None and batch_authorizer is None:
        raise ValueError("batch service resolver requires a batch or workbench task authorizer")
    graph_configured = any(value is not None for value in (
        graph_query_service_resolver, graph_read_authorizer,
    ))
    if graph_configured and (
        graph_query_service_resolver is None or graph_read_authorizer is None
    ):
        raise ValueError("graph query service resolver and graph read authorizer must be configured together")
    if graph_visualization_url is not None:
        parsed_graph_url = urlsplit(graph_visualization_url)
        if (
            not graph_configured or parsed_graph_url.scheme != "https"
            or not parsed_graph_url.hostname or parsed_graph_url.username is not None
            or parsed_graph_url.password is not None or parsed_graph_url.query or parsed_graph_url.fragment
        ):
            raise ValueError("graph_visualization_url requires graph API configuration and an HTTPS URL without credentials or fragment")
    app.include_router(create_workbench_router(
        features={
            "knowledge": True,
            "rag": rag is not None,
            "rag_query": rag_playground_configured,
            "memory": True,
            "sessions": sessions_configured,
            "registry": registry_configured,
            "agent_management": registry_configured,
            "task_execution": task_center_configured,
            "graph": graph_configured,
            "graph_annotation": graph_governance_resolver is not None,
            "graph_visualization": graph_visualization_url is not None,
            "batch_execution": batch_api_configured,
            "approvals": approval_service is not None and approval_authorizer is not None,
            "evaluation_datasets": evaluation_dataset_repository is not None,
            "evaluation_runs": evaluation_run_service is not None,
            "evaluation_reports": evaluation_repository is not None,
            "evaluation_comparisons": evaluation_comparison_service is not None,
            "blackboard": blackboard_configured,
            "quotas": quota_reporting_store is not None or quota_store is not None,
            "metrics": metrics_exporter is not None,
            "audit": audit_reader is not None and audit_authorizer is not None,
            "prompt_management": prompt_registry is not None and prompt_manage_authorizer is not None,
        },
        resolve_principal=_principal_from_request if task_center_configured else None,
        resolve_task_service=task_service_resolver,
        authorize_task=task_authorizer,
        connection_factory=connection_factory,
        redis_client=redis,
    ))
    if batch_api_configured:
        app.include_router(create_batch_router(
            resolve_principal=_principal_from_request,
            resolve_service=batch_service_resolver,
            authorize=batch_authorizer or task_authorizer,
        ))
    if blackboard_configured:
        app.include_router(create_blackboard_router(
            resolve_principal=_principal_from_request,
            resolve_repository=blackboard_repository_resolver,
            authorize_scope=blackboard_authorizer,
            verify_evidence_refs=blackboard_evidence_verifier,
            resolve_event_replay=blackboard_event_replay_resolver,
        ))
    if graph_configured:
        graph_identity_context = IdentityContextResolver(identity_repository)
        app.include_router(create_graph_router(
            resolve_principal=_principal_from_request,
            resolve_query_service=graph_query_service_resolver,
            authorize_read=graph_read_authorizer,
            resolve_governance=graph_governance_resolver,
            resolve_knowledge_principal=graph_identity_context.knowledge,
            visualization_url=graph_visualization_url,
        ))
        if connection_factory is not None:
            app.include_router(create_graph_extraction_router(
                service=KnowledgeGraphExtractionService(
                    connection_factory=connection_factory,
                    extractor=build_extractor_from_env(),
                ),
                resolve_principal=_principal_from_request,
                authorize_read=graph_read_authorizer,
            ))
    approvals_configured = any(value is not None for value in (approval_service, approval_authorizer))
    if approvals_configured:
        if approval_service is None or approval_authorizer is None:
            raise ValueError("approval service and approver authorizer must be configured together")
        if approval_risk_resolver is None:
            raise ValueError("approval risk resolver must be configured with the approval service")
        app.include_router(create_approval_router(
            service=approval_service, authorize_approver=approval_authorizer,
            resolve_risk_level=approval_risk_resolver,
        ))
    audit_configured = any(value is not None for value in (audit_reader, audit_authorizer, run_budget_audit_reader))
    if audit_configured:
        if audit_reader is None or audit_authorizer is None:
            raise ValueError("audit reader and audit authorizer must be configured together")
        app.include_router(create_audit_router(
            reader=audit_reader, identity_reader=identity_audit_reader,
            authorize_read=audit_authorizer,
            budget_reader=run_budget_audit_reader,
        ))
    quota_configured = any(value is not None for value in (
        quota_reporting_store, quota_service, quota_manage_authorizer, quota_scope_authorizer,
    ))
    if quota_configured:
        if quota_reporting_store is None:
            raise ValueError("quota reporting store is required when quota routes are configured")
        app.include_router(create_quota_reporting_router(
            quota_store=quota_reporting_store, quota_service=quota_service,
            authorize_manage=quota_manage_authorizer,
            authorize_scope=quota_scope_authorizer,
            audit_writer=quota_audit_writer or resource_audit_writer,
            quota_policy_service=quota_policy_service,
            authorize_default_policy=quota_default_policy_authorizer,
            authorize_policy_scope=quota_policy_scope_authorizer,
        ))
        if quota_service is not None and quota_manage_authorizer is not None:
            app.include_router(create_quota_admin_router(
                service=quota_service, authorize_manage=quota_manage_authorizer,
                authorize_scope=quota_scope_authorizer,
                audit_writer=quota_audit_writer or resource_audit_writer,
            ))
    elif quota_store is not None:
        app.include_router(create_quota_reporting_router(quota_store=quota_store))
    webhooks_configured = any(value is not None for value in (webhook_admin_service, webhook_admin_authorizer))
    if webhooks_configured:
        if webhook_admin_service is None or webhook_admin_authorizer is None:
            raise ValueError("webhook admin service and authorizer must be configured together")
        app.include_router(create_webhook_admin_router(
            service=webhook_admin_service,
            authorize_manage=webhook_admin_authorizer,
            audit_writer=resource_audit_writer,
        ))
    webhook_delivery_admin_configured = any(value is not None for value in (
        webhook_delivery_store, webhook_delivery_authorizer,
    ))
    if webhook_delivery_replay_service is not None and not webhook_delivery_admin_configured:
        raise ValueError("webhook replay service requires the delivery admin router")
    if webhook_delivery_admin_configured:
        if webhook_delivery_store is None or webhook_delivery_authorizer is None:
            raise ValueError("webhook delivery store and authorizer must be configured together")
        app.include_router(create_webhook_delivery_admin_router(
            store=webhook_delivery_store,
            authorize_manage=webhook_delivery_authorizer,
            replay_service=webhook_delivery_replay_service,
        ))
    if webhook_delivery_worker is not None:
        if not callable(getattr(webhook_delivery_worker, "start", None)) or not callable(
            getattr(webhook_delivery_worker, "stop", None)
        ):
            raise ValueError("webhook_delivery_worker must expose async start() and stop()")
        app.router.on_startup.append(webhook_delivery_worker.start)
        app.router.on_shutdown.append(webhook_delivery_worker.stop)
    if memory_extraction_event_worker is not None:
        if not callable(getattr(memory_extraction_event_worker, "start", None)) or not callable(
            getattr(memory_extraction_event_worker, "stop", None)
        ):
            raise ValueError("memory_extraction_event_worker must expose async start() and stop()")
        app.router.on_startup.append(memory_extraction_event_worker.start)
        app.router.on_shutdown.append(memory_extraction_event_worker.stop)
    if outbox_relay_worker is not None:
        if not callable(getattr(outbox_relay_worker, "start", None)) or not callable(
            getattr(outbox_relay_worker, "stop", None)
        ):
            raise ValueError("outbox_relay_worker must expose async start() and stop()")
        app.router.on_startup.append(outbox_relay_worker.start)
        app.router.on_shutdown.append(outbox_relay_worker.stop)
    connectors_configured = any(value is not None for value in (connector_service_resolver, connector_authorizer))
    if any(worker is not None for worker in (connector_sync_worker, connector_cleanup_worker)) and not connectors_configured:
        raise ValueError("connector workers require connector routes to be configured")
    if connectors_configured:
        if connector_service_resolver is None or connector_authorizer is None:
            raise ValueError("connector service resolver and authorizer must be configured together")
        app.include_router(create_connectors_router(
            resolve_principal=_principal_from_request,
            resolve_service=connector_service_resolver,
            authorize=connector_authorizer,
            audit_writer=resource_audit_writer,
        ))
    for worker, name in (
        (connector_sync_worker, "connector_sync_worker"),
        (connector_cleanup_worker, "connector_cleanup_worker"),
    ):
        if worker is not None:
            if not callable(getattr(worker, "start", None)) or not callable(
                getattr(worker, "stop", None)
            ):
                raise ValueError(f"{name} must expose async start() and stop()")
            app.router.on_startup.append(worker.start)
            app.router.on_shutdown.append(worker.stop)
    runs_configured = any(value is not None for value in (
        run_lifecycle_resolver, run_authorizer, tool_effect_reconciliation_resolver,
        run_budget_ledger_resolver,
    ))
    if runs_configured:
        if run_lifecycle_resolver is None or run_authorizer is None:
            raise ValueError("Run lifecycle resolver and authorizer must be configured together")
        app.include_router(create_runs_router(
            resolve_principal=_principal_from_request,
            resolve_lifecycle=run_lifecycle_resolver,
            authorize=run_authorizer,
            resolve_interventions=run_intervention_resolver,
            resolve_details=run_detail_resolver,
            resolve_budget_ledger=run_budget_ledger_resolver,
            resolve_executor=run_executor_resolver,
            resolve_tool_effects=tool_effect_reconciliation_resolver,
        ))
    if run_replay_resolver is not None:
        if run_authorizer is None:
            raise ValueError("Run replay resolver requires the Run authorizer")
        app.include_router(create_run_replay_router(
            resolve_principal=_principal_from_request,
            resolve_replay=run_replay_resolver,
            authorize=run_authorizer,
        ))
    locale_configured = any(value is not None for value in (
        locale_preference_service, locale_manage_authorizer,
    ))
    if locale_configured:
        if any(value is None for value in (
            locale_preference_service, locale_manage_authorizer, resource_audit_writer,
        )):
            raise ValueError(
                "locale preference service, authorizer and audit writer must be configured together"
            )
        app.include_router(create_locale_preferences_router(
            service=locale_preference_service,
            authorize_manage=locale_manage_authorizer,
            audit_writer=resource_audit_writer,
        ))
    value_management_configured = any(value is not None for value in (
        value_management_resolver, value_management_authorizer,
    ))
    if value_management_configured:
        if value_management_resolver is None or value_management_authorizer is None:
            raise ValueError("AI value management resolver and authorizer must be configured together")
        app.include_router(create_value_management_router(
            service_resolver=value_management_resolver,
            authorize_manage=value_management_authorizer,
            usage_store=quota_reporting_store or quota_store,
        ))
    return app
