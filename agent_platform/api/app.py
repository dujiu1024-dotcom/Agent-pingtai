"""Production composition factory for the platform HTTP API."""
from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urlsplit

from fastapi import FastAPI

from agent_platform.api.approvals import create_approval_router
from agent_platform.api.audit import create_audit_router
from agent_platform.api.blackboard import create_blackboard_router
from agent_platform.api.connectors import create_connectors_router
from agent_platform.api.identity import create_identity_router
from agent_platform.api.identity_context import IdentityContextResolver
from agent_platform.api.knowledge_memory import create_knowledge_memory_router
from agent_platform.api.observability import create_observability_router
from agent_platform.api.quotas import create_quota_reporting_router
from agent_platform.api.registry import create_registry_router
from agent_platform.api.runs import create_runs_router
from agent_platform.api.sessions import create_sessions_router
from agent_platform.api.webhook_deliveries import create_webhook_delivery_admin_router
from agent_platform.api.webhooks import create_webhook_admin_router
from agent_platform.api.workbench import create_workbench_router
from agent_platform.events.admin import WebhookSubscriptionService
from agent_platform.identity.http import (
    IdentityASGIMiddleware,
    RedisSlidingWindowRateLimiter,
)
from agent_platform.identity.service import IdentityService
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.extraction import MemoryExtractionService
from agent_platform.memory.service import MemoryService


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
    memory_extraction: MemoryExtractionService | None = None,
    memory_expiry_worker: Any | None = None,
    registry_service_resolver: Any | None = None,
    session_service_resolver: Any | None = None,
    registry_authorizer: Any | None = None,
    session_authorizer: Any | None = None,
    connector_service_resolver: Any | None = None,
    connector_authorizer: Any | None = None,
    run_lifecycle_resolver: Any | None = None,
    run_authorizer: Any | None = None,
    run_intervention_resolver: Any | None = None,
    run_detail_resolver: Any | None = None,
    run_executor_resolver: Any | None = None,
    approval_service: Any | None = None,
    approval_authorizer: Any | None = None,
    metrics_exporter: Any | None = None,
    audit_reader: Any | None = None,
    audit_authorizer: Any | None = None,
    quota_store: Any | None = None,
    quota_reporting_store: Any | None = None,
    quota_service: Any | None = None,
    quota_manage_authorizer: Any | None = None,
    quota_scope_authorizer: Any | None = None,
    webhook_admin_service: WebhookSubscriptionService | None = None,
    webhook_admin_authorizer: Any | None = None,
    webhook_delivery_store: Any | None = None,
    webhook_delivery_authorizer: Any | None = None,
    webhook_delivery_worker: Any | None = None,
    outbox_relay_worker: Any | None = None,
    task_service_resolver: Any | None = None,
    task_authorizer: Any | None = None,
    task_worker: Any | None = None,
    blackboard_repository_resolver: Any | None = None,
    blackboard_authorizer: Any | None = None,
    blackboard_evidence_verifier: Any | None = None,
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

    app = FastAPI()
    app.add_middleware(
        IdentityASGIMiddleware,
        identity=identity,
        service_identity=service_identity,
        allowed_origins=origins,
        protected_paths=("/api", "/auth"),
        login_rate_limiter=RedisSlidingWindowRateLimiter(redis),
    )
    app.include_router(create_identity_router(identity=identity))
    if metrics_exporter is not None:
        app.include_router(create_observability_router(metrics=metrics_exporter))
    app.include_router(create_knowledge_memory_router(
        knowledge=knowledge,
        memory=memory,
        rag=rag,
        memory_extraction=memory_extraction,
        identity_repository=identity_repository,
    ))
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
    blackboard_configured = any(value is not None for value in (
        blackboard_repository_resolver, blackboard_authorizer,
        blackboard_evidence_verifier,
    ))
    if blackboard_configured and any(value is None for value in (
        blackboard_repository_resolver, blackboard_authorizer,
    )):
        raise ValueError("Blackboard repository resolver and authorizer must be configured together")
    app.include_router(create_workbench_router(
        features={
            "knowledge": True,
            "rag": rag is not None,
            "memory": True,
            "sessions": sessions_configured,
            "registry": registry_configured,
            "agent_management": registry_configured,
            "task_execution": task_center_configured,
            "approvals": approval_service is not None and approval_authorizer is not None,
            "blackboard": blackboard_configured,
            "quotas": quota_reporting_store is not None or quota_store is not None,
            "metrics": metrics_exporter is not None,
            "audit": audit_reader is not None and audit_authorizer is not None,
        },
        resolve_principal=_principal_from_request if task_center_configured else None,
        resolve_task_service=task_service_resolver,
        authorize_task=task_authorizer,
    ))
    if blackboard_configured:
        app.include_router(create_blackboard_router(
            resolve_principal=_principal_from_request,
            resolve_repository=blackboard_repository_resolver,
            authorize_scope=blackboard_authorizer,
            verify_evidence_refs=blackboard_evidence_verifier,
        ))
    approvals_configured = any(value is not None for value in (approval_service, approval_authorizer))
    if approvals_configured:
        if approval_service is None or approval_authorizer is None:
            raise ValueError("approval service and approver authorizer must be configured together")
        app.include_router(create_approval_router(
            service=approval_service, authorize_approver=approval_authorizer,
        ))
    audit_configured = any(value is not None for value in (audit_reader, audit_authorizer))
    if audit_configured:
        if audit_reader is None or audit_authorizer is None:
            raise ValueError("audit reader and audit authorizer must be configured together")
        app.include_router(create_audit_router(
            reader=audit_reader, authorize_read=audit_authorizer,
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
        ))
    webhook_delivery_admin_configured = any(value is not None for value in (
        webhook_delivery_store, webhook_delivery_authorizer,
    ))
    if webhook_delivery_admin_configured:
        if webhook_delivery_store is None or webhook_delivery_authorizer is None:
            raise ValueError("webhook delivery store and authorizer must be configured together")
        app.include_router(create_webhook_delivery_admin_router(
            store=webhook_delivery_store,
            authorize_manage=webhook_delivery_authorizer,
        ))
    if webhook_delivery_worker is not None:
        if not callable(getattr(webhook_delivery_worker, "start", None)) or not callable(
            getattr(webhook_delivery_worker, "stop", None)
        ):
            raise ValueError("webhook_delivery_worker must expose async start() and stop()")
        app.router.on_startup.append(webhook_delivery_worker.start)
        app.router.on_shutdown.append(webhook_delivery_worker.stop)
    if outbox_relay_worker is not None:
        if not callable(getattr(outbox_relay_worker, "start", None)) or not callable(
            getattr(outbox_relay_worker, "stop", None)
        ):
            raise ValueError("outbox_relay_worker must expose async start() and stop()")
        app.router.on_startup.append(outbox_relay_worker.start)
        app.router.on_shutdown.append(outbox_relay_worker.stop)
    connectors_configured = any(value is not None for value in (connector_service_resolver, connector_authorizer))
    if connectors_configured:
        if connector_service_resolver is None or connector_authorizer is None:
            raise ValueError("connector service resolver and authorizer must be configured together")
        app.include_router(create_connectors_router(
            resolve_principal=_principal_from_request,
            resolve_service=connector_service_resolver,
            authorize=connector_authorizer,
        ))
    runs_configured = any(value is not None for value in (run_lifecycle_resolver, run_authorizer))
    if runs_configured:
        if run_lifecycle_resolver is None or run_authorizer is None:
            raise ValueError("Run lifecycle resolver and authorizer must be configured together")
        app.include_router(create_runs_router(
            resolve_principal=_principal_from_request,
            resolve_lifecycle=run_lifecycle_resolver,
            authorize=run_authorizer,
            resolve_interventions=run_intervention_resolver,
            resolve_details=run_detail_resolver,
            resolve_executor=run_executor_resolver,
        ))
    return app
