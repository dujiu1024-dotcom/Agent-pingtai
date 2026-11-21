"""部署侧的 Run 执行组合（本地单机版）。

按 docs/deployment/issue-614-run-execution-composition.md 的非 Compose 路径，
通过 ``create_production_app(run_execution_builder=...)`` 注入。

本地实例使用注册表中的 OpenAI 兼容模型（商汤 Token Plan），由可信图工厂
构造“系统指令 + 用户输入 -> 模型回复”的单节点 LangGraph；模型调用经过
配额预检、用量记账与审计。工具/技能目录当前为空，Agent 定义也不引用它们。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, Mapping, Sequence, TypedDict
from uuid import uuid4

from agent_platform.control_plane.postgres import (
    PostgresRegistryRepository,
    PostgresSessionRepository,
)
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
from agent_platform.models.circuit_breaker import AsyncCircuitBreaker
from agent_platform.quotas.concurrency import QuotaConcurrencyLimiter
from agent_platform.quotas.token_estimator import TokenEstimate, TokenEstimationError
from agent_platform.observability.run_trace import PostgresRunTraceRepository, RunTraceService
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
from agent_platform.tools.registry_tools import (
    REGISTRY_TOOL_HANDLERS, tool_schemas_for_llm,
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


class _LocalSkillReviewer:
    """本地部署的轻量 Skill 放行器。

    本地部署没有 artifact registry + zip 打包机制。这里把 "lightweight"
    Skill（definition 里带 mode=lightweight 和 tool_ids）视为 tool 集合
    的别名，直接放行；重型 Skill（需要 entrypoint + dependencies）仍然拒绝。
    """

    async def require_approved(self, *, tenant_id: str, item: Any) -> Any:
        definition = getattr(item, "definition", None) or {}
        # 轻量模式 → 只要带 tool_ids 就放行
        if isinstance(definition, dict) and definition.get("mode") == "lightweight":
            return {"skill_id": item.item_id, "mode": "lightweight",
                    "tool_ids": definition.get("tool_ids", [])}
        raise RuntimeError(
            f"本地部署不支持重型 Skill（需要 artifact registry）；"
            f"用 mode=lightweight + tool_ids 即可"
        )


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
    tool_calls_history: list[dict[str, Any]]
    rounds_used: int
    any_tool_called: bool


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


def _truncate_json_for_preview(obj: Any, max_chars: int = 600) -> str:
    """序列化成 JSON，超长时截断并修复尾部未闭合的括号/引号，保证 json.loads 可用。

    策略：
      1. 先完整 dumps
      2. 如果没超 max_chars，直接返回
      3. 超了就截到 max_chars-1（留一个字符），然后从尾到头补齐未闭合的 quote / ] / }
    """
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    cut = max_chars
    truncated = text[:cut]
    # 从尾往头扫，统计未闭合的引号和括号
    in_string = False
    escape = False
    single_quotes = 0  # 实际上 JSON 只用双引号
    depth_brace = 0
    depth_bracket = 0
    quote_open = False
    for ch in truncated:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            quote_open = not quote_open
        elif not quote_open:
            if ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace -= 1
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket -= 1

    # 修尾部：先关字符串，再关 bracket，再关 brace
    repaired = truncated
    if quote_open:
        repaired += '"'
    if depth_bracket > 0:
        repaired += "]" * depth_bracket
    if depth_brace > 0:
        repaired += "}" * depth_brace
    # 如果修复后尾部还有逗号（如 "...},]" → 尾部的 , 会让 JSON 无效）
    repaired = repaired.rstrip(",")
    # 重新闭合（可能修复逗号后又露出未闭合括号）
    quote_open = False
    depth_brace = 0
    depth_bracket = 0
    escape = False
    for ch in repaired:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            quote_open = not quote_open
        elif not quote_open:
            if ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace -= 1
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket -= 1
    if quote_open:
        repaired += '"'
    if depth_bracket > 0:
        repaired += "]" * depth_bracket
    if depth_brace > 0:
        repaired += "}" * depth_brace
    return repaired


def _fallback_output(tool_calls_history: list[dict[str, Any]], user_prompt: str = "") -> str:
    """LLM 只调工具没生成人话时，从 tool_calls_history 拼自然语言摘要——**纯通用**，不硬编码任何工具。

    策略：遍历每个 agentic call → 尝试解析 result_preview →
      ✓ JSON dict → 抽 message（最优先）→ 否则抽关键列表/数值 → 否则给工具执行确认
      ✓ JSON list → 报告列表数量
      ✗ JSON → 给工具执行确认
    绝不输出原始 JSON 片段到用户可见的对话里。
    """
    if not tool_calls_history:
        return ""
    agentic_calls = [h for h in tool_calls_history if h.get("agentic")]
    if not agentic_calls:
        return ""

    def _parse_preview(preview: str | None) -> Any:
        if not preview:
            return None
        try:
            return json.loads(preview)
        except (json.JSONDecodeError, TypeError):
            return None

    def _extract_names(item: Any) -> str | None:
        """从 agent registry 等列表里抽 name 字段。"""
        if isinstance(item, list) and item:
            names = []
            for entry in item[:8]:
                if isinstance(entry, dict):
                    n = entry.get("name") or entry.get("title") or entry.get("agent_id")
                    if isinstance(n, str) and n.strip():
                        names.append(n.strip())
            if names:
                return "、".join(names) + (" 等" if len(item) > len(names) else "")
        return None

    def _summarize_value(val: Any, max_len: int = 60) -> str:
        """把任意 JSON 值转成简洁可读的文本片段，绝不输出原始 JSON。"""
        if val is None:
            return ""
        if isinstance(val, bool):
            return "成功" if val else "失败"
        if isinstance(val, (int, float)):
            return str(val)
        if isinstance(val, str):
            s = val.strip()
            if len(s) > max_len:
                s = s[:max_len] + "..."
            return s
        if isinstance(val, list):
            names = _extract_names(val)
            if names:
                return names
            return f"{len(val)} 项"
        if isinstance(val, dict):
            names = _extract_names(val.get("agents")) or _extract_names(val.get("items"))
            if names:
                return names
            # 最多 2 个 primitive 字段
            primitives = []
            for k, v in val.items():
                if isinstance(v, (str, int, float, bool)) and v is not None:
                    text = str(v).strip()
                    if text and len(text) < max_len:
                        primitives.append(text)
                    if len(primitives) >= 2:
                        break
            return "，".join(primitives) if primitives else f"包含 {len(val)} 个字段"
        return str(val)

    def _friendly_tool_name(tname: str) -> str:
        """把 snake_case 工具名转成可读中文。"""
        mapping = {
            "registry_list_agents": "查询 Agent 名单",
            "task_dispatch": "任务转派",
            "finance_query": "财务查询",
            "finance_record": "记账",
            "ops_check": "运维检查",
            "hr_query": "人事查询",
            "knowledge_search": "知识库检索",
        }
        if tname in mapping:
            return mapping[tname]
        # 通用 snake_case → 中文描述
        parts = tname.replace("_", " ").split()
        if len(parts) >= 2:
            verb_map = {
                "list": "查询", "query": "查询", "search": "检索",
                "get": "获取", "fetch": "获取", "read": "读取",
                "create": "创建", "add": "添加", "write": "写入",
                "update": "更新", "edit": "修改", "delete": "删除",
                "dispatch": "转派", "route": "路由", "assign": "分配",
                "record": "记录", "log": "记录", "submit": "提交",
                "check": "检查", "verify": "验证", "validate": "校验",
                "run": "执行", "invoke": "调用", "call": "调用",
            }
            verb = verb_map.get(parts[0], parts[0])
            subject = " ".join(parts[1:])
            return f"{verb} {subject}"
        return tname

    summaries: list[str] = []
    for call in agentic_calls:
        tname = call.get("tool_name", "?")
        friendly = _friendly_tool_name(tname)
        parsed = _parse_preview(call.get("result_preview"))

        if isinstance(parsed, dict):
            # 优先取 message 字段（通常是业务层的自然语言回复）
            msg = parsed.get("message")
            if isinstance(msg, str) and msg.strip() and len(msg.strip()) > 10:
                summaries.append(msg.strip())
                continue
            # 其次取 summary 字段
            summary = parsed.get("summary")
            if isinstance(summary, str) and summary.strip():
                summaries.append(summary.strip())
                continue
            # 取 status 确认
            status = parsed.get("status")
            if isinstance(status, str) and status.strip() in ("success", "ok", "done", "completed"):
                # 成功但无 message → 报告关键结果
                agent_names = _extract_names(parsed.get("agents"))
                if agent_names:
                    summaries.append(f"{friendly}：当前可用的 Agent 有 {agent_names}")
                    continue
                items_count = parsed.get("entry_count") or parsed.get("total") or parsed.get("count")
                if isinstance(items_count, int) and items_count > 0:
                    summaries.append(f"{friendly}：共找到 {items_count} 条记录")
                    continue
                balances = parsed.get("balances")
                if isinstance(balances, (dict, list)):
                    btext = _summarize_value(balances, 80)
                    if btext:
                        summaries.append(f"{friendly}：{btext}")
                        continue
                summaries.append(f"{friendly}：执行成功")
                continue
            elif isinstance(status, str) and status.strip() not in ("success", "ok", "done", "completed"):
                summaries.append(f"{friendly}：状态为 {status}")
                continue
            # 无 status → 报告有哪些数据
            agent_names = _extract_names(parsed.get("agents"))
            if agent_names:
                summaries.append(f"{friendly}：当前可用的 Agent 有 {agent_names}")
                continue
            summaries.append(f"{friendly}：执行完毕")
            continue

        if isinstance(parsed, list):
            names = _extract_names(parsed)
            if names:
                summaries.append(f"{friendly}：{names}")
            else:
                summaries.append(f"{friendly}：共 {len(parsed)} 项")
            continue

        # JSON 解析失败（非 JSON 或 None）→ 只说执行了什么工具，不暴露原始内容
        summaries.append(f"{friendly}：已执行")

    # 去掉每个 summary 末尾的标点，避免拼接后重复
    cleaned = []
    for s in summaries:
        s = s.rstrip("。.！!？?；;，,")
        if s:
            cleaned.append(s)
    return "。".join(cleaned) + "。"


class _SnapshotRouter:
    """按租户把快照请求路由到对应服务，保持本地单进程下的多租户隔离。"""

    def __init__(self, resolver: Any) -> None:
        self._resolver = resolver

    async def get(self, *, tenant_id: str, run_id: str) -> Any:
        return await self._resolver(tenant_id).get(tenant_id=tenant_id, run_id=run_id)


def _build_model_router(
    *, connection_factory: Any, quota_service: Any, audit_writer: Any,
) -> OpenAICompatibleModelRouter:
    # 价格卡：按模型厂商的公开/参考定价填入。本地 Ollama 模型成本为 0，
    # 商业模型使用行业参考价，确保成本中心能展示非零的估算费用。
    #
    # 参考来源：Ciyuano 平台 SenseNova 参考价（¥2.0/¥8.0 ≈ $0.28/$1.11），
    # 换算后按 $0.30 / $1.20 四舍五入；flash-lite 版本取一半。
    catalog = ModelPricingCatalog({
        "sensenova-6.8-flash-lite": PricingCard(
            model_key="sensenova-6.8-flash-lite", version="sn-ref-2025",
            input_usd_per_million=Decimal("0.15"),
            output_usd_per_million=Decimal("0.60"),
        ),
        "sensenova-6.8-flash": PricingCard(
            model_key="sensenova-6.8-flash", version="sn-ref-2025",
            input_usd_per_million=Decimal("0.30"),
            output_usd_per_million=Decimal("1.20"),
        ),
        # 本本地 Ollama / 自研模板：零成本（本地代跑，无厂商账单）。
        # Catalog.get 会自动剥离 @数字 版本后缀匹配到此处。
        "local-template": PricingCard(
            model_key="local-template", version="local-0",
            input_usd_per_million=Decimal("0"), output_usd_per_million=Decimal("0"),
        ),
        "ollama-template": PricingCard(
            model_key="ollama-template", version="local-0",
            input_usd_per_million=Decimal("0"), output_usd_per_million=Decimal("0"),
        ),
        "sensenova-flash-template": PricingCard(
            model_key="sensenova-flash-template", version="sn-ref-2025",
            input_usd_per_million=Decimal("0.30"),
            output_usd_per_million=Decimal("1.20"),
        ),
        "lukey03/qwen3.5-9b-abliterated": PricingCard(
            model_key="lukey03/qwen3.5-9b-abliterated", version="ollama-0",
            input_usd_per_million=Decimal("0"), output_usd_per_million=Decimal("0"),
        ),
    })
    cost_estimator = CatalogModelCostEstimator(catalog)
    return OpenAICompatibleModelRouter(
        resolver=RegistryModelResolver(_EnvSecretResolver(), allow_insecure_loopback=True),
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


logger = logging.getLogger("agent_platform.deploy")


def _registry_tool_handlers() -> dict[str, Any]:
    """Return trusted handler map for registry management tools.

    These handlers run inside the Run's trusted tool gateway when an agent's
    tool_ids reference a published tool. They execute with tenant-scoped DB
    access via psycopg.
    """
    return dict(REGISTRY_TOOL_HANDLERS)


async def _run_tool_handler(
    *, handler: Any, arguments: dict[str, Any],
    tenant_id: str, run_id: str, agent_id: str,
    session_id: str | None = None,
) -> Any:
    """Invoke a trusted tool handler with execution context fields injected."""
    augmented = dict(arguments)
    augmented["_tenant_id"] = tenant_id
    augmented["_run_id"] = run_id
    augmented["_agent_id"] = agent_id
    if session_id:
        augmented["_session_id"] = session_id
    try:
        result = await handler(augmented)
        return result
    except Exception as exc:
        logger.warning("tool %s failed: %s", getattr(handler, "__name__", "?"), exc)
        return {"error": str(exc)}


async def _agent_roster(dsn: str, tenant_id: str) -> str:
    """把租户当前已发布的 Agent 清单注入系统指令，避免模型凭空猜测组织架构。"""
    try:
        import psycopg
        from psycopg.rows import dict_row

        async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as connection:
            cursor = await connection.execute(
                "SELECT a.item_id, v.definition->>'name' AS name, "
                "v.definition->>'description' AS description "
                "FROM platform_registry_active a "
                "JOIN platform_registry_versions v "
                "ON v.tenant_id=a.tenant_id AND v.kind=a.kind "
                "AND v.item_id=a.item_id AND v.version=a.active_version "
                "WHERE a.tenant_id=%s AND a.kind='agent' ORDER BY a.item_id",
                (tenant_id,),
            )
            rows = await cursor.fetchall()
    except Exception:
        logger.exception("读取 Agent 清单失败，本次不注入组织上下文")
        return ""
    lines = ["## 平台当前已注册的 Agent（系统实时数据，以此为准）"]
    if not rows:
        lines.append("（暂无）")
    for row in rows:
        description = str(row["description"] or "").strip()
        label = f"- {row['item_id']}（{row['name']}）"
        lines.append(f"{label}：{description}" if description else label)
    lines.append("以上是全部 Agent。未列出的角色不存在，不要假设组织中还有其他角色。")
    return "\n".join(lines)


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
        agent_id = str(task_record.get("agent_id") or "")
        session_id = task_record.get("session_id")  # 用于 assistant 内部查 DB 历史

        @asynccontextmanager
        async def build():
            roster = await _agent_roster(dsn, tenant_id) if tenant_id else ""
            system_prompt = f"{instructions}\n\n{roster}" if roster else instructions
            async with postgres_checkpointer(dsn) as checkpointer:
                def _history_to_messages(history: Any, current_prompt: str) -> list[dict[str, str]]:
                    """把 session history 转成 LLM messages 列表。

                    规则：
                    - 跳过 run_input 快照（和 command-center 是重复的）
                    - assistant content 如果是 run_result 的 JSON 字符串（{"output": "...", "usage": {...}}），
                      提取里面的 output 纯文本再喂给 LLM
                    - 跳过和当前 user prompt 完全相同的最后一条（避免重复）
                    - 最多保留 20 条，避免上下文过长
                    """
                    if not history:
                        return []
                    msgs: list[dict[str, str]] = []
                    seen_current = False
                    for item in history:
                        if not isinstance(item, dict):
                            continue
                        meta = item.get("metadata") or {}
                        # 跳过后端内部快照（run_input）和非对话来源
                        if meta.get("source") == "run_input":
                            continue
                        role = item.get("role", "")
                        content = item.get("content", "")
                        if role not in ("user", "assistant") or not isinstance(content, str):
                            continue
                        # assistant 的 run_result content 是 JSON 字符串，提取 output
                        if role == "assistant":
                            stripped = content.strip()
                            if stripped.startswith("{"):
                                try:
                                    obj = json.loads(stripped)
                                    for key in ("output", "content", "result", "text", "answer", "message"):
                                        if isinstance(obj.get(key), str) and obj[key].strip():
                                            content = obj[key]
                                            break
                                except Exception:
                                    pass
                        # 跳过和当前这条完全相同的 user message（begin_run 会先 append 再 list，
                        # 所以 history 里最后一条 user 就是当前 prompt）
                        if role == "user" and content == current_prompt and not seen_current:
                            seen_current = True
                            continue
                        msgs.append({"role": role, "content": content})
                    return msgs[-20:]  # 尾部 20 条

                async def assistant(state: Mapping[str, Any]) -> dict[str, Any]:
                    if not tenant_id or not run_id:
                        raise RuntimeError("缺少 Run 身份，无法调用模型")
                    runtime = await runtime_provider.get(tenant_id, run_id)
                    prompt = _prompt_from(dict(state)) or _prompt_from(task_input)
                    if not prompt:
                        raise RuntimeError("任务输入为空，无法生成回复")

                    # 直接查 DB 拿 session history（绕过 LangGraph config 传递问题）
                    history: list[dict[str, Any]] = []
                    if session_id and tenant_id:
                        try:
                            import psycopg
                            from psycopg.rows import dict_row as _dr
                            async with await psycopg.AsyncConnection.connect(dsn, row_factory=_dr) as conn:
                                cur = await conn.execute(
                                    "SELECT role, content, metadata FROM platform_messages "
                                    "WHERE tenant_id=%s AND session_id=%s ORDER BY created_at ASC",
                                    (tenant_id, session_id),
                                )
                                rows = await cur.fetchall()
                                history = [dict(r) for r in rows]
                        except Exception as e:
                            logger.warning("读取 session history 失败 session=%s err=%s", session_id, e)

                    history_messages = _history_to_messages(history, prompt)

                    # 构建 LLM messages：system + history + user
                    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
                    messages.extend(history_messages)
                    messages.append({"role": "user", "content": prompt})

                    # 从 agent_definition.tool_ids 取可用 tool schemas
                    agent_tool_ids = agent_definition.get("tool_ids") or []
                    available_tools = tool_schemas_for_llm() if agent_tool_ids else None
                    if available_tools:
                        # 只暴露 agent 请求的那些 tool
                        available_tools = [
                            t for t in available_tools
                            if t["function"]["name"] in set(agent_tool_ids)
                        ]
                        if not available_tools:
                            available_tools = None

                    logger.info("assistant run=%s session=%s history_msgs=%d tools=%d",
                                run_id, session_id, len(history_messages),
                                len(available_tools) if available_tools else 0)

                    # Function call loop：最多 5 轮，每轮 LLM 调用 120s 超时保护
                    final_content = ""
                    total_usage: dict[str, Any] = {}
                    tool_calls_history: list[dict[str, Any]] = []
                    rounds_used = 0
                    ANY_TOOL_CALL_TRIGGERED = False

                    # 兜底机制：如果 agent 有工具但第一轮没调，framework 自动
                    # 预热 registry_list_agents（大多数 agent 都有），把 roster
                    # 注入 messages 后继续让模型决策一次。
                    FALLBACK_INJECTED = False
                    # 模型调完工具后返回空 content 的 retry 标记（单次 run 只重试一次）
                    _EMPTY_CONTENT_RETRYED = False

                    for round_idx in range(5):
                        rounds_used = round_idx + 1
                        try:
                            response = await asyncio.wait_for(
                                runtime.model.complete(
                                    messages=messages,
                                    request_id=f"run-{run_id}",
                                    data_level="internal",
                                    tools=available_tools,
                                    tool_choice="auto" if available_tools else None,
                                ),
                                timeout=120.0,
                            )
                        except asyncio.TimeoutError:
                            raise RuntimeError(
                                f"LLM call timed out after 120s (run={run_id})"
                            )
                        # 累加 usage
                        u = dict(response.usage or {})
                        for k, v in u.items():
                            try:
                                total_usage[k] = (total_usage.get(k, 0) or 0) + v
                            except (TypeError, ValueError):
                                total_usage[k] = v

                        tool_calls = response.tool_calls
                        if not tool_calls:
                            # --- 检测：模型什么都没说（既没调工具也没给文本回复）---
                            if not (response.content or "").strip():
                                # 已经调过工具但突然沉默 → 提示它总结，给一次 retry
                                if (
                                    ANY_TOOL_CALL_TRIGGERED
                                    and round_idx > 0
                                    and not _EMPTY_CONTENT_RETRYED
                                ):
                                    _EMPTY_CONTENT_RETRYED = True
                                    logger.warning(
                                        "agent=%s model returned empty content + no tool_calls "
                                        "after tool execution, retrying with hint (run=%s)",
                                        str(task_record.get("agent_id") or ""), run_id,
                                    )
                                    messages.append({
                                        "role": "assistant",
                                        "content": "",
                                    })
                                    messages.append({
                                        "role": "user",
                                        "content": (
                                            "[系统提示] 你刚才执行了工具并拿到了结果，"
                                            "但没有生成文字回复。请用中文给出自然语言总结——"
                                            "把工具结果里的关键信息（数值、状态、列表）告诉用户。"
                                        ),
                                    })
                                    continue
                                # Retry 也救不了（没调过工具 or 已重试过）→ 跳出循环，走兜底输出
                                # 不要用 break，让下面的 final_content = "" 赋值自然生效
                                final_content = ""
                                break

                            # --- 兜底检测 ---
                            # 第一轮没调工具，但 agent 声明了有工具可用
                            if (
                                round_idx == 0
                                and available_tools is not None
                                and not ANY_TOOL_CALL_TRIGGERED
                                and not FALLBACK_INJECTED
                            ):
                                # 找 registry_list_agents 作为预热工具
                                list_agents_schema = next(
                                    (t for t in available_tools
                                     if t["function"]["name"] == "registry_list_agents"),
                                    None,
                                )
                                if list_agents_schema is not None:
                                    FALLBACK_INJECTED = True
                                    logger.warning(
                                        "agent=%s first-round no tool_calls, "
                                        "injecting registry_list_agents fallback (run=%s)",
                                        agent_id, run_id,
                                    )
                                    # 框架层自动调一次 registry_list_agents
                                    handler = REGISTRY_TOOL_HANDLERS.get(
                                        "registry_list_agents"
                                    )
                                    fallback_result: Any
                                    if handler is None:
                                        fallback_result = {
                                            "error": "registry_list_agents handler missing"
                                        }
                                    else:
                                        fallback_result = await _run_tool_handler(
                                            handler=handler.handler,
                                            arguments={},
                                            tenant_id=tenant_id,
                                            run_id=run_id,
                                            agent_id=str(task_record.get("agent_id") or ""),
                                            session_id=session_id,
                                        )
                                    tool_calls_history.append({
                                        "tool_name": "registry_list_agents",
                                        "agentic": False,
                                        "trigger": "framework_fallback_no_tool_calls",
                                        "args": {},
                                        "result_preview": _truncate_json_for_preview(
                                            fallback_result, max_chars=600
                                        ),
                                    })
                                    # 把兜底结果注入 messages，继续让模型决策
                                    messages.append({
                                        "role": "assistant",
                                        "content": (
                                            response.content or ""
                                        ) + "\n\n[系统补充:已自动查询当前所有 Agent 名单，请参考后再回复。]",
                                    })
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": "fallback-registry-list",
                                        "content": json.dumps(
                                            fallback_result, ensure_ascii=False, default=str
                                        )[:4000],
                                    })
                                    continue  # 继续下一轮让模型重新决策

                            final_content = response.content
                            break

                        # LLM 要调工具 → 执行所有 tool_calls
                        ANY_TOOL_CALL_TRIGGERED = True
                        messages.append({
                            "role": "assistant",
                            "content": response.content or "",
                            "tool_calls": [dict(tc) for tc in tool_calls],
                        })
                        for tc in tool_calls:
                            tool_name = (
                                tc.get("function", {}).get("name")
                                if isinstance(tc, dict) else None
                            )
                            tool_args_str = (
                                tc.get("function", {}).get("arguments", "{}")
                                if isinstance(tc, dict) else "{}"
                            )
                            tool_call_id = (
                                tc.get("id", "")
                                if isinstance(tc, dict) else ""
                            )
                            try:
                                tool_args = json.loads(tool_args_str)
                            except json.JSONDecodeError:
                                tool_args = {}
                            handler = REGISTRY_TOOL_HANDLERS.get(tool_name)
                            if handler is None:
                                tool_result = {"error": f"tool {tool_name} not available"}
                            else:
                                tool_result = await _run_tool_handler(
                                    handler=handler.handler,
                                    arguments=tool_args,
                                    tenant_id=tenant_id,
                                    run_id=run_id,
                                    agent_id=str(task_record.get("agent_id") or ""),
                                    session_id=session_id,
                                )
                            tool_calls_history.append({
                                "tool_name": tool_name,
                                "agentic": True,
                                "trigger": "agent_request",
                                "args": tool_args,
                                "result_preview": _truncate_json_for_preview(
                                    tool_result, max_chars=600
                                ),
                            })
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "content": json.dumps(
                                    tool_result, ensure_ascii=False, default=str
                                )[:4000],
                            })
                    else:
                        # 超过 5 轮还在调工具
                        final_content = response.content

                    logger.info(
                        "assistant run=%s rounds=%d tool_calls_total=%d triggered_by_agent=%d "
                        "fallback_injected=%s agentic_tools=yes",
                        run_id, rounds_used, len(tool_calls_history),
                        sum(1 for h in tool_calls_history if h.get("agentic")),
                        FALLBACK_INJECTED,
                    )

                    # --- output 兜底：如果模型只调工具没生成人话，从 tool_calls_history 生成执行摘要 ---
                    if not final_content.strip() and tool_calls_history:
                        final_content = _fallback_output(tool_calls_history, prompt) or "已执行完毕，但没有生成文本回复。"

                    return {
                        "output": final_content,
                        "usage": total_usage,
                        "tool_calls_history": tool_calls_history,
                        "rounds_used": rounds_used,
                        "any_tool_called": ANY_TOOL_CALL_TRIGGERED,
                    }

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
    run_context_resolver: Any | None = None,
) -> ProductionRunExecution:
    snapshots_by_tenant: dict[str, RunRegistrySnapshotService] = {}
    # 运行回放读取同一份持久化 trace 事件流。
    trace_service = RunTraceService(
        repository=PostgresRunTraceRepository(connection_factory=connection_factory),
    )

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
            skill_reviewer=_LocalSkillReviewer(),  # type: ignore[arg-type]
            trusted_tool_handlers=_registry_tool_handlers(),
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
        run_context_resolver=run_context_resolver,
        trace_service_resolver=lambda _tenant_id: trace_service,
        session_repository_resolver=lambda tenant_id: PostgresSessionRepository(
            tenant_id=tenant_id, connection_factory=connection_factory,
        ),
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
        run_context_resolver=run_context_resolver,
    )
