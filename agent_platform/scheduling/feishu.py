"""飞书通知适配器（新平台版）。

只做两件事：用应用凭据换 tenant_access_token，向指定会话/用户发送文本消息。
凭据优先读进程环境变量，其次按 `.env.prod` → `.env.dev` → `.env` 的顺序读取。
日志与接口返回都不携带 app_secret / access_token。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import httpx

DEFAULT_BASE = "https://open.feishu.cn/open-apis"
# 凭据失效类错误码：清空缓存换一次 token 后重试。
TOKEN_INVALID = {99991661, 99991663, 99991668}
ENV_FILES = (".env.prod", ".env.dev", ".env")


def _repo_root() -> Path:
    # agent_platform/scheduling/feishu.py → 仓库根目录
    return Path(__file__).resolve().parents[2]


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value:
            values[key] = value
    return values


def _resolve(key: str, *, files: Mapping[str, dict[str, str]]) -> tuple[str, str]:
    """返回 (值, 来源)。环境变量优先，其次是按优先级排序的 env 文件。"""
    direct = os.environ.get(key, "").strip()
    if direct:
        return direct, "environment"
    for name in ENV_FILES:
        value = files.get(name, {}).get(key, "").strip()
        if value:
            return value, name
    return "", ""


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str = ""
    app_secret: str = field(default="", repr=False)
    user_open_id: str = ""
    chat_id: str = ""
    base_url: str = DEFAULT_BASE
    source: str = ""

    @property
    def configured(self) -> bool:
        # 没有收件人时即便有凭据也无法推送。
        return bool(self.app_id and self.app_secret and (self.chat_id or self.user_open_id))

    @property
    def receiver_type(self) -> str:
        return "chat_id" if self.chat_id else "open_id"

    @property
    def receiver_id(self) -> str:
        return self.chat_id or self.user_open_id

    def public_view(self) -> dict[str, Any]:
        """可安全返回给前端的字段，绝不包含密钥。"""
        return {
            "configured": self.configured,
            "app_id": self.app_id,
            "receiver_type": self.receiver_type if self.configured else "",
            "receiver_masked": _mask(self.receiver_id),
            "source": self.source,
        }


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "***"
    return f"{value[:6]}***{value[-4:]}"


def load_feishu_config(repo_root: Path | None = None) -> FeishuConfig:
    root = repo_root or _repo_root()
    files = {name: _read_env_file(root / name) for name in ENV_FILES}
    values: dict[str, str] = {}
    sources: list[str] = []
    for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_USER_OPEN_ID", "FEISHU_CHAT_ID"):
        value, source = _resolve(key, files=files)
        values[key] = value
        if value:
            sources.append(source)
    base_url, _ = _resolve("FEISHU_BASE_URL", files=files)
    # 来源只用于说明凭据从哪读到的，取第一个命中的来源即可。
    return FeishuConfig(
        app_id=values["FEISHU_APP_ID"],
        app_secret=values["FEISHU_APP_SECRET"],
        user_open_id=values["FEISHU_USER_OPEN_ID"],
        chat_id=values["FEISHU_CHAT_ID"],
        base_url=(base_url or DEFAULT_BASE).rstrip("/"),
        source=sources[0] if sources else "",
    )


class FeishuNotifier:
    """轻量飞书客户端；未配置时所有发送都返回 skipped。"""

    def __init__(self, config: FeishuConfig | None = None) -> None:
        self.config = config or load_feishu_config()
        self._token = ""
        self._token_expires_at = 0.0

    @property
    def configured(self) -> bool:
        return self.config.configured

    def public_view(self) -> dict[str, Any]:
        return self.config.public_view()

    async def _tenant_token(self, client: httpx.AsyncClient, *, force: bool = False) -> str:
        if not force and self._token and time.time() < self._token_expires_at:
            return self._token
        response = await client.post(
            f"{self.config.base_url}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
        )
        data = response.json()
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise RuntimeError(
                f"飞书鉴权失败：code={data.get('code')} msg={data.get('msg') or response.status_code}"
            )
        self._token = str(data["tenant_access_token"])
        # 提前 60 秒过期，避免边界时刻拿到失效 token。
        self._token_expires_at = time.time() + max(int(data.get("expire") or 7200) - 60, 60)
        return self._token

    async def bot_info(self) -> dict[str, Any]:
        """读取机器人自身信息（open_id / 名称），用于群聊 @ 判定。"""
        if not self.config.app_id:
            return {}
        async with httpx.AsyncClient(timeout=10.0) as client:
            token = await self._tenant_token(client)
            response = await client.get(
                f"{self.config.base_url}/bot/v3/info",
                headers={"Authorization": f"Bearer {token}"},
            )
            data = response.json()
            if data.get("code") != 0:
                raise RuntimeError(
                    f"读取机器人信息失败：code={data.get('code')} msg={data.get('msg')}"
                )
            bot = data.get("bot") or {}
            return {
                "open_id": str(bot.get("open_id") or ""),
                "app_name": str(bot.get("app_name") or ""),
            }

    async def send_text(self, text: str) -> dict[str, Any]:
        """发送纯文本消息；返回值 {status, message}，status ∈ sent/skipped/failed。"""
        if not self.configured:
            return {"status": "skipped", "message": "飞书未配置，已跳过通知"}
        return await self.send_to(self.config.receiver_type, self.config.receiver_id, text)

    async def send_to(self, receiver_type: str, receiver_id: str, text: str) -> dict[str, Any]:
        """向指定 open_id / chat_id 发送文本，供入站监听回复使用。"""
        if not self.configured:
            return {"status": "skipped", "message": "飞书未配置，已跳过通知"}
        if receiver_type not in {"open_id", "chat_id"} or not receiver_id:
            return {"status": "failed", "message": "飞书收件人无效"}
        payload = {
            "receive_id": receiver_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                token = await self._tenant_token(client)
                response = await client.post(
                    f"{self.config.base_url}/im/v1/messages",
                    params={"receive_id_type": receiver_type},
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                )
                data = response.json()
                if data.get("code") in TOKEN_INVALID:
                    token = await self._tenant_token(client, force=True)
                    response = await client.post(
                        f"{self.config.base_url}/im/v1/messages",
                        params={"receive_id_type": receiver_type},
                        headers={"Authorization": f"Bearer {token}"},
                        json=payload,
                    )
                    data = response.json()
                if data.get("code") != 0:
                    return {
                        "status": "failed",
                        "message": f"飞书发送失败：code={data.get('code')} msg={data.get('msg')}",
                    }
                return {"status": "sent", "message": "飞书通知已发送"}
        except Exception as exc:  # 网络/解析异常都不应阻断调度循环
            return {"status": "failed", "message": f"飞书发送异常：{type(exc).__name__}: {exc}"}

    async def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        """回复指定消息，保持会话线程。"""
        if not self.configured:
            return {"status": "skipped", "message": "飞书未配置，已跳过通知"}
        payload = {
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                token = await self._tenant_token(client)
                response = await client.post(
                    f"{self.config.base_url}/im/v1/messages/{message_id}/reply",
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                )
                data = response.json()
                if data.get("code") != 0:
                    return {
                        "status": "failed",
                        "message": f"飞书回复失败：code={data.get('code')} msg={data.get('msg')}",
                    }
                return {"status": "sent", "message": "飞书回复已发送"}
        except Exception as exc:
            return {"status": "failed", "message": f"飞书回复异常：{type(exc).__name__}: {exc}"}
