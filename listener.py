#!/usr/bin/env python3
"""listener.py — 飞书长连接监听（P1）

作用：让机器人长耳朵。收到你的消息就回一条，并把原文追加到
agency/state/inbound.jsonl，供后续接进事件引擎。

依赖：lark-oapi（只装在 .venv-listener 里，主程序仍零依赖）。
启动：.venv-listener/bin/python listener.py

前提：开发者后台 → 事件与回调 → 订阅方式选「长连接」，
      并订阅事件「接收消息 im.message.receive_v1」。
"""

import datetime as dt
import json
import os
import sys
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "agency" / "state" / "inbound.jsonl"

sys.path.insert(0, str(ROOT))
import feishu_bot  # noqa: E402


def load_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env()
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")


def text_of(message):
    try:
        return json.loads(message.content or "{}").get("text", "")
    except json.JSONDecodeError:
        return ""


def record(kind, payload):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "type": kind, **payload}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def on_message(data: P2ImMessageReceiveV1):
    event = data.event
    msg = event.message
    sender = event.sender
    text = text_of(msg)

    # 机器人自己发的消息不回，避免自激
    if sender and sender.sender_type == "app":
        return

    open_id = (sender.sender_id.open_id if sender and sender.sender_id else "") or ""
    record("message", {
        "open_id": open_id,
        "chat_id": msg.chat_id,
        "msg_id": msg.message_id,
        "text": text,
    })
    print(f"[收到] {open_id} @ {msg.chat_id}：{text}", flush=True)

    reply = f"收到：{text}" if text else "收到（非文本消息，暂不处理）"
    try:
        feishu_bot.Bot().send_text(reply, receive_id=msg.chat_id, id_type="chat_id")
    except Exception as e:  # noqa: BLE001
        print("[回信失败]", e, flush=True)


def main():
    if not APP_ID or not APP_SECRET:
        sys.exit("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，检查 .env")

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    cli = lark.ws.Client(APP_ID, APP_SECRET, event_handler=handler,
                         log_level=lark.LogLevel.INFO)
    print("长连接启动，等待消息…… Ctrl+C 退出", flush=True)
    cli.start()


if __name__ == "__main__":
    main()
