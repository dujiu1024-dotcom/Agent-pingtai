#!/usr/bin/env python3
"""memory.py — 结构化工作记忆（wm_session 六槽位）

设计依据：08《语义与记忆设计》§2、09《分项计划》T-0009。

铁律：
1. 槽位是结构化 JSON，不拼接字符串；组装只读 wm_session 表。
2. 每次写入必须带来源与时间戳，缺来源直接拒绝。
3. 目标与未决问题永不裁剪；超预算先压 digest，再减产物，最后截约束末尾。
"""

import datetime as dt
import json
from contextlib import closing

import db as dbm

# 六槽位：与 wm_session 列一一对应，唯一定义处。
SLOTS = ("goal", "constraints", "decisions", "open_questions", "artifacts", "digest")
LIST_SLOTS = ("constraints", "decisions", "open_questions", "artifacts")
TEXT_SLOTS = ("goal", "digest")

# 各槽位字符预算（中文约 1 字 ≈ 1 token，总预算 8000）
BUDGET = {
    "goal": 200,
    "constraints": 1200,
    "decisions": 1200,
    "open_questions": 800,
    "artifacts": 800,
    "digest": 1600,
}
TOTAL_BUDGET = 6000


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def connect():
    con = dbm.connect(dbm.DB_PATH)
    dbm.migrate(con, verbose=False)
    return con


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False)


def _load_scalar(raw):
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(obj, dict):
        return obj.get("text", "")
    if isinstance(obj, list):
        return "；".join(x.get("text", "") for x in obj if isinstance(x, dict))
    return str(obj)


def _load_list(raw):
    if not raw:
        return []
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [x for x in obj if isinstance(x, dict)] if isinstance(obj, list) else []


def _row(con, task_id, role):
    return con.execute(
        "SELECT * FROM wm_session WHERE task_id = ? AND role = ?", (task_id, role)
    ).fetchone()


def ensure(task_id, role, goal=""):
    """任务开跑时建 session；已存在则原样返回。"""
    with closing(connect()) as con:
        # 并发 ensure 时由唯一键兜底，不再先查后插
        con.execute(
            "INSERT INTO wm_session(task_id, role, turn, goal, constraints, decisions,"
            " open_questions, artifacts, digest, token_used, updated_at)"
            " VALUES (?, ?, 0, ?, '[]', '[]', '[]', '[]', '', 0, ?)"
            " ON CONFLICT(task_id, role) DO NOTHING",
            (task_id, role,
             _dump({"text": goal, "source": "task", "at": now()}) if goal else "",
             now()),
        )
        con.commit()
        return dict(_row(con, task_id, role))


def set_slot(task_id, role, slot, text, source, owner=""):
    """写槽位。数组槽位追加去重，文本槽位覆盖；无来源直接拒绝。"""
    if slot not in SLOTS:
        raise ValueError("未登记的槽位：" + slot)
    text = (text or "").strip()
    source = (source or "").strip()
    if not text:
        raise ValueError("槽位内容不能为空")
    if not source:
        raise ValueError("写入必须带来源（--source），无来源写入被拒")

    ensure(task_id, role)
    with closing(connect()) as con, con:
        # 数组槽位是读改写；先拿写锁，避免并发追加互相覆盖
        con.execute("BEGIN IMMEDIATE")
        row = _row(con, task_id, role)
        entry = {"text": text, "source": source, "at": now()}
        if slot in LIST_SLOTS:
            items = _load_list(row[slot])
            if any(i.get("text") == text and i.get("source") == source for i in items):
                return False
            if owner:
                entry["owner"] = owner
            items.append(entry)
            value = _dump(items)
        else:
            value = _dump(entry)
        con.execute(
            f"UPDATE wm_session SET {slot} = ?, updated_at = ?"
            " WHERE task_id = ? AND role = ?",
            (value, now(), task_id, role),
        )
    return True


def _clip(text, budget):
    return text if len(text) <= budget else text[: max(budget - 1, 0)] + "…"


def _item_lines(items, budget):
    lines, used = [], 0
    for it in items:
        s = it.get("text", "")
        if it.get("owner"):
            s += f"（{it['owner']}）"
        s += f" ［{it.get('source', '')} {it.get('at', '')[:10]}］"
        if used + len(s) > budget:
            break
        lines.append(s)
        used += len(s)
    return lines


def render(task_id, role, budget=TOTAL_BUDGET):
    """按固定顺序组装六槽位。只读表，不做字符串拼接。"""
    with closing(connect()) as con:
        row = _row(con, task_id, role)
        if not row:
            return ""
        row = dict(row)

    goal = _clip(_load_scalar(row["goal"]), BUDGET["goal"])
    constraints = _item_lines(_load_list(row["constraints"]), BUDGET["constraints"])
    questions = _item_lines(_load_list(row["open_questions"]), BUDGET["open_questions"])
    artifacts = _item_lines(_load_list(row["artifacts"])[-2:], BUDGET["artifacts"])
    digest = _clip(_load_scalar(row["digest"]), BUDGET["digest"])

    def build():
        parts = []
        if goal:
            parts.append("【目标】" + goal)
        if constraints:
            parts.append("【约束】\n" + "\n".join("- " + x for x in constraints))
        if questions:
            parts.append("【未决问题】\n" + "\n".join("- " + x for x in questions))
        if artifacts:
            parts.append("【最近产物】\n" + "\n".join("- " + x for x in artifacts))
        if digest:
            parts.append("【历史摘要】" + digest)
        return "\n".join(parts)

    text = build()
    # 超预算：按优先级裁 digest → 产物 → 约束；目标与未决问题永不裁。
    if len(text) > budget:
        cuts = []
        if digest:
            digest = _clip(digest, 60)
            cuts.append("digest")
            text = build()
        if len(text) > budget and len(artifacts) > 1:
            artifacts = artifacts[-1:]
            cuts.append("artifacts")
            text = build()
        while len(text) > budget and len(constraints) > 1:
            constraints.pop()
            cuts.append("constraints")
            text = build()
        if cuts:
            text += "\n（已按预算压缩：" + "、".join(dict.fromkeys(cuts)) + "）"
    return text


def record_run(task_id, role, output_path, summary, tokens=0):
    """一轮结束：turn+1、累计 token、登记产物引用。"""
    ensure(task_id, role)
    with closing(connect()) as con:
        row = dict(_row(con, task_id, role))
        items = _load_list(row["artifacts"])
        items.append({"text": summary[:200], "source": str(output_path), "at": now()})
        con.execute(
            "UPDATE wm_session SET turn = turn + 1, token_used = token_used + ?,"
            " artifacts = ?, updated_at = ? WHERE task_id = ? AND role = ?",
            (tokens, _dump(items), now(), task_id, role),
        )
        con.commit()


def stats(task_id, role):
    with closing(connect()) as con:
        row = _row(con, task_id, role)
    if not row:
        return None
    return {
        "turn": row["turn"],
        "token_used": row["token_used"],
        "updated_at": row["updated_at"],
        "slots": {s: bool(row[s]) for s in SLOTS},
    }


if __name__ == "__main__":
    # 冒烟：python3 memory.py <task_id> <role>
    import sys
    if len(sys.argv) != 3:
        sys.exit("用法：python3 memory.py <task_id> <role>")
    print(render(sys.argv[1], sys.argv[2]) or "（无工作记忆）")
