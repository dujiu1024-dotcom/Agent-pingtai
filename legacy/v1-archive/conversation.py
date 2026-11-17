#!/usr/bin/env python3
"""conversation.py — 按 open_id 的多轮上下文与待确认意图（纯标准库）

- 最近 10 条消息（用户 + 机器人）落 SQLite conversations 表；
- 闲置超过 30 分钟自动开新会话（清空该人的历史）；
- 只存对话文本，不写客户隐私/凭据；敏感字段由调用方过滤后传入；
- pending_intent 存 config_kv，用于"追问一句后接续执行"。
"""

import json
import time
from contextlib import closing

import db as dbm

IDLE_SECONDS = 30 * 60
MAX_MESSAGES = 10
PENDING_PREFIX = "conv.pending."


def _con():
    con = dbm.connect()
    con.execute(
        "CREATE TABLE IF NOT EXISTS conversations ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, open_id TEXT NOT NULL,"
        " role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)")
    return con


def _idle(con, open_id):
    row = con.execute(
        "SELECT created_at FROM conversations WHERE open_id = ?"
        " ORDER BY id DESC LIMIT 1", (open_id,)).fetchone()
    if not row:
        return False
    try:
        from datetime import datetime
        last = datetime.fromisoformat(row["created_at"])
        return (time.time() - last.timestamp()) > IDLE_SECONDS
    except (ValueError, TypeError):
        return False


def append(open_id, role, text):
    """记一轮消息；闲置超时先清空，保证不带昨天的上下文。"""
    text = " ".join((text or "").split())[:500]
    with closing(_con()) as con, con:
        if _idle(con, open_id):
            con.execute("DELETE FROM conversations WHERE open_id = ?", (open_id,))
        con.execute(
            "INSERT INTO conversations(open_id, role, text, created_at)"
            " VALUES (?, ?, ?, ?)", (open_id, role, text, dbm.now()))
        con.execute(
            "DELETE FROM conversations WHERE open_id = ? AND id NOT IN ("
            " SELECT id FROM conversations WHERE open_id = ?"
            " ORDER BY id DESC LIMIT ?)", (open_id, open_id, MAX_MESSAGES))


def history(open_id, limit=MAX_MESSAGES):
    with closing(_con()) as con:
        if _idle(con, open_id):
            return []
        rows = con.execute(
            "SELECT role, text FROM conversations WHERE open_id = ?"
            " ORDER BY id DESC LIMIT ?", (open_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def context_text(open_id, limit=MAX_MESSAGES):
    lines = []
    for item in history(open_id, limit):
        who = "用户" if item["role"] == "user" else "助手"
        lines.append(f"{who}：{item['text']}")
    return "\n".join(lines)


def set_pending(open_id, payload):
    data = dict(payload or {})
    data["at"] = time.time()
    with closing(dbm.connect()) as con, con:
        con.execute("INSERT OR REPLACE INTO config_kv(key, value) VALUES (?, ?)",
                    (PENDING_PREFIX + open_id, json.dumps(data, ensure_ascii=False)))


def get_pending(open_id):
    with closing(dbm.connect()) as con:
        row = con.execute("SELECT value FROM config_kv WHERE key = ?",
                          (PENDING_PREFIX + open_id,)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["value"])
    except (ValueError, TypeError):
        return None
    # 待确认意图与会话同寿命：闲置超过 30 分钟就不再拿来接新消息
    try:
        if time.time() - float(data.get("at") or 0) > IDLE_SECONDS:
            clear_pending(open_id)
            return None
    except (ValueError, TypeError):
        clear_pending(open_id)
        return None
    data.pop("at", None)
    return data


def clear_pending(open_id):
    with closing(dbm.connect()) as con, con:
        con.execute("DELETE FROM config_kv WHERE key = ?", (PENDING_PREFIX + open_id,))
