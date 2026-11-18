"""平台定时作业调度服务。

旧系统 agency 的 tick / due_scan / dead_letter_retry / event_sync / daily_brief
等定时任务在此统一承接：作业定义与执行历史落在 PostgreSQL，进程重启后
调度状态仍然存在；通知渠道当前只有飞书，未配置时自动跳过。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

logger = logging.getLogger("agent_platform.scheduling")


class ScheduledJobNotFound(LookupError):
    """作业未定义。"""


class ScheduledJobBusy(RuntimeError):
    """作业正在执行，拒绝重复触发。"""


class EventSubscriptionNotFound(LookupError):
    """事件订阅不存在。"""


# 订阅事件类型必须与 100 号迁移中的 CHECK 约束保持一致。
SUBSCRIPTION_EVENT_TYPES = ("task.failed", "run.failed", "approval.pending", "daily")


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        return value
    return Jsonb(value)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


@dataclass(frozen=True)
class JobDefinition:
    job_id: str
    name: str
    description: str
    interval_seconds: int
    enabled: bool = True


# 与旧系统作业清单一一对应；content_due 因内容日历尚未迁移默认停用。
DEFAULT_JOBS: tuple[JobDefinition, ...] = (
    JobDefinition("tick", "运行态巡检", "统计任务、运行、审批、会话与死信数量", 300),
    JobDefinition("due_scan", "逾期任务扫描", "发现超时未推进或租约过期的任务并告警", 900),
    JobDefinition("dead_letter_retry", "死信巡检", "汇总死信队列并提醒人工处理", 1800),
    JobDefinition("event_sync", "事件同步", "推送新增待审批与失败任务", 600),
    JobDefinition("daily_brief", "每日简报", "每天 09:00 推送运营摘要", 86400),
    JobDefinition(
        "content_due", "内容日历到期提醒",
        "内容日历尚未迁移到新平台，默认停用", 3600, enabled=False,
    ),
    JobDefinition("selfcheck", "平台自检", "检查数据库连通性", 1800),
)
JOB_ORDER = {definition.job_id: index for index, definition in enumerate(DEFAULT_JOBS)}


def _job_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "name": row["name"],
        "description": row["description"],
        "interval_seconds": row["interval_seconds"],
        "enabled": bool(row["enabled"]),
        "status": row["status"],
        "last_run_at": _iso(row.get("last_run_at")),
        "last_result": row.get("last_result"),
        "last_message": row.get("last_message"),
        "last_duration_ms": row.get("last_duration_ms"),
        "next_run_at": _iso(row.get("next_run_at")),
        "run_count": int(row.get("run_count") or 0),
        "failure_count": int(row.get("failure_count") or 0),
    }


def _run_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(row["run_id"]),
        "job_id": row["job_id"],
        "trigger": row["trigger"],
        "status": row["status"],
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "duration_ms": row.get("duration_ms"),
        "message": row.get("message"),
        "detail": row.get("detail") or {},
    }


def _subscription_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "subscription_id": str(row["subscription_id"]),
        "event_type": row["event_type"],
        "channel": row["channel"],
        "target": row["target"],
        "enabled": bool(row["enabled"]),
        "created_by": row.get("created_by") or "",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


class PostgresSchedulerService:
    """基于 PostgreSQL 的单集群调度器；同一作业同一时刻只允许一个执行者。"""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        notifier: Any | None = None,
        poll_seconds: int = 30,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self._connect = connection_factory
        self._notifier = notifier
        self._poll_seconds = max(int(poll_seconds), 5)
        self._tz = ZoneInfo(timezone_name)
        self._task: asyncio.Task[None] | None = None

    @asynccontextmanager
    async def _connection(self):
        connection = await self._connect()
        async with connection:
            yield connection

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        try:
            await self._recover_stale()
            await self.ensure_all_tenants()
        except Exception:
            # 初始化失败不能让 API 起不来，循环里会继续重试。
            logger.exception("定时作业初始化失败")
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="platform-scheduler")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("定时调度循环异常")
            await asyncio.sleep(self._poll_seconds)

    async def _recover_stale(self) -> None:
        # 进程被强杀时 running 状态会残留，超过 10 分钟视为中断并复位。
        async with self._connection() as connection:
            await connection.execute(
                "UPDATE platform_scheduled_jobs SET status='idle', updated_at=now() "
                "WHERE status='running' AND updated_at < now() - interval '10 minutes'"
            )

    # ---------- 作业定义 ----------
    def _initial_next_run(self, definition: JobDefinition) -> datetime:
        if definition.job_id == "daily_brief":
            return self._next_daily_run()
        return datetime.now(timezone.utc) + timedelta(seconds=definition.interval_seconds)

    def _next_daily_run(self) -> datetime:
        # 每日简报固定在当地 09:00 触发。
        now = datetime.now(self._tz)
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.astimezone(timezone.utc)

    async def ensure_jobs(self, tenant_id: str) -> None:
        async with self._connection() as connection:
            for definition in DEFAULT_JOBS:
                await connection.execute(
                    "INSERT INTO platform_scheduled_jobs "
                    "(tenant_id, job_id, name, description, interval_seconds, enabled, next_run_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id, job_id) DO NOTHING",
                    (
                        tenant_id, definition.job_id, definition.name, definition.description,
                        definition.interval_seconds, definition.enabled,
                        self._initial_next_run(definition),
                    ),
                )

    async def ensure_all_tenants(self) -> None:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT tenant_id FROM platform_tenants WHERE status='active'"
            )
            rows = await cursor.fetchall()
        for row in rows:
            await self.ensure_jobs(row["tenant_id"])

    # ---------- 查询 ----------
    async def list_jobs(self, tenant_id: str) -> list[dict[str, Any]]:
        await self.ensure_jobs(tenant_id)
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM platform_scheduled_jobs WHERE tenant_id=%s", (tenant_id,)
            )
            rows = await cursor.fetchall()
        return sorted((_job_view(row) for row in rows), key=lambda item: JOB_ORDER.get(item["job_id"], 99))

    async def list_runs(self, tenant_id: str, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM platform_scheduled_job_runs WHERE tenant_id=%s "
                "ORDER BY started_at DESC LIMIT %s",
                (tenant_id, limit),
            )
            rows = await cursor.fetchall()
        return [_run_view(row) for row in rows]

    def notifier_status(self) -> dict[str, Any]:
        if self._notifier is None:
            return {"configured": False, "reason": "通知适配器未初始化"}
        return self._notifier.public_view()

    # ---------- 操作 ----------
    async def set_enabled(self, tenant_id: str, job_id: str, enabled: bool) -> dict[str, Any]:
        await self.ensure_jobs(tenant_id)
        async with self._connection() as connection:
            cursor = await connection.execute(
                "UPDATE platform_scheduled_jobs SET enabled=%s, updated_at=now() "
                "WHERE tenant_id=%s AND job_id=%s RETURNING *",
                (bool(enabled), tenant_id, job_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ScheduledJobNotFound(job_id)
        return _job_view(row)

    # ---------- 事件订阅 ----------
    async def list_subscriptions(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT subscription_id, event_type, channel, target, enabled, "
                "created_by, created_at, updated_at FROM platform_event_subscriptions "
                "WHERE tenant_id=%s ORDER BY created_at",
                (tenant_id,),
            )
            return [_subscription_view(row) for row in await cursor.fetchall()]

    async def create_subscription(
        self, tenant_id: str, *, event_type: str, target: str,
        created_by: str, enabled: bool = True,
    ) -> dict[str, Any]:
        if event_type not in SUBSCRIPTION_EVENT_TYPES:
            raise ValueError(f"不支持的事件类型：{event_type}")
        receiver_type, receiver_id = self._parse_target(target)
        if not receiver_type:
            raise ValueError("收件人格式无效，应为 open_id:ou_xxx 或 chat_id:oc_xxx")
        normalized = f"{receiver_type}:{receiver_id}"
        async with self._connection() as connection:
            cursor = await connection.execute(
                "INSERT INTO platform_event_subscriptions "
                "(tenant_id, event_type, channel, target, enabled, created_by) "
                "VALUES (%s,%s,'feishu',%s,%s,%s) "
                "ON CONFLICT (tenant_id, event_type, channel, target) DO UPDATE SET "
                "enabled=EXCLUDED.enabled, updated_at=now() "
                "RETURNING subscription_id, event_type, channel, target, enabled, "
                "created_by, created_at, updated_at",
                (tenant_id, event_type, normalized, bool(enabled), created_by),
            )
            return _subscription_view(await cursor.fetchone())

    async def update_subscription(
        self, tenant_id: str, subscription_id: str, *,
        enabled: bool | None = None, target: str | None = None,
    ) -> dict[str, Any]:
        subscription_uuid = self._subscription_uuid(subscription_id)
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT subscription_id, event_type, target FROM platform_event_subscriptions "
                "WHERE tenant_id=%s AND subscription_id=%s",
                (tenant_id, subscription_uuid),
            )
            current = await cursor.fetchone()
            if current is None:
                raise EventSubscriptionNotFound(subscription_id)
            normalized = None
            if target is not None:
                receiver_type, receiver_id = self._parse_target(target)
                if not receiver_type:
                    raise ValueError("收件人格式无效，应为 open_id:ou_xxx 或 chat_id:oc_xxx")
                normalized = f"{receiver_type}:{receiver_id}"
                cursor = await connection.execute(
                    "SELECT 1 FROM platform_event_subscriptions "
                    "WHERE tenant_id=%s AND event_type=%s AND channel='feishu' "
                    "AND target=%s AND subscription_id<>%s",
                    (tenant_id, current["event_type"], normalized, subscription_uuid),
                )
                if await cursor.fetchone() is not None:
                    raise ValueError("同一事件类型下已存在相同的收件人订阅")
            cursor = await connection.execute(
                "UPDATE platform_event_subscriptions SET enabled=COALESCE(%s,enabled), "
                "target=COALESCE(%s,target), updated_at=now() "
                "WHERE tenant_id=%s AND subscription_id=%s "
                "RETURNING subscription_id, event_type, channel, target, enabled, "
                "created_by, created_at, updated_at",
                (enabled, normalized, tenant_id, subscription_uuid),
            )
            return _subscription_view(await cursor.fetchone())

    async def delete_subscription(self, tenant_id: str, subscription_id: str) -> None:
        subscription_uuid = self._subscription_uuid(subscription_id)
        async with self._connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM platform_event_subscriptions "
                "WHERE tenant_id=%s AND subscription_id=%s RETURNING subscription_id",
                (tenant_id, subscription_uuid),
            )
            if await cursor.fetchone() is None:
                raise EventSubscriptionNotFound(subscription_id)

    @staticmethod
    def _subscription_uuid(subscription_id: str) -> UUID:
        try:
            return UUID(str(subscription_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("subscription_id 必须是合法 UUID") from exc

    async def run_job(
        self, tenant_id: str, job_id: str, *, trigger: str = "manual",
        require_due: bool = False,
    ) -> dict[str, Any] | None:
        await self.ensure_jobs(tenant_id)
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM platform_scheduled_jobs "
                "WHERE tenant_id=%s AND job_id=%s FOR UPDATE",
                (tenant_id, job_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ScheduledJobNotFound(job_id)
            if row["status"] == "running":
                raise ScheduledJobBusy(job_id)
            if require_due:
                due = row["next_run_at"]
                if not row["enabled"] or due is None or due > datetime.now(timezone.utc):
                    return None
            await connection.execute(
                "UPDATE platform_scheduled_jobs SET status='running', updated_at=now() "
                "WHERE tenant_id=%s AND job_id=%s",
                (tenant_id, job_id),
            )
            definition = {
                "job_id": row["job_id"],
                "interval_seconds": int(row["interval_seconds"]),
            }

        started = datetime.now(timezone.utc)
        try:
            handler = getattr(self, f"_job_{job_id}", None)
            if handler is None:
                status, message, detail = "skipped", f"未知作业类型：{job_id}", {}
            else:
                status, message, detail = await handler(tenant_id)
        except Exception as exc:
            logger.exception("定时作业执行失败：%s", job_id)
            status = "failed"
            message = f"{type(exc).__name__}: {exc}"
            detail = {"error": message}
        finished = datetime.now(timezone.utc)
        duration_ms = int((finished - started).total_seconds() * 1000)
        next_run = (
            self._next_daily_run()
            if job_id == "daily_brief"
            else finished + timedelta(seconds=definition["interval_seconds"])
        )

        async with self._connection() as connection:
            await connection.execute(
                "UPDATE platform_scheduled_jobs SET status='idle', last_run_at=%s, "
                "last_result=%s, last_message=%s, last_duration_ms=%s, next_run_at=%s, "
                "run_count=run_count+1, failure_count=failure_count+%s, updated_at=now() "
                "WHERE tenant_id=%s AND job_id=%s",
                (
                    started, status, message, duration_ms, next_run,
                    1 if status == "failed" else 0, tenant_id, job_id,
                ),
            )
            cursor = await connection.execute(
                "INSERT INTO platform_scheduled_job_runs "
                "(tenant_id, job_id, trigger, status, started_at, finished_at, "
                " duration_ms, message, detail) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    tenant_id, job_id, trigger, status, started, finished,
                    duration_ms, message, _jsonb(detail),
                ),
            )
            run_row = await cursor.fetchone()
        return _run_view(run_row)

    async def run_due(self) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT tenant_id, job_id FROM platform_scheduled_jobs "
                "WHERE enabled AND status <> 'running' AND next_run_at <= now() "
                "ORDER BY next_run_at LIMIT 20"
            )
            due = await cursor.fetchall()
        results: list[dict[str, Any]] = []
        for row in due:
            try:
                result = await self.run_job(
                    row["tenant_id"], row["job_id"], trigger="schedule", require_due=True,
                )
            except ScheduledJobBusy:
                continue
            except Exception:
                logger.exception("触发到期作业失败：%s", row["job_id"])
                continue
            if result is not None:
                results.append(result)
        return results

    # ---------- 通知 ----------
    async def _notify(self, tenant_id: str, dedupe_key: str, text: str) -> dict[str, Any]:
        """按默认收件人（deploy/.env 的 FEISHU_CHAT_ID / FEISHU_USER_OPEN_ID）推送。"""
        return await self._notify_to(tenant_id, "", "", dedupe_key, text)

    async def _notify_to(
        self, tenant_id: str, receiver_type: str, receiver_id: str,
        dedupe_key: str, text: str,
    ) -> dict[str, Any]:
        """按指定收件人推送；receiver_type 为空时回落到默认收件人。"""
        if self._notifier is None or not getattr(self._notifier, "configured", False):
            return {"status": "skipped", "message": "飞书未配置，已跳过通知"}
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT status FROM platform_notification_log "
                "WHERE tenant_id=%s AND channel='feishu' AND dedupe_key=%s",
                (tenant_id, dedupe_key),
            )
            if await cursor.fetchone() is not None:
                return {"status": "skipped", "message": "重复通知已忽略"}
        if receiver_type:
            result = await self._notifier.send_to(receiver_type, receiver_id, text)
        else:
            result = await self._notifier.send_text(text)
        if result.get("status") == "failed":
            # 失败不落库，下一轮继续重试。
            return result
        async with self._connection() as connection:
            await connection.execute(
                "INSERT INTO platform_notification_log "
                "(tenant_id, channel, dedupe_key, status, message) VALUES (%s,'feishu',%s,%s,%s) "
                "ON CONFLICT (tenant_id, channel, dedupe_key) DO NOTHING",
                (tenant_id, dedupe_key, result.get("status", "sent"), result.get("message")),
            )
        return result

    @staticmethod
    def _parse_target(target: str) -> tuple[str, str]:
        """解析订阅收件人：支持 `open_id:ou_xxx` / `chat_id:oc_xxx`，也兼容纯 ID。"""
        value = (target or "").strip()
        if not value:
            return "", ""
        if ":" in value:
            kind, _, raw = value.partition(":")
            raw = raw.strip()
            return (kind, raw) if kind in {"open_id", "chat_id"} and raw else ("", "")
        if value.startswith("ou_"):
            return "open_id", value
        if value.startswith("oc_"):
            return "chat_id", value
        return "", ""

    @staticmethod
    def _merge_targets(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """合并多组订阅并按收件人去重，避免同一事件重复推送。"""
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in groups:
            for subscription in group:
                receiver_type, receiver_id = PostgresSchedulerService._parse_target(
                    subscription.get("target") or ""
                )
                if not receiver_type:
                    continue
                identity = f"{receiver_type}:{receiver_id}"
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append({"target": identity})
        return merged

    async def _event_targets(self, tenant_id: str, event_type: str) -> list[dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT subscription_id, target FROM platform_event_subscriptions "
                "WHERE tenant_id=%s AND enabled AND event_type=%s ORDER BY created_at",
                (tenant_id, event_type),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def _subscription_count(self, tenant_id: str) -> int:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT count(*) AS total FROM platform_event_subscriptions "
                "WHERE tenant_id=%s AND enabled",
                (tenant_id,),
            )
            row = await cursor.fetchone() or {}
            return int(row.get("total") or 0)

    async def _deliver(
        self, tenant_id: str, dedupe_base: str, text: str,
        subscriptions: list[dict[str, Any]], *, fallback: bool = True,
    ) -> int:
        """把一条事件发给全部订阅收件人。

        fallback=True 表示租户完全没有订阅，此时保留默认收件人推送，
        避免从旧系统迁移过来后通知直接静默。
        """
        sent = 0
        if not subscriptions:
            if not fallback:
                return 0
            result = await self._notify(tenant_id, dedupe_base, text)
            return 1 if result.get("status") == "sent" else 0
        for subscription in subscriptions:
            receiver_type, receiver_id = self._parse_target(subscription.get("target") or "")
            if not receiver_type:
                logger.warning(
                    "订阅 %s 的收件人格式无效，已跳过：%s",
                    subscription.get("subscription_id"), subscription.get("target"),
                )
                continue
            result = await self._notify_to(
                tenant_id, receiver_type, receiver_id,
                f"{dedupe_base}:{receiver_type}:{receiver_id}", text,
            )
            sent += 1 if result.get("status") == "sent" else 0
        return sent

    # ---------- 各作业实现 ----------
    async def _job_tick(self, tenant_id: str) -> tuple[str, str, dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT "
                " (SELECT count(*) FROM platform_runs WHERE tenant_id=%s "
                "    AND status IN ('queued','running')) AS active_runs,"
                " (SELECT count(*) FROM platform_approvals WHERE tenant_id=%s "
                "    AND status='pending') AS pending_approvals,"
                " (SELECT count(*) FROM platform_conversation_sessions WHERE tenant_id=%s)"
                "    AS session_count,"
                " (SELECT count(*) FROM platform_run_dead_letters WHERE tenant_id=%s"
                "    AND resolved_at IS NULL) AS dead_letters",
                (tenant_id,) * 4,
            )
            row = await cursor.fetchone() or {}
        detail = {key: int(row.get(key) or 0) for key in (
            "active_runs", "pending_approvals", "session_count", "dead_letters",
        )}
        message = (
            f"在办运行 {detail['active_runs']} / "
            f"待审批 {detail['pending_approvals']} / 会话 {detail['session_count']} / "
            f"死信 {detail['dead_letters']}"
        )
        return "succeeded", message, detail

    async def _job_due_scan(self, tenant_id: str) -> tuple[str, str, dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT run_id AS task_id, agent_id, status, updated_at, "
                "lease_expires_at, last_error "
                "FROM platform_runs WHERE tenant_id=%s AND status='running' "
                "AND (lease_expires_at IS NULL OR lease_expires_at < now() "
                "     OR updated_at < now() - interval '15 minutes') "
                "ORDER BY updated_at ASC LIMIT 50",
                (tenant_id,),
            )
            rows = await cursor.fetchall()
        if not rows:
            return "succeeded", "未发现逾期任务", {"count": 0}
        lines = ["【FDE 平台】发现逾期任务："]
        for row in rows:
            lines.append(
                f"- 任务 {row['task_id']}（Agent {row['agent_id']}）："
                f"最后更新 {_iso(row['updated_at'])}，错误 {row.get('last_error') or '无'}"
            )
        await self._notify(tenant_id, f"due_task:{rows[0]['task_id']}:{_iso(rows[0]['updated_at'])}",
                           "\n".join(lines))
        return "succeeded", f"发现 {len(rows)} 个逾期任务", {
            "count": len(rows),
            "tasks": [{"task_id": str(row["task_id"]), "agent_id": row["agent_id"],
                       "updated_at": _iso(row["updated_at"])} for row in rows],
        }

    async def _job_dead_letter_retry(self, tenant_id: str) -> tuple[str, str, dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT run_id, attempt_count, max_attempts, reason, created_at "
                "FROM platform_run_dead_letters WHERE tenant_id=%s AND resolved_at IS NULL "
                "ORDER BY created_at ASC LIMIT 50",
                (tenant_id,),
            )
            rows = await cursor.fetchall()
        if not rows:
            return "succeeded", "死信队列为空", {"count": 0}
        lines = ["【FDE 平台】死信队列待处理："]
        for row in rows:
            lines.append(
                f"- 运行 {row['run_id']}：{row.get('reason') or '未知原因'}"
                f"（{row['attempt_count']}/{row['max_attempts']} 次）"
            )
        for row in rows:
            await self._notify(
                tenant_id, f"dead_letter:{row['run_id']}",
                f"【FDE 平台】运行 {row['run_id']} 已进入死信队列：{row.get('reason') or '未知原因'}",
            )
        return "succeeded", f"死信 {len(rows)} 条", {
            "count": len(rows),
            "runs": [str(row["run_id"]) for row in rows],
        }

    async def _job_event_sync(self, tenant_id: str) -> tuple[str, str, dict[str, Any]]:
        """按订阅推送事件；没有订阅时保持默认收件人推送，避免功能退化为静默。"""
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT approval_id, run_id, action, resource, risk_level, created_at "
                "FROM platform_approvals WHERE tenant_id=%s AND status='pending' "
                "AND created_at < now() - interval '10 minutes' "
                "ORDER BY created_at ASC LIMIT 20",
                (tenant_id,),
            )
            approvals = await cursor.fetchall()
            cursor = await connection.execute(
                "SELECT run_id, agent_id, last_error, finished_at FROM platform_runs "
                "WHERE tenant_id=%s AND status='failed' "
                "AND finished_at > now() - interval '24 hours' "
                "ORDER BY finished_at DESC LIMIT 20",
                (tenant_id,),
            )
            failed_runs = await cursor.fetchall()

        approval_targets = await self._event_targets(tenant_id, "approval.pending")
        task_targets = await self._event_targets(tenant_id, "task.failed")
        run_targets = await self._event_targets(tenant_id, "run.failed")
        # 任务与运行在新平台统一落在 platform_runs，两类订阅合并后去重，避免重复推送。
        failure_targets = self._merge_targets(task_targets, run_targets)
        # 只有租户一条订阅都没有时才回落到默认收件人。
        fallback = await self._subscription_count(tenant_id) == 0

        sent = 0
        for row in approvals:
            sent += await self._deliver(
                tenant_id, f"approval:{row['approval_id']}",
                f"【FDE 平台】待审批：{row['action']} → {row['resource']}"
                f"（风险等级 {row['risk_level']}）",
                approval_targets, fallback=fallback,
            )
        for row in failed_runs:
            sent += await self._deliver(
                tenant_id, f"failed_run:{row['run_id']}",
                f"【FDE 平台】运行失败：{row['run_id']}"
                f"（Agent {row['agent_id']}），原因 {row.get('last_error') or '未记录'}",
                failure_targets, fallback=fallback,
            )
        return "succeeded", (
            f"待审批 {len(approvals)} 条 / 失败运行 {len(failed_runs)} 条，"
            f"实际推送 {sent} 条"
        ), {
            "approvals": len(approvals), "failed_runs": len(failed_runs), "sent": sent,
            "subscriptions": {
                "approval.pending": len(approval_targets),
                "task.failed": len(task_targets),
                "run.failed": len(run_targets),
            },
        }

    async def _job_daily_brief(self, tenant_id: str) -> tuple[str, str, dict[str, Any]]:
        async with self._connection() as connection:
            cursor = await connection.execute(
                "SELECT count(*) AS total,"
                " count(*) FILTER (WHERE status='succeeded') AS succeeded,"
                " count(*) FILTER (WHERE status='failed') AS failed "
                "FROM platform_runs WHERE tenant_id=%s AND created_at > now() - interval '24 hours'",
                (tenant_id,),
            )
            tasks = await cursor.fetchone() or {}
            cursor = await connection.execute(
                "SELECT count(*) AS pending FROM platform_approvals "
                "WHERE tenant_id=%s AND status='pending'",
                (tenant_id,),
            )
            approvals = await cursor.fetchone() or {}
            cursor = await connection.execute(
                "SELECT count(*) AS dead_letters FROM platform_run_dead_letters "
                "WHERE tenant_id=%s AND resolved_at IS NULL",
                (tenant_id,),
            )
            dead = await cursor.fetchone() or {}
        detail = {
            "runs_total": int(tasks.get("total") or 0),
            "runs_succeeded": int(tasks.get("succeeded") or 0),
            "runs_failed": int(tasks.get("failed") or 0),
            "pending_approvals": int(approvals.get("pending") or 0),
            "dead_letters": int(dead.get("dead_letters") or 0),
        }
        date_key = datetime.now(self._tz).strftime("%Y-%m-%d")
        text = (
            f"【FDE 平台】{date_key} 运营简报\n"
            f"运行：共 {detail['runs_total']}，成功 {detail['runs_succeeded']}，"
            f"失败 {detail['runs_failed']}\n"
            f"待审批 {detail['pending_approvals']} / 死信 {detail['dead_letters']}"
        )
        subscriptions = await self._event_targets(tenant_id, "daily")
        fallback = await self._subscription_count(tenant_id) == 0
        sent = await self._deliver(
            tenant_id, f"daily:{date_key}", text, subscriptions, fallback=fallback,
        )
        detail["sent"] = sent
        message = (
            f"简报已推送 {sent} 个收件人" if sent
            else "简报已生成，本次无需推送（飞书未配置或重复）"
        )
        return "succeeded", message, detail

    async def _job_content_due(self, tenant_id: str) -> tuple[str, str, dict[str, Any]]:
        return "skipped", "内容日历尚未迁移到新平台，本作业暂不执行", {}

    async def _job_selfcheck(self, tenant_id: str) -> tuple[str, str, dict[str, Any]]:
        async with self._connection() as connection:
            await connection.execute("SELECT 1")
        return "succeeded", "数据库连通正常", {"database": "ok"}
