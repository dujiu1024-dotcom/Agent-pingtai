"""部署侧的 Run 执行组合（本地单机版）。

按 docs/deployment/issue-614-run-execution-composition.md 的非 Compose 路径，
通过 ``create_production_app(run_execution_builder=...)`` 注入。

本地实例使用注册表中的 OpenAI 兼容模型（商汤 Token Plan），由可信图工厂
构造“系统指令 + 用户输入 -> 模型回复”的单节点 LangGraph；模型调用经过
配额预检、用量记账与审计。工具/技能目录当前为空，Agent 定义也不引用它们。
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, Mapping, Sequence, TypedDict
from uuid import uuid4

from agent_platform.control_plane.postgres import PostgresRegistryRepository
from agent_platform.control_plane.registry import RegistryService
from agent_platform.identity.models import ResourceRef
from agent_platform.identity.service import AuthorizationDenied
from agent_platform.quotas.pricing import (
    CatalogModelCostEstimator,
    ModelPricingCatalog,
    PricingCard,
)
from agent_platform.models.registry_resolver import RegistryModelResolver
from agent_platform.models.runtime_router import (
    OpenAICompatibleHTTPTransport,
    OpenAICompatibleModelRouter,
    PostgresModelUsageRecorder,
)
from agent_platform.quotas.concurrency import QuotaConcurrencyLimiter
from agent_platform.quotas.token_estimator import TokenEstimate, TokenEstimationError
from agent_platform.runtime.langgraph_adapter import build_state_graph, postgres_checkpointer
from agent_platform.runtime.postgres_registry_snapshot import PostgresRunSnapshotStore
from agent_platform.runtime.production_composition import (
    ProductionRunExecution,
    build_production_run_execution,
)
from agent_platform.runtime.registry_runtime import (
    RegistryRuntimeAssembler,
    RegistryRuntimeProvider,
)
from agent_platform.runtime.registry_snapshot import RunRegistrySnapshotService
from agent_platform.runtime.workbench_tasks import RegistryLangGraphExecutorResolver

SECRET_REF = "secret://fde/models/sensenova"


class _TenantScope:
    """只承载 tenant_id 的最小主体，用于构造租户级注册表仓库。"""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id


class _EnvSecretResolver:
    """本地部署的密钥解析：secret://fde/models/sensenova -> 环境变量。

    优先读 SENSENOVA_API_KEYS（逗号分隔，取第一个），其次 SENSENOVA_API_KEY。
    密钥只存在于进程内，不写日志、不回传 API。
    """

    async def resolve(self, secret_ref: str) -> str:
        if secret_ref != SECRET_REF:
            raise RuntimeError(f"本地部署不支持解析密钥：{secret_ref}")
        keys = os.environ.get("SENSENOVA_API_KEYS", "")
        value = next((part.strip() for part in keys.split(",") if part.strip()), "")
        value = value or os.environ.get("SENSENOVA_API_KEY", "").strip()
        if not value:
            raise RuntimeError("未配置 SENSENOVA_API_KEYS / SENSENOVA_API_KEY")
        return value


class _HeuristicChatEstimator:
    """无 tiktoken 时的保守分词估算。

    中文按 1 字 1 token 计（偏保守），其他字符按 3 字符 1 token 计。
    该估算只用于配额预检与上下文校验，不参与计费。
    """

    @staticmethod
    def _count(text: str) -> int:
        cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        other = len(text) - cjk
        return cjk + (other + 2) // 3

    def estimate(
        self, *, model_id: str, messages: Sequence[Mapping[str, Any]],
        request_parameters: Mapping[str, Any], context_window_tokens: int,
    ) -> TokenEstimate:
        if not model_id:
            raise TokenEstimationError("model_id is required")
        output_keys = [key for key in ("max_completion_tokens", "max_tokens") if key in request_parameters]
        if len(output_keys) != 1:
            raise TokenEstimationError("exactly one explicit max_tokens is required")
        output_tokens = request_parameters[output_keys[0]]
        if not isinstance(output_tokens, int) or isinstance(output_tokens, bool) or output_tokens < 1:
            raise TokenEstimationError("planned output tokens must be a positive integer")
        if output_tokens >= context_window_tokens:
            raise TokenEstimationError("planned output allowance must be smaller than the context window")
        input_tokens = 3
        for message in messages:
            if not isinstance(message, Mapping):
                raise TokenEstimationError("chat messages must be mappings")
            input_tokens += 4 + self._count(str(message.get("role") or ""))
            content = message.get("content")
            if isinstance(content, str):
                input_tokens += self._count(content)
            elif content is not None:
                raise TokenEstimationError("only text message content is supported")
        if input_tokens + output_tokens > context_window_tokens:
            raise TokenEstimationError("estimated prompt plus planned output exceeds context window")
        return TokenEstimate(input_tokens=input_tokens, output_tokens=output_tokens)


class _UnusedSkillReviewer:
    """当前 Agent 未引用 Skill；一旦出现 Skill 调用，明确失败而不是放行。"""

    async def require_approved(self, **_kwargs: Any) -> Any:
        raise RuntimeError("本地部署尚未配置已审核的 Skill 目录")


class _ModelAuditAdapter:
    """把模型路由的细粒度 outcome 映射到身份审计允许的取值。"""

    def __init__(self, writer: Any) -> None:
        self._writer = writer

    async def record(
        self, *, tenant_id: str, actor_id: str, action: str, subject_id: str,
        outcome: str = "succeeded", detail: Mapping[str, Any] | None = None,
    ) -> None:
        mapped = "succeeded" if outcome in {"started", "succeeded"} else outcome
        if mapped not in {"succeeded", "denied", "failed"}:
            mapped = "failed"
        payload = dict(detail or {})
        payload.setdefault("model_outcome", outcome)
        await self._writer.record(
            tenant_id=tenant_id, actor_id=actor_id, action=action,
            subject_id=subject_id, outcome=mapped, detail=payload,
        )


class _ChatState(TypedDict, total=False):
    prompt: str
    message: str
    text: str
    question: str
    query: str
    input: Any
    output: str
    usage: dict[str, Any]


def _prompt_from(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, Mapping):
        for name in ("prompt", "message", "text", "question", "query", "input"):
            value = payload.get(name)
            text = _prompt_from(value)
            if text:
                return text
        return json.dumps(dict(payload), ensure_ascii=False)
    if payload is None:
        return ""
    return str(payload)


class _SnapshotRouter:
    """按租户把快照请求路由到对应服务，保持本地单进程下的多租户隔离。"""

    def __init__(self, resolver: Any) -> None:
        self._resolver = resolver

    async def get(self, *, tenant_id: str, run_id: str) -> Any:
        return await self._resolver(tenant_id).get(tenant_id=tenant_id, run_id=run_id)


def _build_model_router(
    *, connection_factory: Any, quota_service: Any, audit_writer: Any,
) -> OpenAICompatibleModelRouter:
    # 本地部署按“平台代付、无单价”处理成本：价格卡显式为 0，
    # 令牌额度仍然生效，避免把未知商业单价编造成真实成本。
    catalog = ModelPricingCatalog({
        "sensenova-6.8-flash-lite": PricingCard(
            model_key="sensenova-6.8-flash-lite", version="local-0",
            input_usd_per_million=Decimal("0"), output_usd_per_million=Decimal("0"),
        ),
    })
    cost_estimator = CatalogModelCostEstimator(catalog)
    return OpenAICompatibleModelRouter(
        resolver=RegistryModelResolver(_EnvSecretResolver()),
        transport=OpenAICompatibleHTTPTransport(),
        usage_recorder=PostgresModelUsageRecorder(
            connection_factory, cost_estimator=cost_estimator,
        ),
        audit_sink=_ModelAuditAdapter(audit_writer),
        timeout_seconds=120,
        quota_service=quota_service,
        token_estimator=_HeuristicChatEstimator(),
        quota_cost_estimator=cost_estimator,
    )


def _graph_factory(*, dsn: str):
    def factory(*, agent_definition: Mapping[str, Any], snapshot: Any,
                task_record: Mapping[str, Any], runtime_provider: Any) -> Any:
        instructions = str(
            agent_definition.get("instructions") or "你是一个简洁的中文助手。"
        )
        task_input = task_record.get("input")
        # LangGraph 只向单参数节点传 state；Run 身份从本次任务记录闭包获取。
        tenant_id = str(task_record.get("tenant_id") or "")
        run_id = str(task_record.get("run_id") or "")

        @asynccontextmanager
        async def build():
            async with postgres_checkpointer(dsn) as checkpointer:
                async def assistant(state: Mapping[str, Any]) -> dict[str, Any]:
                    if not tenant_id or not run_id:
                        raise RuntimeError("缺少 Run 身份，无法调用模型")
                    runtime = await runtime_provider.get(tenant_id, run_id)
                    prompt = _prompt_from(dict(state)) or _prompt_from(task_input)
                    if not prompt:
                        raise RuntimeError("任务输入为空，无法生成回复")
                    response = await runtime.model.complete(
                        messages=[
                            {"role": "system", "content": instructions},
                            {"role": "user", "content": prompt},
                        ],
                        request_id=f"run-{run_id}",
                        data_level="internal",
                    )
                    return {"output": response.content, "usage": dict(response.usage)}

                yield build_state_graph(
                    state_schema=_ChatState,
                    nodes={"assistant": assistant},
                    edges=[("assistant", "__end__")],
                    entry_point="assistant",
                    checkpointer=checkpointer,
                    tool_using_nodes=(),
                )

        return build()

    return factory


def build_run_execution(
    *,
    run_store: Any,
    lifecycle: Any,
    quota_service: Any,
    quota_store: Any,
    identity_service: Any,
    identity_repository: Any,
    audit_writer: Any,
    connection_factory: Any,
    redis_client: Any,
    settings: Any,
) -> ProductionRunExecution:
    snapshots_by_tenant: dict[str, RunRegistrySnapshotService] = {}

    def registry_resolver(principal: Any) -> RegistryService:
        repository = PostgresRegistryRepository(
            tenant_id=principal.tenant_id, connection_factory=connection_factory,
        )
        return RegistryService(repository)

    def snapshot_for_tenant(tenant_id: str) -> RunRegistrySnapshotService:
        service = snapshots_by_tenant.get(tenant_id)
        if service is None:
            service = RunRegistrySnapshotService(
                registry=registry_resolver(_TenantScope(tenant_id)),
                store=PostgresRunSnapshotStore(connection_factory),
            )
            snapshots_by_tenant[tenant_id] = service
        return service

    def snapshot_resolver(principal: Any) -> RunRegistrySnapshotService:
        return snapshot_for_tenant(principal.tenant_id)

    snapshot_router = _SnapshotRouter(snapshot_for_tenant)
    runtime_provider = RegistryRuntimeProvider(
        assembler=RegistryRuntimeAssembler(
            snapshots=snapshot_router,  # type: ignore[arg-type]
            model_router=_build_model_router(
                connection_factory=connection_factory,
                quota_service=quota_service,
                audit_writer=audit_writer,
            ),
            skill_reviewer=_UnusedSkillReviewer(),  # type: ignore[arg-type]
            trusted_tool_handlers={},
        ),
    )

    async def allowed(
        principal: Any, resource_type: str, resource_id: str, permission_key: str,
    ) -> bool:
        try:
            await identity_service.authorize(
                principal=principal,
                resource=ResourceRef(resource_type, resource_id),
                permission_key=permission_key,
            )
            return True
        except AuthorizationDenied:
            return False

    async def authorize_task(principal: Any, resource_id: str, action: str) -> bool:
        if resource_id == "*":
            return await allowed(principal, "tenant", principal.tenant_id, "agent.run")
        if action == "execute":
            return await allowed(principal, "agent", resource_id, "agent.run")
        if action == "use_project":
            return await allowed(principal, "project", resource_id, "project.read")
        if action == "use_session":
            return await allowed(principal, "session", resource_id, "agent.run")
        record = await run_store.get_task_record(principal.tenant_id, resource_id)
        if record is None:
            return False
        return await allowed(principal, "agent", str(record["agent_id"]), "agent.run")

    executor_resolver = RegistryLangGraphExecutorResolver(
        snapshots=snapshot_router,
        lifecycle_resolver=lambda _tenant_id: lifecycle,
        runtime_provider_resolver=lambda _tenant_id: runtime_provider,
        graph_factory=_graph_factory(dsn=settings.postgres_dsn),
    )

    return build_production_run_execution(
        run_store=run_store,
        lifecycle=lifecycle,
        registry_resolver=registry_resolver,
        snapshot_resolver=snapshot_resolver,
        executor_resolver=executor_resolver,
        task_authorizer=authorize_task,
        concurrency_limiter=QuotaConcurrencyLimiter(quota_service),
        worker_pool_scope_id="local",
        queue_scope_resolver=lambda record: str(record.get("project_id") or "default"),
        worker_id=f"local-{uuid4().hex}",
        poll_interval=2.0,
    )
