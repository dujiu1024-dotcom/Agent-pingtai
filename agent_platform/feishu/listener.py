"""飞书长连接监听（入站对话与指令）。

运行在独立的 `.venv-listener` 进程中，通过长连接接收 `im.message.receive_v1`，
把消息转成新平台的 HTTP API 调用，再用飞书回复。只允许配置的 owner open_id。

私聊直接发文本即进入对话；群聊必须 @机器人。未显式指定智能体时，按注册表中的
智能体描述做轻量意图匹配，命中不了再走 FEISHU_DEFAULT_AGENT。

支持指令：
  /help              指令说明
  /agents            列出可用智能体
  /status            平台健康与任务概览
  /brief             当日运营简报
  /run <内容>        提交 Agent 任务并回传结果（同样走意图匹配）
  /agent <id> <内容> 指定智能体
  /task add <内容>   同 /run
  /sync              查看待审批与最近失败任务
  /plan ...          计划拆解尚未迁移，给出提示
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from agent_platform.scheduling.feishu import FeishuNotifier, load_feishu_config

logger = logging.getLogger("agent_platform.feishu.listener")

PLATFORM_BASE_URL = os.environ.get("PLATFORM_BASE_URL", "https://127.0.0.1:8000")
PLATFORM_AGENT_ID = os.environ.get("FEISHU_AGENT_ID", "local-assistant")
DEFAULT_AGENT = os.environ.get("FEISHU_DEFAULT_AGENT", PLATFORM_AGENT_ID)
AGENT_CACHE_SECONDS = 120  # 缩短缓存到 2 分钟，agent 发布/更新后更快生效
AGENT_KEYWORDS_RAW = os.environ.get("FEISHU_AGENT_KEYWORDS") or ""
AGENT_KEYWORDS = AGENT_KEYWORDS_RAW if AGENT_KEYWORDS_RAW.strip() else (
    # 默认覆盖所有已发布 agent 的高频意图词；空字符串意味着只靠 bigram/description 匹配。
    # 单一事实源：Registry /api/platform/v1/registry/agent 里的 agent 清单，每次新增 agent 必须同步到此。
    "hr:招,招聘,招人,招个,招一名,招一位,简历,面试,入职,离职,绩效,薪酬,薪资,人力,人事,秘书,岗位,候选人,JD,offer,晋升,调岗,裁员;"
    "finance:财务,账,账本,账目,支出,收入,流水,记账,报表,预算,账单,付款,收款,发票,报销,工资,成本,盈利,亏损,算账;"
    "ops:运维,服务器,服务器运维,记账,账本,ledger,服务器维护;"
    "test-ops:运维,部署,上线,宕机,挂了,崩溃,重启,告警,报警,监控,服务,系统,日志,报错,故障,排查,恢复,扩容,带宽,挂;"
    "local-assistant:老板,助理,万能,帮我,转派,任务,查账本,做报表,看团队,万能助理"
)
TASK_POLL_SECONDS = 3
# 长任务可能执行十几分钟，回传等待上限必须跟着执行超时走。
try:
    TASK_TIMEOUT_SECONDS = int(os.environ.get("FEISHU_TASK_TIMEOUT_SECONDS", "1200"))
except (TypeError, ValueError):
    TASK_TIMEOUT_SECONDS = 1200

HELP_TEXT = (
    "FDE 平台飞书指令：\n"
    "直接发消息 → 自动识别意图并交给对应智能体\n"
    "/agents 列出可用智能体\n"
    "/agent <名称> <内容> 指定智能体\n"
    "/status 平台状态与任务概览\n"
    "/brief 今日运营简报\n"
    "/run <内容> 提交任务并回传结果\n"
    "/sync 查看待审批与最近失败任务\n"
    "/help 显示本说明\n"
    "群聊中需要 @机器人 才会响应。"
)

_SEEN: OrderedDict[str, float] = OrderedDict()
_SEEN_LOCK = threading.Lock()
_SEEN_MAX = 500
_SEEN_TTL = 300

# pending 恢复只做一次
_resume_started = False

# --- pending 任务持久化 ---
# listener 重启后，之前未汇报完成的任务需要继续轮询并回飞书。
# 格式: { "task_id": { "message_id": str, "created_at": timestamp } }
_PENDING_PATH = Path(__file__).resolve().parents[3] / "agency" / "state" / "listener.pending.json"
_PENDING_LOCK = threading.Lock()


def _load_pending() -> dict[str, dict[str, Any]]:
    try:
        if _PENDING_PATH.exists():
            return json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("读取 pending 任务文件失败")
    return {}


def _save_pending(pending: dict[str, dict[str, Any]]) -> None:
    try:
        _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PENDING_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_PENDING_PATH)
    except Exception:
        logger.exception("保存 pending 任务文件失败")


def _pending_add(task_id: str, message_id: str) -> None:
    with _PENDING_LOCK:
        pending = _load_pending()
        pending[task_id] = {"message_id": message_id, "created_at": time.time()}
        _save_pending(pending)


def _pending_remove(task_id: str) -> None:
    with _PENDING_LOCK:
        pending = _load_pending()
        pending.pop(task_id, None)
        _save_pending(pending)


def _first_time(message_id: str) -> bool:
    """同一条消息只处理一次，避免长连接重投导致重复执行。"""
    if not message_id:
        return True
    now = time.time()
    with _SEEN_LOCK:
        expired = [key for key, seen in _SEEN.items() if now - seen > _SEEN_TTL]
        for key in expired:
            _SEEN.pop(key, None)
        if message_id in _SEEN:
            return False
        _SEEN[message_id] = now
        while len(_SEEN) > _SEEN_MAX:
            _SEEN.popitem(last=False)
    return True


def _clean_text(content: str) -> str:
    try:
        payload = json.loads(content or "{}")
    except json.JSONDecodeError:
        return ""
    text = str(payload.get("text") or "")
    # 群聊里会带 @_user_1 之类的占位符。
    return re.sub(r"@_user_\d+", "", text).strip()


class PlatformClient:
    """访问本机平台 API 的最小客户端；回环地址免登录。"""

    def __init__(self, base_url: str = PLATFORM_BASE_URL) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, verify=False, timeout=20.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._http.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        response = await self._http.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    async def health(self) -> dict[str, Any]:
        return await self._get("/health/ready")

    async def submit_task(
        self, prompt: str, agent_id: str = PLATFORM_AGENT_ID,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "agent_id": agent_id or PLATFORM_AGENT_ID,
            "input": {"prompt": prompt},
        }
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        return await self._post("/api/platform/v1/workbench/tasks", body)

    async def task(self, task_id: str) -> dict[str, Any]:
        return await self._get(f"/api/platform/v1/workbench/tasks/{task_id}")

    async def tasks(self) -> list[dict[str, Any]]:
        payload = await self._get("/api/platform/v1/workbench/tasks", {"limit": 200})
        return list(payload.get("items") or [])

    async def approvals(self) -> list[dict[str, Any]]:
        payload = await self._get("/api/platform/v1/approvals")
        return list(payload.get("items") or [])

    async def scheduler_jobs(self) -> list[dict[str, Any]]:
        payload = await self._get("/api/platform/v1/scheduler/jobs")
        return list(payload.get("items") or [])

    async def agents(self) -> list[dict[str, Any]]:
        payload = await self._get("/api/platform/v1/registry/agent")
        return list(payload.get("items") or [])


def _bigrams(text: str) -> set[str]:
    cleaned = re.sub(r"[\s\W_]+", "", text.lower())
    if not cleaned:
        return set()
    if len(cleaned) == 1:
        return {cleaned}
    return {cleaned[index:index + 2] for index in range(len(cleaned) - 1)}


def _keyword_map(raw: str) -> dict[str, tuple[str, ...]]:
    """解析 FEISHU_AGENT_KEYWORDS，格式：hr:招聘,简历;assistant:闲聊"""
    mapping: dict[str, list[str]] = {}
    for block in raw.split(";"):
        agent_id, _, words = block.partition(":")
        agent_id = agent_id.strip()
        if not agent_id:
            continue
        for word in re.split(r"[,，\s]+", words):
            if word.strip():
                mapping.setdefault(agent_id, []).append(word.strip())
    return {key: tuple(value) for key, value in mapping.items()}


class AgentRouter:
    """按注册表里的智能体描述做轻量意图匹配。

    推理顺序：显式指定 > 关键词 / 描述相似度 > 默认智能体。
    只读取注册表的公开定义，不额外调用模型，避免拖慢单条消息的响应。
    """

    def __init__(self, platform: PlatformClient, default_agent: str = DEFAULT_AGENT) -> None:
        self._platform = platform
        self._default = default_agent or PLATFORM_AGENT_ID
        self._keywords = _keyword_map(AGENT_KEYWORDS)
        self._cached_at = 0.0
        self._agents: list[dict[str, Any]] = []

    async def available(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        now = time.time()
        if refresh or not self._agents or now - self._cached_at > AGENT_CACHE_SECONDS:
            try:
                items = await self._platform.agents()
            except Exception:
                logger.exception("读取智能体列表失败，沿用缓存")
                return self._agents
            agents: list[dict[str, Any]] = []
            for item in items:
                definition = item.get("definition") or {}
                if not isinstance(definition, dict):
                    definition = {}
                agent_id = str(item.get("item_id") or "")
                if not agent_id:
                    continue
                agents.append({
                    "agent_id": agent_id,
                    "name": str(definition.get("name") or agent_id),
                    "description": str(definition.get("description") or ""),
                })
            self._agents = agents
            self._cached_at = now
        return self._agents

    def _explicit(self, text: str) -> tuple[str, str] | None:
        match = re.match(r"^/agent\s+(\S+)\s*(.*)$", text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1), match.group(2).strip()
        match = re.match(r"^#(\S+)\s+(.*)$", text, re.DOTALL)
        if match:
            return match.group(1), match.group(2).strip()
        return None

    @staticmethod
    def _score(text: str, agent: dict[str, Any], keywords: tuple[str, ...]) -> float:
        score = 0.0
        for word in keywords:
            if word and word in text:
                score += 3.0
        message_grams = _bigrams(text)
        if message_grams:
            description_grams = _bigrams(f"{agent['name']} {agent['description']}")
            if description_grams:
                score += len(message_grams & description_grams) / len(message_grams) * 2
        return score

    async def resolve(self, text: str) -> tuple[str, str, str]:
        """返回 (agent_id, 展示名, 原因)。"""
        agents = await self.available()
        explicit = self._explicit(text)
        if explicit:
            agent_id, rest = explicit
            if rest:
                match = next(
                    (item for item in agents
                     if agent_id.lower() in {item["agent_id"].lower(), item["name"].lower()}),
                    None,
                )
                return agent_id, match["name"] if match else agent_id, "指定"
        best: tuple[float, str, str] | None = None
        for agent in agents:
            keywords = self._keywords.get(agent["agent_id"], ())
            score = self._score(text, agent, keywords)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, agent["agent_id"], agent["name"])
        if best is None:
            match = next((a for a in agents if a["agent_id"] == self._default), None)
            return self._default, match["name"] if match else self._default, "默认"
        return best[1], best[2], "意图"


def _mask_id(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "***"
    return f"{value[:6]}***{value[-4:]}"


def _mention_fields(message: Any) -> tuple[set[str], set[str]]:
    open_ids: set[str] = set()
    names: set[str] = set()
    for mention in getattr(message, "mentions", None) or []:
        identifier = getattr(mention, "id", None)
        open_id = getattr(identifier, "open_id", "") if identifier else ""
        if open_id:
            open_ids.add(str(open_id))
        name = getattr(mention, "name", "")
        if name:
            names.add(str(name))
    return open_ids, names


def _task_title(task: dict[str, Any]) -> str:
    """任务展示名：取输入里的任务描述，替代裸 task_id。"""
    raw = task.get("input")
    if isinstance(raw, dict):
        for key in ("prompt", "task", "title", "query", "message", "content", "instruction"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                cleaned = value.strip().replace("\n", " ")
                return cleaned[:40] + ("…" if len(cleaned) > 40 else "")
    if isinstance(raw, str) and raw.strip():
        cleaned = raw.strip().replace("\n", " ")
        return cleaned[:40] + ("…" if len(cleaned) > 40 else "")
    return "未命名任务"


def _result_text(task: dict[str, Any]) -> str:
    output = task.get("output")
    if isinstance(output, dict):
        text = output.get("output") or output.get("content") or output.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    if isinstance(output, str) and output.strip():
        return output.strip()
    return json.dumps(output, ensure_ascii=False)[:1500] if output else "（无输出）"


class FeishuCommandService:
    def __init__(
        self, notifier: FeishuNotifier, platform: PlatformClient,
        router: AgentRouter | None = None,
    ) -> None:
        self._notifier = notifier
        self._platform = platform
        self._router = router or AgentRouter(platform)

    async def _reply(self, message_id: str, text: str) -> None:
        result = await self._notifier.reply_text(message_id, text)
        if result.get("status") != "sent":
            logger.warning("飞书回复失败：%s", result.get("message"))

    async def handle(self, text: str, message_id: str) -> None:
        command, argument = _parse(text)
        try:
            if command == "help":
                await self._reply(message_id, HELP_TEXT)
            elif command == "agents":
                await self._reply(message_id, await self._agents_text())
            elif command == "status":
                await self._reply(message_id, await self._status_text())
            elif command == "brief":
                await self._reply(message_id, await self._brief_text())
            elif command in {"run", "task"}:
                await self._run(argument or "", message_id)
            elif command == "agent":
                await self._run(f"/agent {argument or ''}", message_id)
            elif command == "sync":
                await self._reply(message_id, await self._sync_text())
            elif command == "plan":
                await self._reply(
                    message_id,
                    "计划拆解尚未迁移到新平台；可以直接发消息交给智能体处理。",
                )
            elif command == "":
                # 普通文本（私聊，或群聊里已 @机器人）直接进入对话。
                await self._run(text, message_id)
            else:
                await self._reply(message_id, "未识别指令。\n\n" + HELP_TEXT)
        except Exception as exc:  # 单条指令失败不影响长连接
            logger.exception("处理飞书消息失败：%s", text[:80])
            await self._reply(message_id, f"处理失败：{exc}")

    async def _agents_text(self) -> str:
        agents = await self._router.available(refresh=True)
        if not agents:
            return "注册表里还没有可用的智能体。"
        lines = [f"- {item['name']}：{item['description'][:60]}" for item in agents]
        return "可用智能体：\n" + "\n".join(lines)

    async def _run(self, prompt: str, message_id: str) -> None:
        prompt = prompt.strip()
        if not prompt:
            await self._reply(message_id, "用法：/run <要执行的内容>，或直接发消息。")
            return
        agent_id, agent_name, reason = await self._router.resolve(prompt)
        logger.info("飞书消息路由 agent=%s reason=%s", agent_id, reason)
        # 飞书 message_id 天然唯一，直接当幂等键——后端收到相同 message_id 会返回已有任务。
        task = await self._platform.submit_task(prompt, agent_id, idempotency_key=message_id)
        task_id = str(task.get("task_id") or "")
        await self._reply(
            message_id,
            f"已交给 {agent_name}（{reason}），处理完成后会把结果发回这里。",
        )
        _pending_add(task_id, message_id)
        asyncio.create_task(self._await_result(task_id, message_id))

    async def _await_result(self, task_id: str, message_id: str) -> None:
        deadline = time.time() + TASK_TIMEOUT_SECONDS
        try:
            while time.time() < deadline:
                await asyncio.sleep(TASK_POLL_SECONDS)
                try:
                    task = await self._platform.task(task_id)
                except Exception:
                    continue
                status = task.get("status")
                if status == "succeeded":
                    await self._reply(
                        message_id, f"处理完成，结果如下：\n{_result_text(task)}",
                    )
                    return
                if status in {"failed", "cancelled"}:
                    error = task.get("last_error") or "未记录原因"
                    status_zh = "执行失败" if status == "failed" else "已取消"
                    await self._reply(message_id, f"处理未完成（{status_zh}）：{error}")
                    return
            await self._reply(message_id, "任务还在执行中，完成后会在这里回复你。")
        finally:
            _pending_remove(task_id)

    async def _status_text(self) -> str:
        health = await self._platform.health()
        tasks = await self._platform.tasks()
        active = sum(1 for item in tasks if item.get("status") in {"queued", "running"})
        failed = sum(1 for item in tasks if item.get("status") == "failed")
        approvals = await self._platform.approvals()
        pending = sum(1 for item in approvals if item.get("status") == "pending")
        jobs = await self._platform.scheduler_jobs()
        enabled = sum(1 for item in jobs if item.get("enabled"))
        health_zh = {"ok": "正常", "healthy": "正常", "degraded": "降级", "error": "异常"}.get(
            str(health.get("status") or "").lower(), "未知"
        )
        return (
            f"平台状态：{health_zh}\n"
            f"任务：在办 {active}，失败 {failed}\n"
            f"待审批：{pending}\n"
            f"定时作业：{enabled}/{len(jobs)} 启用"
        )

    async def _brief_text(self) -> str:
        tasks = await self._platform.tasks()
        today = datetime.now(timezone.utc).astimezone().date()
        todays = [
            item for item in tasks
            if str(item.get("created_at") or "")[:10] == today.isoformat()
        ]
        succeeded = sum(1 for item in todays if item.get("status") == "succeeded")
        failed = sum(1 for item in todays if item.get("status") == "failed")
        approvals = await self._platform.approvals()
        pending = sum(1 for item in approvals if item.get("status") == "pending")
        return (
            f"{today.isoformat()} 运营简报\n"
            f"今日任务：{len(todays)}（成功 {succeeded}，失败 {failed}）\n"
            f"待审批：{pending}"
        )

    async def _sync_text(self) -> str:
        tasks = await self._platform.tasks()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        failures: list[str] = []
        for item in tasks:
            if item.get("status") != "failed":
                continue
            finished = item.get("finished_at")
            if not finished:
                continue
            try:
                moment = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
            except ValueError:
                continue
            if moment >= cutoff:
                failures.append(f"- {_task_title(item)}：{item.get('last_error') or '未记录'}")
        approvals = await self._platform.approvals()
        pending = [item for item in approvals if item.get("status") == "pending"]
        lines = [f"待审批 {len(pending)} 条", f"近 24 小时失败任务 {len(failures)} 条"]
        lines.extend(failures[:5])
        return "\n".join(lines)


def _parse(text: str) -> tuple[str, str]:
    text = text.strip()
    if not text:
        return "", ""
    lowered = text.lower()
    for prefix in ("/task add", "task add"):
        if lowered == prefix:
            return "task", ""
        if lowered.startswith(prefix + " "):
            return "task", text[len(prefix):].strip()
    if not text.startswith("/"):
        return "", text
    body = text[1:].strip()
    if not body:
        return "help", ""
    command, _, argument = body.partition(" ")
    return command.lower(), argument.strip()


def _mentions_bot(message: Any, bot_open_id: str, bot_name: str) -> bool:
    open_ids, names = _mention_fields(message)
    if bot_open_id and bot_open_id in open_ids:
        return True
    if bot_name and any(bot_name in name or name in bot_name for name in names):
        return True
    # 拿不到自身身份时退化为“有 @ 就算”，避免群聊完全不可用。
    return not bot_open_id and bool(open_ids)


def build_listener() -> Any:
    config = load_feishu_config()
    if not config.configured:
        raise RuntimeError("飞书未配置：缺少 FEISHU_APP_ID / FEISHU_APP_SECRET / 收件人")
    notifier = FeishuNotifier(config)
    platform = PlatformClient()
    router = AgentRouter(platform)
    service = FeishuCommandService(notifier, platform, router)
    try:
        bot = asyncio.run(notifier.bot_info())
    except Exception:
        logger.exception("读取机器人信息失败，群聊 @ 判定退化为有 @ 即响应")
        bot = {}
    bot_open_id = str(bot.get("open_id") or "")
    bot_name = str(bot.get("app_name") or "")
    logger.info("机器人身份 open_id=%s name=%s", _mask_id(bot_open_id), bot_name)

    def on_message(data: P2ImMessageReceiveV1) -> None:
        global _resume_started
        # 首次收到消息时，恢复 pending 任务的回传轮询（loop 此时已存活）
        if not _resume_started:
            _resume_started = True
            try:
                pending = _load_pending()
                if pending:
                    logger.info("恢复 %d 条 pending 任务的回传", len(pending))
                    for tsk_id, info in list(pending.items()):
                        msg_id = str(info.get("message_id") or "")
                        if not msg_id:
                            _pending_remove(tsk_id)
                            continue
                        if time.time() - float(info.get("created_at") or 0) > 600:
                            _pending_remove(tsk_id)
                            continue
                        asyncio.create_task(service._await_result(tsk_id, msg_id))
            except Exception:
                logger.exception("恢复 pending 任务失败")

        event = data.event
        if event is None or event.message is None:
            return
        message = event.message
        if not _first_time(str(message.message_id or "")):
            return
        sender = event.sender.sender_id.open_id if event.sender and event.sender.sender_id else ""
        if config.user_open_id and sender != config.user_open_id:
            logger.warning("忽略非白名单用户消息：%s", _mask_id(sender))
            return
        if str(getattr(message, "chat_type", "") or "") == "group":
            if not _mentions_bot(message, bot_open_id, bot_name):
                return
        if message.message_type != "text":
            asyncio.create_task(service._reply(str(message.message_id), "暂只支持文本消息，发送 /help 查看用法。"))
            return
        text = _clean_text(message.content)
        if not text:
            asyncio.create_task(service._reply(str(message.message_id), HELP_TEXT))
            return
        asyncio.create_task(service.handle(text, str(message.message_id)))

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    return lark.ws.Client(
        config.app_id, config.app_secret, event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="飞书入站长连接监听")
    parser.add_argument("--check", action="store_true", help="只检查配置与平台连通性后退出")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.check:
        config = load_feishu_config()

        async def check() -> None:
            client = PlatformClient()
            try:
                health = await client.health()
                print(json.dumps({
                    "feishu_configured": config.configured,
                    "owner_configured": bool(config.user_open_id),
                    "platform": health,
                }, ensure_ascii=False))
            finally:
                await client.close()

        asyncio.run(check())
        return
    logger.info("启动飞书长连接监听，平台 %s", PLATFORM_BASE_URL)
    pending = _load_pending()
    if pending:
        logger.info("发现 %d 条待回传的 pending 任务，启动后自动恢复轮询", len(pending))
    client = build_listener()
    client.start()


if __name__ == "__main__":
    main()
