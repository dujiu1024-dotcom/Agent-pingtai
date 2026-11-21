"""Trusted registry management + task dispatch tool handlers for agent execution.

These handlers execute inside the Run context and are authorized by role.
Agent-side calls reach them through SupervisedToolGateway → build_tool_definitions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Sequence
from typing import Any, Mapping
from uuid import UUID, uuid4

from agent_platform.approval.policy import RiskLevel
from agent_platform.tools.gateway import ToolDefinition


def _dsn() -> str:
    import os
    dsn = os.environ.get("FDE_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if not dsn:
        raise RuntimeError("DATABASE_URL not configured for registry tool handlers")
    return dsn


async def _registry_conn():
    import psycopg
    from psycopg.rows import dict_row
    return await psycopg.AsyncConnection.connect(_dsn(), row_factory=dict_row)


async def _write_tool_audit(
    tool_name: str, arguments: Mapping[str, Any], outcome: str = "succeeded",
    detail: str = "",
) -> None:
    """Append a row to platform_tool_audit. Fire-and-forget: failures are swallowed."""
    try:
        tenant_id = arguments.get("_tenant_id") or ""
        if not tenant_id:
            return
        run_id = arguments.get("_run_id")
        agent_id = arguments.get("_agent_id")
        actor_id = agent_id or "system"
        public_args = {k: v for k, v in arguments.items() if not k.startswith("_")}
        action_digest = hashlib.sha256(
            json.dumps(public_args, sort_keys=True, default=str).encode()
        ).hexdigest()
        resource = f"tool:{tool_name}"
        conn = await _registry_conn()
        async with conn:
            await conn.execute(
                """INSERT INTO platform_tool_audit
                   (tenant_id, actor_id, action, resource, action_digest,
                    outcome, run_id, agent_id, detail)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (tenant_id, actor_id, f"tool.{tool_name}", resource,
                 action_digest, outcome, run_id, agent_id, detail),
            )
    except Exception:
        pass  # audit 失败不能影响工具结果


def audit_tool(tool_name: str):
    """Decorator: wrap a tool handler and write a tool_audit row on exit.

    Usage:
        @audit_tool("ledger_query")
        async def _ledger_query_handler(arguments): ...
    """
    def _wrap(fn):
        async def _inner(arguments: Mapping[str, Any], *args, **kwargs) -> Any:
            outcome = "failed"
            detail = ""
            try:
                result = await fn(arguments, *args, **kwargs)
                outcome = "succeeded"
                return result
            except Exception as exc:
                outcome = "failed"
                detail = str(exc)[:200]
                raise
            finally:
                await _write_tool_audit(tool_name, arguments, outcome, detail)
        # Preserve original name for introspection
        _inner.__name__ = fn.__name__
        return _inner
    return _wrap


@audit_tool("registry_list_agents")
async def _list_agents_handler(arguments: Mapping[str, Any]) -> Any:
    tenant_id = arguments.get("_tenant_id")
    if not tenant_id:
        raise ValueError("_tenant_id required")
    conn = await _registry_conn()
    async with conn:
        cur = await conn.execute(
            "SELECT v.item_id, v.version, v.definition->>'name' AS name, "
            "v.definition->>'description' AS description, "
            "v.definition->>'instructions' AS instructions "
            "FROM platform_registry_active a "
            "JOIN platform_registry_versions v ON v.tenant_id=a.tenant_id "
            "AND v.kind=a.kind AND v.item_id=a.item_id "
            "AND v.version=a.active_version "
            "WHERE a.tenant_id=%s AND a.kind='agent' "
            "ORDER BY v.item_id",
            (tenant_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    return {"agents": rows, "count": len(rows)}


@audit_tool("registry_create_agent")
async def _create_agent_handler(arguments: Mapping[str, Any]) -> Any:
    tenant_id = arguments.get("_tenant_id")
    item_id = arguments.get("agent_id", "").strip()
    name = arguments.get("name", "").strip()
    instructions = arguments.get("instructions", "").strip()
    description = arguments.get("description", "").strip()
    template_id = arguments.get("model_template_id", "local-template").strip()

    if not tenant_id or not item_id or not name:
        raise ValueError("agent_id and name are required")

    definition: dict[str, Any] = {
        "name": name,
        "description": description or name,
        "instructions": instructions or "",
        "tool_ids": [],
        "skill_ids": [],
        "workflow_id": None,
        "model_template_id": template_id,
        "model_template_version": None,
    }

    conn = await _registry_conn()
    async with conn:
        # 查当前版本
        cur = await conn.execute(
            """SELECT COALESCE(MAX(version), 0) as current_version
               FROM platform_registry_versions
               WHERE tenant_id=%s AND kind='agent' AND item_id=%s""",
            (tenant_id, item_id),
        )
        row = await cur.fetchone()
        current_version = (row or {}).get("current_version", 0)

        if current_version > 0:
            raise ValueError(f"agent {item_id} already exists (v{current_version}). Use registry_update_agent instead.")

        # 首次创建：写 v1 + 激活
        await conn.execute(
            """INSERT INTO platform_registry_versions
               (tenant_id, kind, item_id, version, definition, status, created_by, created_at)
               VALUES (%s, 'agent', %s, 1, %s, 'published', 'registry-tool', now())""",
            (tenant_id, item_id, json.dumps(definition, ensure_ascii=False)),
        )
        await conn.execute(
            """INSERT INTO platform_registry_active (tenant_id, kind, item_id, active_version, updated_at)
               VALUES (%s, 'agent', %s, 1, now())""",
            (tenant_id, item_id),
        )
    return {"agent_id": item_id, "version": 1, "status": "published"}


@audit_tool("registry_update_agent")
async def _update_agent_handler(arguments: Mapping[str, Any]) -> Any:
    tenant_id = arguments.get("_tenant_id")
    item_id = arguments.get("agent_id", "").strip()
    if not tenant_id or not item_id:
        raise ValueError("agent_id required")

    conn = await _registry_conn()
    async with conn:
        # 读当前 active version 的 definition
        cur = await conn.execute(
            """SELECT v.version, v.definition FROM platform_registry_versions v
               JOIN platform_registry_active a ON a.tenant_id=v.tenant_id
               AND a.kind=v.kind AND a.item_id=v.item_id AND a.active_version=v.version
               WHERE a.tenant_id=%s AND a.kind='agent' AND a.item_id=%s""",
            (tenant_id, item_id),
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError(f"agent {item_id} not found")
        current = row["definition"] or {}
        current_version = row["version"]

        # 合并更新字段
        updatable_keys = ["name", "description", "instructions", "tool_ids", "skill_ids",
                          "workflow_id", "model_template_id"]
        for key in updatable_keys:
            if key in arguments and not key.startswith("_"):
                current[key] = arguments[key]

        new_version = current_version + 1
        await conn.execute(
            """INSERT INTO platform_registry_versions
               (tenant_id, kind, item_id, version, definition, status, created_by, created_at)
               VALUES (%s, 'agent', %s, %s, %s, 'published', 'registry-tool', now())""",
            (tenant_id, item_id, new_version, json.dumps(current, ensure_ascii=False)),
        )
        await conn.execute(
            """UPDATE platform_registry_active SET active_version=%s, updated_at=now()
               WHERE tenant_id=%s AND kind='agent' AND item_id=%s""",
            (new_version, tenant_id, item_id),
        )
    return {"agent_id": item_id, "old_version": current_version, "new_version": new_version}


@audit_tool("task_dispatch")
async def _dispatch_task_handler(arguments: Mapping[str, Any], context: Any = None) -> Any:
    """让当前 agent 把一个活转派给另一个 agent。

    流程：
    1. 从 registry 读目标 agent 的 active version + model template + provider + tools + skills
    2. 算 registry_snapshot + digest
    3. INSERT platform_tasks（先建任务，让任务中心可见）
    4. INSERT platform_runs (status=queued, dispatch_ready=true, task_id 指向新建 task)
    5. 回填 platform_tasks.run_id
    6. 同步轮询最多 wait_seconds，等子 Run 跑完就带 output 回来；
       超时或失败也返回 run_id 让 agent 可以 fire-and-forget。
    """
    tenant_id = arguments.get("_tenant_id")
    parent_run_id = arguments.get("_run_id")
    parent_session_id = arguments.get("_session_id")  # 父对话 session（用于子任务完成后异步回写）
    actor_agent_id = arguments.get("_agent_id")  # 交办方（当前 agent）
    agent_id = arguments.get("agent_id", "").strip()
    prompt = arguments.get("prompt", "").strip()
    wait_seconds = int(arguments.get("wait_seconds", 60))

    if not tenant_id or not agent_id or not prompt:
        raise ValueError("agent_id and prompt are required")
    if wait_seconds > 300:
        wait_seconds = 300

    # === Self-dispatch guard ===
    # Agent 不能 dispatch 给自己——会造成无限循环 queued。
    current_agent = actor_agent_id or (
        getattr(context, "agent_id", None) if context is not None else None
    )
    if current_agent and current_agent == agent_id:
        return {
            "status": "rejected",
            "reason": "self_dispatch",
            "message": (
                "这个任务的目标就是你自己，不能派给自己。"
                "请直接处理，或者转派给其他智能体。"
            ),
        }

    conn = await _registry_conn()
    async with conn:
        # 1. 读目标 agent 定义
        cur = await conn.execute(
            """SELECT a.active_version, v.definition, 
                      v.definition->>'model_template_id' AS template_id
               FROM platform_registry_active a
               JOIN platform_registry_versions v
                 ON v.tenant_id=a.tenant_id AND v.kind=a.kind
                 AND v.item_id=a.item_id AND v.version=a.active_version
               WHERE a.tenant_id=%s AND a.kind='agent' AND a.item_id=%s""",
            (tenant_id, agent_id),
        )
        row = await cur.fetchone()
        if not row:
            raise ValueError(f"agent {agent_id} not found or not published")
        agent_version = int(row["active_version"])
        agent_def: dict[str, Any] = row["definition"] or {}
        # 面向用户的文案统一用智能体中文名，不暴露内部 id
        target_name = str(agent_def.get("name") or agent_id)
        template_id = agent_def.get("model_template_id") or "local-template"
        tool_ids: Sequence[str] = agent_def.get("tool_ids") or []
        skill_ids: Sequence[str] = agent_def.get("skill_ids") or []
        workflow_id = agent_def.get("workflow_id")

        # 2. 读 template + provider + tool/skill 版本
        cur = await conn.execute(
            """SELECT a.active_version AS template_version,
                      t.definition->>'provider_id' AS provider_id,
                      t.definition->>'provider_version' AS provider_version
               FROM platform_registry_active a
               JOIN platform_registry_versions t
                 ON t.tenant_id=a.tenant_id AND t.kind=a.kind
                 AND t.item_id=a.item_id AND t.version=a.active_version
               WHERE a.tenant_id=%s AND a.kind='model_template' AND a.item_id=%s""",
            (tenant_id, template_id),
        )
        tpl_row = await cur.fetchone()
        if not tpl_row:
            raise ValueError(f"model_template {template_id} not found")
        template_version = int(tpl_row["template_version"])
        provider_id = tpl_row["provider_id"]
        provider_version = int(tpl_row["provider_version"])

        cur = await conn.execute(
            """SELECT active_version FROM platform_registry_active
               WHERE tenant_id=%s AND kind='model_provider' AND item_id=%s""",
            (tenant_id, provider_id),
        )
        prov_row = await cur.fetchone()
        if not prov_row or int(prov_row["active_version"]) != provider_version:
            raise ValueError(f"model_provider {provider_id} active version mismatch")

        # 3. 拼 registry_snapshot（**必须包含 definition 字段**，否则 PostgresRunSnapshotStore 不认）
        async def _entry(kind: str, item_id: str, version: int) -> dict[str, Any]:
            cur2 = await conn.execute(
                "SELECT definition FROM platform_registry_versions "
                "WHERE tenant_id=%s AND kind=%s AND item_id=%s AND version=%s",
                (tenant_id, kind, item_id, version),
            )
            row2 = await cur2.fetchone()
            return {"kind": kind, "item_id": item_id, "version": version,
                    "definition": dict(row2["definition"])} if row2 else {"kind": kind, "item_id": item_id, "version": version}

        snapshot_entries: list[dict[str, Any]] = [
            await _entry("agent", agent_id, agent_version),
            await _entry("model_template", template_id, template_version),
            await _entry("model_provider", provider_id, provider_version),
        ]
        for tid in tool_ids:
            cur2 = await conn.execute(
                "SELECT active_version FROM platform_registry_active WHERE tenant_id=%s AND kind='tool' AND item_id=%s",
                (tenant_id, tid),
            )
            v2 = await cur2.fetchone()
            if v2: snapshot_entries.append(await _entry("tool", tid, int(v2["active_version"])))
        for sid in skill_ids:
            cur2 = await conn.execute(
                "SELECT active_version FROM platform_registry_active WHERE tenant_id=%s AND kind='skill' AND item_id=%s",
                (tenant_id, sid),
            )
            v2 = await cur2.fetchone()
            if v2: snapshot_entries.append(await _entry("skill", sid, int(v2["active_version"])))
        if workflow_id:
            cur2 = await conn.execute(
                "SELECT active_version FROM platform_registry_active WHERE tenant_id=%s AND kind='workflow' AND item_id=%s",
                (tenant_id, workflow_id),
            )
            v2 = await cur2.fetchone()
            if v2: snapshot_entries.append(await _entry("workflow", workflow_id, int(v2["active_version"])))

        # 4. 生成 run_id + task_id + 算 digest（必须在算 digest 之前，因为 digest 包含 run_id）
        sub_run_id = str(uuid4())
        sub_task_id = str(uuid4())

        # entries 必须按 (kind, item_id) 排序（和 RunRegistrySnapshotService.pin_versions 一致）
        snapshot_entries.sort(key=lambda e: (e["kind"], e["item_id"]))
        canonical = json.dumps(
            {"tenant_id": tenant_id, "run_id": sub_run_id, "entries": snapshot_entries},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        snapshot_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot_json = json.dumps(snapshot_entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

        # 5. CREATE Task（先建任务，让任务中心可见）
        #    actor_id = 交办方 agent；agent_id = 执行方 agent
        #    input = 原始 prompt
        actor_id = actor_agent_id or "task-dispatch-tool"
        # 子任务归属父 Run 的发起人，否则任务看板按 created_by 过滤时会把派发出去的任务全部隐藏。
        dispatcher = "task-dispatch-tool"
        if parent_run_id:
            cur = await conn.execute(
                "SELECT created_by FROM platform_runs WHERE tenant_id=%s AND run_id=%s::uuid",
                (tenant_id, parent_run_id),
            )
            parent_row = await cur.fetchone()
            if parent_row and parent_row["created_by"]:
                dispatcher = str(parent_row["created_by"])
        await conn.execute(
            """INSERT INTO platform_tasks
              (tenant_id, task_id, actor_id, agent_id, input, priority,
               project_id, session_id, status, created_at, updated_at)
               VALUES
              (%s, %s::uuid, %s, %s, %s::jsonb, 100,
               NULL, NULL, 'queued', now(), now())""",
            (tenant_id, sub_task_id, actor_id, agent_id,
             json.dumps({"prompt": prompt, "parent_run_id": parent_run_id}, ensure_ascii=False)),
        )

        # 6. CREATE Run（task_id 指向刚创建的 platform_tasks.task_id；dispatch_ready=true 让 worker claim）
        preparation_owner = str(uuid4())
        await conn.execute(
            """INSERT INTO platform_runs
              (tenant_id, run_id, task_id, agent_id, agent_version, template_id, template_version,
               status, input, created_by, idempotency_key, registry_snapshot, registry_snapshot_digest,
               parent_run_id, budget_root_run_id, budget_lineage_kind, preparation_owner,
               preparation_lease_expires_at, dispatch_ready, priority, version, updated_at)
               VALUES
              (%s, %s::uuid, %s::uuid, %s, %s, %s, %s,
               'queued', %s::jsonb, %s, NULL, %s::jsonb, %s,
               NULL, %s::uuid, 'root', %s,
               now() + interval '30 seconds', true, 100, 1, now())""",
            (tenant_id, sub_run_id, sub_task_id, agent_id, agent_version, template_id, template_version,
             json.dumps({"prompt": prompt}),
             dispatcher, snapshot_json, snapshot_digest,
             sub_run_id, preparation_owner),
        )

        # 7. 回填 platform_tasks.run_id → 让任务可以追踪到 Run 状态
        await conn.execute(
            """UPDATE platform_tasks SET run_id=%s::uuid, updated_at=now()
               WHERE tenant_id=%s AND task_id=%s::uuid""",
            (sub_run_id, tenant_id, sub_task_id),
        )
        await conn.commit()

    # === 后台协程：子任务完成后自动回写父对话 ===
    # 同步等待窗口内可能等不到，但后台会继续监控最长 10 分钟
    # 子任务完成后把结果追加到父 session 的 ASSISTANT 消息里
    if parent_session_id and parent_run_id:
        import uuid as _uuid

        async def _async_callback():
            """子 Run 终态后 → 写 platform_messages 回父对话。"""
            try:
                # 继续监控最长 10 分钟
                cb_deadline = asyncio.get_event_loop().time() + 600
                while asyncio.get_event_loop().time() < cb_deadline:
                    await asyncio.sleep(2)
                    try:
                        cb_conn = await _registry_conn()
                        async with cb_conn:
                            cur = await cb_conn.execute(
                                "SELECT status, output, last_error FROM platform_runs WHERE tenant_id=%s AND run_id=%s::uuid",
                                (tenant_id, sub_run_id),
                            )
                            row = await cur.fetchone()
                            if not row:
                                continue
                            st = row["status"]
                            if st == "succeeded":
                                out = row.get("output") or {}
                                text = ""
                                if isinstance(out, dict) and isinstance(out.get("output"), str):
                                    text = out["output"]
                                elif isinstance(out, str):
                                    text = out
                                else:
                                    text = json.dumps(out, ensure_ascii=False)[:500]
                                content = (
                                    f"**【{target_name}】交办任务已完成**\n\n{text}"
                                )
                                break
                            elif st in ("failed", "timed_out", "cancelled"):
                                err = row.get("last_error") or "未知错误"
                                status_zh = {"failed": "执行失败", "timed_out": "执行超时", "cancelled": "已取消"}.get(st, "未完成")
                                content = (
                                    f"**【{target_name}】交办任务未完成**（{status_zh}）\n\n{err}"
                                )
                                break
                            else:
                                continue
                    except Exception:
                        continue
                else:
                    # 10 分钟到了还没终态
                    return

                # 写入父 session 的 platform_messages
                async def _write_msg():
                    try:
                        msg_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"dispatch-callback:{sub_run_id}"))
                        w_conn = await _registry_conn()
                        async with w_conn:
                            # 检查 session 还活跃
                            scur = await w_conn.execute(
                                "SELECT 1 FROM platform_conversation_sessions WHERE tenant_id=%s AND session_id=%s::uuid AND status='active'",
                                (tenant_id, parent_session_id),
                            )
                            if await scur.fetchone() is None:
                                return  # session 已关闭，放弃
                            # INSERT message（ON CONFLICT 保证幂等）
                            await w_conn.execute(
                                """INSERT INTO platform_messages
                                    (tenant_id,message_id,session_id,role,content,actor_id,run_id,
                                     metadata,created_at)
                                    VALUES (%s,%s::uuid,%s::uuid,'assistant',%s,%s,%s::uuid,
                                     %s::jsonb,now())
                                    ON CONFLICT (tenant_id,message_id) DO NOTHING""",
                                (tenant_id, msg_id, parent_session_id, content,
                                 f"task-dispatch→{agent_id}", sub_run_id,
                                 json.dumps({"source": "task_dispatch_callback",
                                            "parent_run_id": parent_run_id,
                                            "child_run_id": sub_run_id})),
                            )
                            await w_conn.execute(
                                "UPDATE platform_conversation_sessions SET updated_at=now() WHERE tenant_id=%s AND session_id=%s::uuid",
                                (tenant_id, parent_session_id),
                            )
                    except Exception:
                        pass  # 后台回调失败静默吞掉，不影响主流程

                await _write_msg()
            except Exception:
                pass

        asyncio.create_task(_async_callback())

    # 5. 同步轮询等子 Run 跑完
    deadline = asyncio.get_event_loop().time() + wait_seconds
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.5)
        try:
            conn2 = await _registry_conn()
            async with conn2:
                cur = await conn2.execute(
                    "SELECT status, output, last_error FROM platform_runs WHERE tenant_id=%s AND run_id=%s::uuid",
                    (tenant_id, sub_run_id),
                )
                row = await cur.fetchone()
                if not row: continue
                status = row["status"]
                if status == "succeeded":
                    output = row.get("output") or {}
                    if isinstance(output, dict) and "output" in output:
                        return {
                            "run_id": sub_run_id,
                            "agent_id": agent_id,
                            "status": "succeeded",
                            "agent_output": output["output"],
                            "usage": output.get("usage", {}),
                        }
                    return {"run_id": sub_run_id, "agent_id": agent_id, "status": "succeeded", "agent_output": output}
                elif status in ("failed", "timed_out", "cancelled"):
                    return {
                        "run_id": sub_run_id,
                        "agent_id": agent_id,
                        "status": status,
                        "error": row.get("last_error") or "unknown error",
                    }
        except Exception:
            continue

    # 超时 —— 但 Run 已经入队，worker 会自然处理
    return {
        "run_id": sub_run_id,
        "agent_id": agent_id,
        "status": "queued",
        "message": f"已转派给「{target_name}」，正在处理中；完成后结果会自动回到这里。",
        "waited_seconds": wait_seconds,
    }


def _tenant_roles_allowed(ctx, args):
    """Allow any role for registry management tools in local runtime."""
    return True


# ──────────────────────────────────────────────────────────────
# Finance Ledger Tools
# 账本文件基于 JSON 持久化在本地目录，tenant 隔离
# 路径：<LEDGER_ROOT>/<tenant_id>/ledger.json
# ──────────────────────────────────────────────────────────────

from datetime import datetime, timezone
from decimal import Decimal

LEDGER_ROOT = "/Users/longyi/FDE工程/finance"


def _ledger_path(tenant_id: str, ledger_id: str = "main") -> str:
    """Resolve filesystem path for a ledger file.

    tenant_id is used as directory component for multitenant isolation.
    ledger_id defaults to 'main'; pass a short slug for project ledgers.
    """
    safe_tenant = "".join(c for c in tenant_id if c.isalnum() or c in "-_")
    safe_ledger = "".join(c for c in ledger_id if c.isalnum() or c in "-_") or "main"
    directory = f"{LEDGER_ROOT}/{safe_tenant}"
    os.makedirs(directory, exist_ok=True)
    return f"{directory}/{safe_ledger}.json"


@audit_tool("ledger_create")
async def _ledger_create_handler(arguments: Mapping[str, Any]) -> Any:
    """Create (or verify) a ledger row in platform_finance_ledger.

    Idempotent: if a row already exists for this tenant_id + ledger_id,
    return it without overwriting balances.
    """
    tenant_id = arguments.get("_tenant_id", "default")
    ledger_id = arguments.get("ledger_id", "main")
    name = arguments.get("name", "FDE 工作室")
    currency = arguments.get("currency", "CNY")
    opening_date = arguments.get("opening_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conn = await _registry_conn()
    try:
        async with conn.cursor() as cur:
            # 先查有没有
            await cur.execute(
                """SELECT ledger_id, ledger_name, currency, opening_date, current_balance
                   FROM platform_finance_ledger
                   WHERE tenant_id = %s AND ledger_id = %s""",
                (tenant_id, ledger_id),
            )
            row = await cur.fetchone()
            if row:
                return {
                    "status": "exists",
                    "ledger_id": ledger_id,
                    "ledger": {
                        "name": row["ledger_name"],
                        "currency": row["currency"],
                        "opening_date": str(row["opening_date"]) if row["opening_date"] else None,
                        "current_balance": str(row["current_balance"]),
                    },
                    "path": f"db:platform_finance_ledger/{tenant_id}/{ledger_id}",
                }

            await cur.execute(
                """INSERT INTO platform_finance_ledger
                   (tenant_id, ledger_id, ledger_name, currency, opening_date)
                   VALUES (%s, %s, %s, %s, %s)""",
                (tenant_id, ledger_id, name, currency, opening_date),
            )
        await conn.commit()
    finally:
        await conn.close()

    return {
        "status": "created",
        "ledger_id": ledger_id,
        "ledger": {"name": name, "currency": currency, "current_balance": "0.00"},
        "path": f"db:platform_finance_ledger/{tenant_id}/{ledger_id}",
    }


@audit_tool("ledger_add_entry")
async def _ledger_add_entry_handler(arguments: Mapping[str, Any]) -> Any:
    """Record an income or expense entry.

    🔒 安全门禁：
    - proof_file_id 必须填写（发票/收据/转账截图等已上传文件的 asset_id）
    - 系统校验该文件确实存在于 platform_file_assets 且属于当前 tenant
    - 必须先调用 ledger_create 确保账本已存在
    """
    tenant_id = arguments.get("_tenant_id", "default")
    agent_id = arguments.get("_agent_id")
    run_id = arguments.get("_run_id")
    ledger_id = arguments.get("ledger_id", "main")
    entry_type = arguments.get("entry_type", "").strip().lower()
    amount = arguments.get("amount")
    category = arguments.get("category", "").strip()
    description = arguments.get("description", "").strip()
    entry_date = arguments.get("entry_date")
    proof_file_id = arguments.get("proof_file_id", "").strip()

    # ── 门禁校验 ──────────────────────────────────
    if entry_type not in ("income", "expense"):
        raise ValueError("entry_type must be 'income' or 'expense'")
    try:
        decimal_amount = Decimal(str(amount))
        if decimal_amount <= 0:
            raise ValueError
    except Exception:
        raise ValueError("amount must be a positive number")
    if not category:
        raise ValueError("category is required (e.g. 工资, 办公费, 服务费)")
    # proof_file_id 必须给
    if not proof_file_id or not isinstance(proof_file_id, str) or len(proof_file_id) < 8:
        raise ValueError(
            "proof_file_id is required: the asset_id of an uploaded file (invoice/receipt/screenshot). "
            "Upload the proof file first via the file upload API, then pass its asset_id here."
        )

    conn = await _registry_conn()
    try:
        async with conn.cursor() as cur:
            # 1. 账本必须存在
            await cur.execute(
                "SELECT ledger_id FROM platform_finance_ledger WHERE tenant_id = %s AND ledger_id = %s",
                (tenant_id, ledger_id),
            )
            if not await cur.fetchone():
                raise ValueError(f"ledger '{ledger_id}' does not exist — call ledger_create first")

            # 2. proof_file_id 必须属于当前 tenant 且真实存在
            await cur.execute(
                """SELECT asset_id FROM platform_file_assets
                   WHERE tenant_id = %s AND asset_id = %s::uuid AND status = 'available'""",
                (tenant_id, proof_file_id),
            )
            proof = await cur.fetchone()
            if not proof:
                raise ValueError(
                    f"proof_file_id '{proof_file_id}' does not exist in tenant '{tenant_id}' "
                    "or is not available. Upload the invoice/receipt as a file asset first."
                )

            # 3. 插入流水（触发器自动更新 balance）
            await cur.execute(
                """INSERT INTO platform_finance_entries
                   (tenant_id, ledger_id, entry_date, type, amount, category, description,
                    run_id, agent_id, proof_file_id)
                   VALUES (%s, %s, %s::date, %s, %s, %s, %s, %s::uuid, %s, %s::uuid)
                   RETURNING entry_id, created_at""",
                (tenant_id, ledger_id,
                 entry_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                 entry_type, decimal_amount, category, description or None,
                 run_id, agent_id, proof_file_id),
            )
            insert_row = await cur.fetchone()

            # 4. 取最新余额（触发器已更新）
            await cur.execute(
                "SELECT current_balance, currency FROM platform_finance_ledger WHERE tenant_id = %s AND ledger_id = %s",
                (tenant_id, ledger_id),
            )
            ledger_row = await cur.fetchone()
        await conn.commit()
    finally:
        await conn.close()

    return {
        "status": "recorded",
        "entry_id": str(insert_row["entry_id"]),
        "type": entry_type,
        "amount": str(decimal_amount.quantize(Decimal("0.01"))),
        "category": category,
        "entry_date": entry_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "balance_after": str(ledger_row["current_balance"]),
        "proof_file_id": proof_file_id,
        "message": f"✅ {entry_type} {decimal_amount} 已记账，余额 {ledger_row['current_balance']} {ledger_row['currency']}",
    }


@audit_tool("ledger_query")
async def _ledger_query_handler(arguments: Mapping[str, Any]) -> Any:
    """Read ledger status + entries from platform_finance_entries."""
    tenant_id = arguments.get("_tenant_id", "default")
    ledger_id = arguments.get("ledger_id", "main")
    limit = int(arguments.get("limit", 20))
    limit = min(max(limit, 1), 500)  # 强制 1..500 上限
    entry_type = arguments.get("entry_type")
    date_from = arguments.get("date_from")
    date_to = arguments.get("date_to")
    category = arguments.get("category")

    # 构建安全 WHERE（tenant_id 必带 + 表别名 e. 避免 JOIN 歧义）
    where_parts = ["e.tenant_id = %s", "e.ledger_id = %s"]
    params: list[Any] = [tenant_id, ledger_id]
    if entry_type:
        where_parts.append("e.type = %s")
        params.append(entry_type)
    if category:
        where_parts.append("e.category = %s")
        params.append(category)
    if date_from:
        where_parts.append("e.entry_date >= %s::date")
        params.append(date_from)
    if date_to:
        where_parts.append("e.entry_date <= %s::date")
        params.append(date_to)
    where_sql = " AND ".join(where_parts)

    conn = await _registry_conn()
    try:
        async with conn.cursor() as cur:
            # 账本存在性
            await cur.execute(
                "SELECT ledger_name, currency, current_balance, opening_date FROM platform_finance_ledger WHERE tenant_id = %s AND ledger_id = %s",
                (tenant_id, ledger_id),
            )
            ledger_row = await cur.fetchone()
            if not ledger_row:
                return {"status": "not_found", "message": f"ledger '{ledger_id}' does not exist"}

            # 流水 + proof 文件链接（e=entries, f=files）
            await cur.execute(
                f"""SELECT e.entry_id::text, e.type, e.amount::text, e.category, e.description,
                           e.entry_date::text, e.created_at::text, e.proof_file_id::text,
                           f.filename as proof_filename
                    FROM platform_finance_entries e
                    LEFT JOIN platform_file_assets f
                           ON f.tenant_id = e.tenant_id AND f.asset_id = e.proof_file_id
                    WHERE {where_sql}
                    ORDER BY e.entry_date DESC, e.created_at DESC
                    LIMIT %s""",
                params + [limit],
            )
            rows = await cur.fetchall()
    finally:
        await conn.close()

    entries = [dict(r) for r in rows]
    return {
        "status": "ok",
        "ledger": {
            "name": ledger_row["ledger_name"],
            "currency": ledger_row["currency"],
            "current_balance": str(ledger_row["current_balance"]),
            "opening_date": str(ledger_row["opening_date"]) if ledger_row["opening_date"] else None,
        },
        "entries": entries,
        "filter": {
            "entry_type": entry_type, "category": category,
            "date_from": date_from, "date_to": date_to, "limit": limit,
        },
        "path": f"db:platform_finance_entries/{tenant_id}/{ledger_id}",
    }


@audit_tool("ledger_report")
async def _ledger_report_handler(arguments: Mapping[str, Any]) -> Any:
    """Generate income/expense summary + by-category breakdown from DB."""
    tenant_id = arguments.get("_tenant_id", "default")
    ledger_id = arguments.get("ledger_id", "main")
    period = arguments.get("period", "all")  # "month" or "all"

    conn = await _registry_conn()
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT ledger_name, currency, opening_date FROM platform_finance_ledger WHERE tenant_id = %s AND ledger_id = %s",
                (tenant_id, ledger_id),
            )
            ledger_row = await cur.fetchone()
            if not ledger_row:
                return {"status": "not_found", "message": f"ledger '{ledger_id}' does not exist"}

            period_start = None
            if period == "month":
                now = datetime.now(timezone.utc)
                period_start = now.strftime("%Y-%m-01")

            where = ["tenant_id = %s", "ledger_id = %s"]
            params: list[Any] = [tenant_id, ledger_id]
            if period_start:
                where.append("entry_date >= %s::date")
                params.append(period_start)
            where_sql = " AND ".join(where)

            # 汇总
            await cur.execute(
                f"""SELECT
                      COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0) as income,
                      COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0) as expense,
                      COUNT(*) as entry_count
                    FROM platform_finance_entries
                    WHERE {where_sql}""",
                params,
            )
            summary = await cur.fetchone()

            # 按类别
            await cur.execute(
                f"""SELECT category, type, SUM(amount) as total
                    FROM platform_finance_entries
                    WHERE {where_sql}
                    GROUP BY category, type""",
                params,
            )
            cat_rows = await cur.fetchall()
    finally:
        await conn.close()

    d = Decimal
    by_category: dict[str, dict[str, str]] = {}
    for r in cat_rows:
        cat = r["category"]
        bucket = by_category.setdefault(cat, {"income": "0.00", "expense": "0.00"})
        total = str(d(str(r["total"])).quantize(d("0.01")))
        bucket[r["type"]] = total

    income = d(str(summary["income"])).quantize(d("0.01"))
    expense = d(str(summary["expense"])).quantize(d("0.01"))

    return {
        "status": "ok",
        "period": period,
        "period_start": period_start or str(ledger_row["opening_date"]),
        "currency": ledger_row["currency"],
        "summary": {
            "income": str(income),
            "expense": str(expense),
            "net": str((income - expense).quantize(d("0.01"))),
            "entry_count": summary["entry_count"],
        },
        "by_category": by_category,
        "path": f"db:platform_finance_entries/{tenant_id}/{ledger_id}",
    }


@audit_tool("registry_list_tools")
async def _list_tools_handler(arguments: Mapping[str, Any]) -> Any:
    """List all registered tools (from both DB and handlers) with descriptions."""
    tenant_id = arguments.get("_tenant_id")
    if not tenant_id:
        raise ValueError("_tenant_id required")

    # 1) 从 DB 拿已注册 tool 的元信息 + input_schema
    conn = await _registry_conn()
    async with conn:
        cur = await conn.execute(
            """SELECT v.item_id, v.version,
                      v.definition->>'description' AS description,
                      v.definition->'input_schema' AS db_schema
               FROM platform_registry_active a
               JOIN platform_registry_versions v ON v.tenant_id=a.tenant_id
               AND v.kind=a.kind AND v.item_id=a.item_id
               AND v.version=a.active_version
               WHERE a.tenant_id=%s AND a.kind='tool'
               ORDER BY v.item_id""",
            (tenant_id,),
        )
        db_tools: dict[str, dict[str, Any]] = {}
        for row in await cur.fetchall():
            db_tools[row["item_id"]] = dict(row)

    # 2) 从 REGISTRY_TOOL_HANDLERS 拿 handler description + input_schema
    results: list[dict[str, Any]] = []
    for tool_id, tool_def in REGISTRY_TOOL_HANDLERS.items():
        db_meta = db_tools.get(tool_id, {})
        schema = tool_def.input_schema or db_meta.get("db_schema")
        results.append({
            "tool_id": tool_id,
            "description": tool_def.description or db_meta.get("description", ""),
            "risk": str(tool_def.risk),
            "input_schema": schema,
            "in_db": tool_id in db_tools,
            "db_version": db_meta.get("version"),
        })
    results.sort(key=lambda t: t["tool_id"])
    return {"tools": results, "count": len(results)}


# ── Agent Factory ──────────────────────────────────────────────────────────

# 工具关键词到 tool_id 的映射，用于 agent_factory 的自动匹配
_TOOL_KEYWORD_MAP: dict[str, tuple[str, ...]] = {
    "ledger_create": ("账本", "ledger", "建账", "创建账", "新建账"),
    "ledger_add_entry": ("记账", "收入", "支出", "流水", "录账", "入账", "payment", "invoice"),
    "ledger_query": ("查账", "余额", "账本查询", "流水查询", "ledger query", "查看账"),
    "ledger_report": ("报表", "月报", "财务报表", "总结", "汇总", "report"),
    "registry_list_agents": ("agent 列表", "有哪些 agent", "agent 名单", "团队", "组织架构", "同事"),
    "registry_create_agent": ("创建 agent", "新建 agent", "招人", "招聘 agent", "注册 agent"),
    "registry_update_agent": ("更新 agent", "改 agent", "调整 agent", "修改 agent"),
    "registry_list_tools": ("工具列表", "有什么工具", "工具清单", "tool 列表", "可用工具"),
    "agent_factory": ("招人", "创建 agent", "工厂", "agent 工厂", "快速建 agent", "招聘"),
    "task_dispatch": ("转派", "dispatch", "委托", "派活", "分给", "delegate"),
}


def _match_tools_by_description(description: str) -> list[str]:
    """对一段职责描述做关键词匹配，返回推荐的 tool_ids。"""
    if not description:
        return []
    lower = description.lower()
    matched: list[str] = []
    for tool_id, keywords in _TOOL_KEYWORD_MAP.items():
        for kw in keywords:
            if kw.lower() in lower:
                matched.append(tool_id)
                break
    # 默认：如果描述里提到"agent"或"同事"，总是给 registry_list_agents
    if "registry_list_agents" not in matched and (
        "agent" in lower or "同事" in description or "团队" in description or "其他" in description
    ):
        matched.append("registry_list_agents")
    # 默认：如果没明确提工具但可能需要帮别人办事，给 task_dispatch
    if "task_dispatch" not in matched and matched and any(
        k in lower for k in ("帮", "协助", "委托", "转派", "其他 agent")
    ):
        matched.append("task_dispatch")
    return matched


@audit_tool("agent_factory")
async def _agent_factory_handler(arguments: Mapping[str, Any]) -> Any:
    """一站式 agent 创建/更新。

    如果 agent_id 不存在 → 创建 v1；已存在 → 更新到新版本。
    tool_ids 不填时，会根据 responsibilities 自动匹配。
    """
    tenant_id = arguments.get("_tenant_id")
    if not tenant_id:
        raise ValueError("_tenant_id required")

    agent_id = str(arguments.get("agent_id", "") or "").strip()
    name = str(arguments.get("name", "") or "").strip()
    responsibilities = str(arguments.get("responsibilities", "") or "").strip()
    tool_ids_input = arguments.get("tool_ids")
    instructions = str(arguments.get("instructions", "") or "").strip()
    template_id = str(arguments.get("model_template_id", "local-template") or "local-template").strip()
    force = bool(arguments.get("force", False))  # 自动匹配不精确时强制应用

    if not agent_id:
        raise ValueError("agent_id is required")
    if not name:
        raise ValueError("name is required")
    if not responsibilities and not tool_ids_input:
        raise ValueError("either responsibilities or tool_ids must be provided")

    # 解析 tool_ids
    if isinstance(tool_ids_input, list):
        resolved_tools = [str(t).strip() for t in tool_ids_input if str(t).strip()]
    elif tool_ids_input is None or tool_ids_input == "auto":
        resolved_tools = _match_tools_by_description(responsibilities or instructions)
    else:
        resolved_tools = [str(tool_ids_input).strip()]

    # 把 registry 自管工具也默认带上
    for t in ("registry_list_agents", "registry_list_tools", "task_dispatch"):
        if t not in resolved_tools:
            resolved_tools.append(t)

    # 校验 tool 是否在 REGISTRY_TOOL_HANDLERS 里
    unknown = [t for t in resolved_tools if t not in REGISTRY_TOOL_HANDLERS]
    if unknown:
        raise ValueError(f"unknown tools: {unknown}")

    # 自动生成 instructions（用户没给的时候）
    if not instructions:
        tool_descs = []
        for tid in resolved_tools:
            tdef = REGISTRY_TOOL_HANDLERS.get(tid)
            if tdef:
                tool_descs.append(f"- {tid}: {tdef.description[:80]}")
        instructions = f"""你是 {name}（{agent_id}），职责：{responsibilities}

## 你有以下工具（必须用，不能编造数据）
{chr(10).join(tool_descs)}

## 红线
- **绝对不能编造数据**：所有输出必须来自真实工具调用结果
- **不能自己派活给自己**：task_dispatch 只能派给其他 agent
- 没有工具能力时老实说"我没有这个能力"，不要瞎编"""

    # 执行创建或更新
    conn = await _registry_conn()
    async with conn:
        cur = await conn.execute(
            """SELECT COALESCE(MAX(version), 0) AS current_version
               FROM platform_registry_versions
               WHERE tenant_id=%s AND kind='agent' AND item_id=%s""",
            (tenant_id, agent_id),
        )
        row = await cur.fetchone()
        current_version = (row or {}).get("current_version", 0)

        if current_version == 0:
            # 创建
            definition: dict[str, Any] = {
                "name": name,
                "description": responsibilities or name,
                "instructions": instructions,
                "tool_ids": resolved_tools,
                "skill_ids": [],
                "workflow_id": None,
                "model_template_id": template_id,
                "model_template_version": None,
            }
            await conn.execute(
                """INSERT INTO platform_registry_versions
                   (tenant_id, kind, item_id, version, definition, status, created_by, created_at)
                   VALUES (%s, 'agent', %s, 1, %s, 'published', 'agent-factory', now())""",
                (tenant_id, agent_id, json.dumps(definition, ensure_ascii=False)),
            )
            await conn.execute(
                """INSERT INTO platform_registry_active
                   (tenant_id, kind, item_id, active_version, updated_at)
                   VALUES (%s, 'agent', %s, 1, now())""",
                (tenant_id, agent_id),
            )
            new_version = 1
            action = "created"
        else:
            # 读旧 definition → 合并
            cur2 = await conn.execute(
                """SELECT v.definition FROM platform_registry_versions v
                   JOIN platform_registry_active a ON a.tenant_id=v.tenant_id
                   AND a.kind=v.kind AND a.item_id=v.item_id AND a.active_version=v.version
                   WHERE a.tenant_id=%s AND a.kind='agent' AND a.item_id=%s""",
                (tenant_id, agent_id),
            )
            old = (await cur2.fetchone() or {}).get("definition") or {}
            old_tools = old.get("tool_ids", [])

            # 合并策略：新工具追加，旧工具保留（除非 force=True 完全替换）
            if force:
                merged_tools = resolved_tools
            else:
                merged_tools = list(dict.fromkeys(list(old_tools) + resolved_tools))

            old["name"] = name
            old["description"] = responsibilities or old.get("description", name)
            old["instructions"] = instructions
            old["tool_ids"] = merged_tools
            old["model_template_id"] = template_id
            old.pop("model_template_version", None)

            new_version = current_version + 1
            await conn.execute(
                """INSERT INTO platform_registry_versions
                   (tenant_id, kind, item_id, version, definition, status, created_by, created_at)
                   VALUES (%s, 'agent', %s, %s, %s, 'published', 'agent-factory', now())""",
                (tenant_id, agent_id, new_version, json.dumps(old, ensure_ascii=False)),
            )
            await conn.execute(
                """UPDATE platform_registry_active SET active_version=%s, updated_at=now()
                   WHERE tenant_id=%s AND kind='agent' AND item_id=%s""",
                (new_version, tenant_id, agent_id),
            )
            action = "updated"

    return {
        "agent_id": agent_id,
        "action": action,
        "version": new_version,
        "tool_ids": resolved_tools,
        "instructions": instructions,
    }


REGISTRY_TOOL_HANDLERS: dict[str, ToolDefinition] = {
    "registry_list_agents": ToolDefinition(
        name="registry_list_agents",
        risk=RiskLevel.READ_ONLY,
        handler=_list_agents_handler,
        description="List all currently published agents in this tenant's registry.",
        allowed_roles=("*",),
        resource_resolver=lambda args: "agents",
        resource_authorizer=_tenant_roles_allowed,
    ),
    "registry_create_agent": ToolDefinition(
        name="registry_create_agent",
        risk=RiskLevel.INTERNAL_REVERSIBLE,
        handler=_create_agent_handler,
        description="Create and publish a new agent in the registry.",
        allowed_roles=("*",),
        resource_resolver=lambda args: f"agent:{args.get('agent_id', '')}",
        resource_authorizer=_tenant_roles_allowed,
    ),
    "registry_update_agent": ToolDefinition(
        name="registry_update_agent",
        risk=RiskLevel.INTERNAL_REVERSIBLE,
        handler=_update_agent_handler,
        description="Update an existing agent's definition (instructions, tools, name, etc.) and publish a new version.",
        allowed_roles=("*",),
        resource_resolver=lambda args: f"agent:{args.get('agent_id', '')}",
        resource_authorizer=_tenant_roles_allowed,
    ),
    "task_dispatch": ToolDefinition(
        name="task_dispatch",
        risk=RiskLevel.INTERNAL_REVERSIBLE,
        handler=_dispatch_task_handler,
        description=(
            "让另一个 agent 帮你办一件事（委托/转派）。"
            "当你判断当前任务不属于自己的职责范围、或者需要专业同事处理时，"
            "用这个工具把活派给合适的 agent，等它做完会把结果带回来。"
            "如果目标 agent 不存在会报错，先用 registry_list_agents 查名单。"
        ),
        allowed_roles=("*",),
        resource_resolver=lambda args: f"agent:{args.get('agent_id', '')}",
        resource_authorizer=_tenant_roles_allowed,
    ),
    # ── Finance Ledger Tools ──
    "ledger_create": ToolDefinition(
        name="ledger_create",
        risk=RiskLevel.INTERNAL_REVERSIBLE,
        handler=_ledger_create_handler,
        description="在本地创建一个空账本（JSON 文件），包含余额字段（收入/支出/结余）。路径是本地文件系统，不涉及外部服务。",
        allowed_roles=("*",),
        resource_resolver=lambda args: f"ledger:{args.get('ledger_id', 'main')}",
        resource_authorizer=_tenant_roles_allowed,
    ),
    "ledger_add_entry": ToolDefinition(
        name="ledger_add_entry",
        risk=RiskLevel.INTERNAL_REVERSIBLE,
        handler=_ledger_add_entry_handler,
        description="在账本中记录一笔收入或支出。自动更新累计余额。amount 必须是正数。",
        allowed_roles=("*",),
        resource_resolver=lambda args: f"ledger:{args.get('ledger_id', 'main')}",
        resource_authorizer=_tenant_roles_allowed,
    ),
    "ledger_query": ToolDefinition(
        name="ledger_query",
        risk=RiskLevel.READ_ONLY,
        handler=_ledger_query_handler,
        description="查询账本状态（余额、流水列表）。支持按 entry_type/category/日期范围过滤。",
        allowed_roles=("*",),
        resource_resolver=lambda args: f"ledger:{args.get('ledger_id', 'main')}",
        resource_authorizer=_tenant_roles_allowed,
    ),
    "ledger_report": ToolDefinition(
        name="ledger_report",
        risk=RiskLevel.READ_ONLY,
        handler=_ledger_report_handler,
        description="生成本月或全部时间段的账本汇总报表：总收入、总支出、净结余、按分类统计。",
        allowed_roles=("*",),
        resource_resolver=lambda args: f"ledger:{args.get('ledger_id', 'main')}",
        resource_authorizer=_tenant_roles_allowed,
    ),
    # ── Registry Tool Management ──
    "registry_list_tools": ToolDefinition(
        name="registry_list_tools",
        risk=RiskLevel.READ_ONLY,
        handler=_list_tools_handler,
        description="列出当前租户所有已注册的工具（tool_id、描述、风险等级、输入schema）。招人/接活时先调这个看自己有哪些手脚。",
        allowed_roles=("*",),
        resource_resolver=lambda args: "tools",
        resource_authorizer=_tenant_roles_allowed,
    ),
    # ── Agent Factory ──
    "agent_factory": ToolDefinition(
        name="agent_factory",
        risk=RiskLevel.INTERNAL_REVERSIBLE,
        handler=_agent_factory_handler,
        description=(
            "一站式 agent 创建/更新：传 agent_id、name、responsibilities（职责描述）"
            "即可自动匹配工具、生成 instructions、写注册表。"
            "已存在的 agent 会追加工具并升到新版本。"
        ),
        allowed_roles=("*",),
        resource_resolver=lambda args: f"agent:{args.get('agent_id', '')}",
        resource_authorizer=_tenant_roles_allowed,
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "agent 唯一 ID，如 ops、strategy、marketing"},
                "name": {"type": "string", "description": "显示名称，如「运营专员」"},
                "responsibilities": {"type": "string", "description": "职责描述（用于自动匹配工具）"},
                "tool_ids": {
                    "oneOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "string", "enum": ["auto"]},
                    ],
                    "description": "指定工具列表；传 'auto' 或不填则按 responsibilities 自动匹配",
                },
                "instructions": {"type": "string", "description": "自定义系统指令（不填则自动生成）"},
                "model_template_id": {"type": "string", "description": "模型模板，默认 local-template"},
                "force": {"type": "boolean", "description": "为 true 时完全替换 tool_ids，不保留旧工具"},
            },
            "required": ["agent_id", "name"],
        },
    ),
}


def tool_schemas_for_llm() -> list[dict[str, Any]]:
    """Build OpenAI-compatible function schemas from trusted handlers for LLM function calling."""
    tool_list: list[dict[str, Any]] = []
    for item_id, tool in REGISTRY_TOOL_HANDLERS.items():
        schema = {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema or _default_schema(item_id),
            },
        }
        tool_list.append(schema)
    return tool_list


def _default_schema(name: str) -> dict[str, Any]:
    """Minimal JSON Schema for a tool that didn't declare one."""
    if name == "registry_list_agents":
        return {"type": "object", "properties": {"tenant_id": {"type": "string"}}, "required": []}
    if name == "registry_create_agent":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Unique machine-readable ID (e.g. finance)"},
                "name": {"type": "string", "description": "Human-readable name"},
                "instructions": {"type": "string", "description": "System instructions for the agent"},
                "description": {"type": "string", "description": "Short one-line description of what this agent does"},
                "model_template_id": {"type": "string", "description": "Model template to use (default: local-template)"},
            },
            "required": ["agent_id", "name"],
        }
    if name == "registry_update_agent":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID to update"},
                "name": {"type": "string", "description": "New name (optional)"},
                "instructions": {"type": "string", "description": "New instructions (optional)"},
                "description": {"type": "string", "description": "New description (optional)"},
            },
            "required": ["agent_id"],
        }
    if name == "task_dispatch":
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "目标 agent 的 ID（如 hr、finance、ops、test-ops、local-assistant，以 registry_list_agents 为准）"},
                "prompt": {"type": "string", "description": "要让目标 agent 处理的具体指令/问题，要完整清晰"},
                "wait_seconds": {"type": "integer", "description": "最多等几秒让目标 agent 跑完并把结果带回来（默认 60，最大 300）"},
            },
            "required": ["agent_id", "prompt"],
        }
    return {"type": "object", "properties": {}}
