"""飞书长连接监听（入站指令）。

运行在独立的 `.venv-listener` 进程中，通过长连接接收 `im.message.receive_v1`，
把指令转成新平台的 HTTP API 调用，再用飞书回复。只允许配置的 owner open_id。

支持指令：
  /help              指令说明
  /status            平台健康与任务概览
  /brief             当日运营简报
  /run <内容>        提交 Agent 任务并回传结果
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
from typing import Any

import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from agent_platform.scheduling.feishu import FeishuNotifier, load_feishu_config

logger = logging.getLogger("agent_platform.feishu.listener")

PLATFORM_BASE_URL = os.environ.get("PLATFORM_BASE_URL", "https://127.0.0.1:8000")
PLATFORM_AGENT_ID = os.environ.get("FEISHU_AGENT_ID", "local-assistant")
TASK_POLL_SECONDS = 3
TASK_TIMEOUT_SECONDS = 180

HELP_TEXT = (
    "FDE 平台飞书指令：\n"
    "/status 平台状态与任务概览\n"
    "/brief 今日运营简报\n"
    "/run <内容> 提交 Agent 任务并回传结果\n"
    "/task add <内容> 同 /run\n"
    "/sync 查看待审批与最近失败任务\n"
    "/help 显示本说明"
)

_SEEN: OrderedDict[str, float] = OrderedDict()
_SEEN_LOCK = threading.Lock()
_SEEN_MAX = 500
_SEEN_TTL = 300


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

    async def submit_task(self, prompt: str) -> dict[str, Any]:
        return await self._post(
            "/api/platform/v1/workbench/tasks",
            {"agent_id": PLATFORM_AGENT_ID, "input": {"prompt": prompt}},
        )

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
    def __init__(self, notifier: FeishuNotifier, platform: PlatformClient) -> None:
        self._notifier = notifier
        self._platform = platform

    async def _reply(self, message_id: str, text: str) -> None:
        result = await self._notifier.reply_text(message_id, text)
        if result.get("status") != "sent":
            logger.warning("飞书回复失败：%s", result.get("message"))

    async def handle(self, text: str, message_id: str) -> None:
        command, argument = _parse(text)
        try:
            if command in {"help", ""}:
                await self._reply(message_id, HELP_TEXT)
            elif command == "status":
                await self._reply(message_id, await self._status_text())
            elif command == "brief":
                await self._reply(message_id, await self._brief_text())
            elif command in {"run", "task"}:
                await self._run(argument or "", message_id)
            elif command == "sync":
                await self._reply(message_id, await self._sync_text())
            elif command == "plan":
                await self._reply(
                    message_id,
                    "计划拆解尚未迁移到新平台；可以先用 /run <内容> 直接执行单步任务。",
                )
            else:
                await self._reply(message_id, "未识别指令。\n\n" + HELP_TEXT)
        except Exception as exc:  # 单条指令失败不影响长连接
            logger.exception("处理飞书指令失败：%s", text)
            await self._reply(message_id, f"处理失败：{type(exc).__name__}: {exc}")

    async def _run(self, prompt: str, message_id: str) -> None:
        if not prompt.strip():
            await self._reply(message_id, "用法：/run <要执行的内容>")
            return
        task = await self._platform.submit_task(prompt.strip())
        task_id = str(task.get("task_id") or "")
        await self._reply(message_id, f"已提交任务 {task_id}，执行完成后会把结果发给你。")
        threading.Thread(
            target=lambda: asyncio.run(self._await_result(task_id, message_id)),
            daemon=True,
        ).start()

    async def _await_result(self, task_id: str, message_id: str) -> None:
        deadline = time.time() + TASK_TIMEOUT_SECONDS
        while time.time() < deadline:
            await asyncio.sleep(TASK_POLL_SECONDS)
            try:
                task = await self._platform.task(task_id)
            except Exception:
                continue
            status = task.get("status")
            if status == "succeeded":
                await self._reply(
                    message_id, f"任务 {task_id} 已完成：\n{_result_text(task)}",
                )
                return
            if status in {"failed", "cancelled"}:
                error = task.get("last_error") or "未记录原因"
                await self._reply(message_id, f"任务 {task_id} {status}：{error}")
                return
        await self._reply(message_id, f"任务 {task_id} 仍在执行，稍后可在工作台查看。")

    async def _status_text(self) -> str:
        health = await self._platform.health()
        tasks = await self._platform.tasks()
        active = sum(1 for item in tasks if item.get("status") in {"queued", "running"})
        failed = sum(1 for item in tasks if item.get("status") == "failed")
        approvals = await self._platform.approvals()
        pending = sum(1 for item in approvals if item.get("status") == "pending")
        jobs = await self._platform.scheduler_jobs()
        enabled = sum(1 for item in jobs if item.get("enabled"))
        return (
            f"平台状态：{health.get('status')}\n"
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
                failures.append(f"- {item.get('task_id')}：{item.get('last_error') or '未记录'}")
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


def build_listener() -> Any:
    config = load_feishu_config()
    if not config.configured:
        raise RuntimeError("飞书未配置：缺少 FEISHU_APP_ID / FEISHU_APP_SECRET / 收件人")
    notifier = FeishuNotifier(config)
    platform = PlatformClient()
    service = FeishuCommandService(notifier, platform)

    def on_message(data: P2ImMessageReceiveV1) -> None:
        event = data.event
        if event is None or event.message is None:
            return
        message = event.message
        if not _first_time(str(message.message_id or "")):
            return
        sender = event.sender.sender_id.open_id if event.sender and event.sender.sender_id else ""
        if config.user_open_id and sender != config.user_open_id:
            logger.warning("忽略非白名单用户消息：%s", sender)
            return
        if message.message_type != "text":
            asyncio.run(service._reply(str(message.message_id), "暂只支持文本指令，发送 /help 查看用法。"))
            return
        text = _clean_text(message.content)
        threading.Thread(
            target=lambda: asyncio.run(service.handle(text, str(message.message_id))),
            daemon=True,
        ).start()

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
    build_listener().start()


if __name__ == "__main__":
    main()
