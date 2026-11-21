#!/usr/bin/env python3
"""console.py — 本地操作控制台（只绑 127.0.0.1，纯标准库）

台账仍是唯一事实源：agency/state/agency.db。本服务只读写本地库，
不对外网开放，也不替代 agency.py；关任务照样要求证据。

用法：
  python3 console.py                 # http://127.0.0.1:8787
  python3 console.py --port 9000
"""

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import plistlib
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import agency as ag
import db as dbm
import intent

ROOT = Path(__file__).resolve().parent
BOARD_PATH = ROOT / "dashboard.html"
MAX_BODY = 64 * 1024
MAX_OUTPUT = 200_000
RUN_TIMEOUT = 300
TASK_ID = re.compile(r"^/api/tasks/(T-\d{4})/(close|update)$")
AGENT_RUN = re.compile(r"^/api/agents/([a-z0-9-]+)/run$")
# 访问令牌：环境变量可固定，否则每次启动随机生成（只打印在本机终端）。
CONSOLE_TOKEN = os.environ.get("AGENCY_CONSOLE_TOKEN") or secrets.token_urlsafe(24)
LEGACY_CONSOLE_AUTH_ENABLED = os.environ.get("AGENCY_LEGACY_CONSOLE_AUTH_ENABLED", "true").strip().lower() not in {"0", "false", "off"}
TOKEN_HEADER = "X-Console-Token"
ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1")
# 看板独立进程：只提供 /board 与 /api/board，端口与环境绑定。
BOARD_ONLY = False
RUN_LOCK = threading.Lock()
RUNNING = set()
JOBS = {}
JOB_LOCK = threading.Lock()
JOB_SEQ = [0]

# ===== 意图识别：每个角色负责的意图域（唯一真源） =====
# 关键词只做 evidence 采集，决策由规则驱动（经验 1276236）
ROLE_MISSIONS = {
    "auditor": {
        "label": "审计",
        "intent": ["audit", "risk", "reconcile", "verify"],
        "verbs": ["审", "核", "查", "核对", "复核", "验证", "风险", "审计", "合规", "凭证", "对账", "证据", "数", "查账"],
        "nouns": ["数", "账", "证据", "合规", "风险", "凭证"],
        "weight": 1.0,
    },
    "chief-of-staff": {
        "label": "幕僚长",
        "intent": ["coordinate", "summary", "plan", "prioritize"],
        "verbs": ["协调", "统筹", "汇总", "整理", "优先", "对齐", "排优先级", "按顺序", "意见", "综合", "汇总"],
        "nouns": ["协调", "汇总", "统筹", "意见"],
        "weight": 1.0,
    },
    "coach": {
        "label": "教练",
        "intent": ["coach", "mentor", "motivate"],
        "verbs": ["辅导", "教练", "鼓励", "激励", "心态", "沟通", "聊聊", "辅导", "指导", "建议"],
        "nouns": ["辅导", "教练", "心态", "激励"],
        "weight": 1.0,
    },
    "content": {
        "label": "内容",
        "intent": ["write", "copy", "article", "post"],
        "verbs": ["写", "稿", "文案", "内容", "草稿", "文章", "推文", "标题", "改", "润色", "发布", "撰写", "产出", "版本"],
        "nouns": ["稿", "文案", "文章", "内容", "标题", "发布"],
        "weight": 1.0,
    },
    "finance": {
        "label": "财务",
        "intent": ["finance", "invoice", "budget", "cost"],
        "verbs": ["账", "发票", "报税", "预算", "成本", "回款", "发票", "合同", "财务", "报价", "收款", "付款", "结算"],
        "nouns": ["账", "发票", "预算", "成本", "回款", "合同"],
        "weight": 1.0,
    },
    "planner": {
        "label": "规划师",
        "intent": ["plan", "milestone", "schedule", "path"],
        "verbs": ["计划", "里程碑", "排期", "排", "WBS", "关键路径", "依赖", "进度", "甘特", "安排", "时间线",
                  "复盘", "验收"],
        "nouns": ["计划", "里程碑", "排期", "进度", "关键路径"],
        "weight": 1.0,
    },
    "pm": {
        "label": "产品经理",
        "intent": ["requirement", "prd", "user_story", "feature"],
        "verbs": ["需求", "PRD", "用户故事", "原型", "功能", "产品", "对齐", "拆解", "故事点", "交互",
                  "诊断", "报价单", "报价", "方案", "验收", "交付物", "范围"],
        "nouns": ["需求", "PRD", "原型", "功能", "用户故事", "产品"],
        "weight": 1.0,
    },
    "producer": {
        "label": "制作人",
        "intent": ["launch", "release", "resource", "risk"],
        "verbs": ["上线", "发布", "推出", "资源", "排期卡死", "bug", "紧急", "赶", "交付", "验收", "质量", "排人"],
        "nouns": ["上线", "发布", "资源", "交付", "质量"],
        "weight": 1.1,
    },
    "scout": {
        "label": "商机侦察",
        "intent": ["lead", "sales", "customer", "market"],
        "verbs": ["客户", "商机", "线索", "销售", "市场", "竞争对手", "扫街", "拓展", "bd", "预算", "谈判", "签", "跑"],
        "nouns": ["客户", "商机", "线索", "市场", "竞争对手", "预算"],
        "weight": 1.0,
    },
    "secretary": {
        "label": "秘书",
        "intent": ["meeting", "email", "diary", "followup"],
        "verbs": ["纪要", "会议", "邮件", "回复", "日程", "安排", "提醒", "跟进", "快递", "签收", "预约", "时间"],
        "nouns": ["纪要", "会议", "日程", "邮件"],
        "weight": 1.0,
    },
}
# 意图域分组（有些事天然需要多 agent 协作）
INTENT_BUNDLES = [
    {
        "key": "launch",
        "label": "上线/发布",
        # 只认"要发出去"的动作：原先"验收/交付"也触发，导致写文档被判成上线。
        "evidence": ["上线", "发布", "发版", "灰度", "demo"],
        "roles": ["producer", "content", "auditor"],
    },
    {
        "key": "full_audit",
        "label": "全流程审计",
        "evidence": ["全流程", "系统审计", "全面检查"],
        "roles": ["auditor", "chief-of-staff", "planner"],
    },
    {
        "key": "go_to_market",
        "label": "商业化推进",
        # 只认真正的商机词：原先只要出现"客户"就触发，导致"和客户开会"被判成销售动作。
        "evidence": ["商机", "销售", "拓展", "获客", "线索"],
        "roles": ["scout", "pm", "producer"],
    },
    {
        "key": "finance_cycle",
        "label": "财务周期",
        "evidence": ["对账", "报税", "月结对账", "合同归档"],
        "roles": ["finance", "auditor"],
    },
]
SYNC_LOCK = threading.Lock()
SYNCING = False
SYNC_TIMEOUT = 180
# 配额类错误进重试队列（指数退避）；模型不存在/权限不足属永久错误，直接失败。
RETRY_MAX = int(os.environ.get("AGENCY_RETRY_MAX", "2"))
# 退避起点取实测恢复时间（65s 后同规模请求恢复），不再拍脑袋 5s。
RETRY_BASE = float(os.environ.get("AGENCY_RETRY_BASE", "60"))
# 并发上限取实测安全值（flash-lite 并发 4 全通，8 有 3/8 被拒）
MAX_PARALLEL = int(os.environ.get("AGENCY_MAX_PARALLEL", "4"))


def connect():
    con = dbm.connect(dbm.DB_PATH)
    dbm.migrate(con, verbose=False)
    return con


def parse_day(value, label):
    try:
        return ag.parse_date(str(value))
    except SystemExit:
        raise ValueError(f"{label}格式错误，应为 YYYY-MM-DD") from None


def agent_roster():
    items = []
    for path in sorted((ROOT / "agents").glob("*/ROLE.md")):
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip() if text else path.parent.name
        match = re.search(r"##\s*1\.\s*身份\s*\n+(.+)", text)
        desc = ""
        if match:
            para = re.sub(r"\*\*|`", "", match.group(1).strip())
            desc = para[:88] + ("…" if len(para) > 88 else "")
        items.append({
            "name": path.parent.name,
            "title": title,
            "desc": desc,
            "command": f"python3 agency.py run {path.parent.name}",
        })
    return items


def milestone_groups(tasks):
    groups = {}
    for task in tasks:
        key = task.get("milestone") or ""
        if key in ("", "—", "-"):
            continue
        groups.setdefault(key, []).append(task)
    result = []
    for key, rows in groups.items():
        starts = [r["start"] for r in rows if r["start"]]
        dues = [r["due"] for r in rows if r["due"]]
        result.append({
            "key": key,
            "start": min(starts) if starts else "",
            "due": max(dues) if dues else "",
            "hours": round(sum(r["hours"] or 0 for r in rows), 1),
            "closed": all(r["db_status"] in ("done", "canceled") for r in rows),
            "tasks": [{"id": r["id"], "title": r["title"],
                       "state": r.get("db_status") or r.get("status") or ""}
                      for r in rows],
        })
    return sorted(result, key=lambda g: int(re.sub(r"\D", "", g["key"]) or 0))


def subscriptions_view():
    """订阅矩阵 + 当日 token 消耗（T-0019）。"""
    spend = ag.sync_spend_today()
    items = []
    for row in ag.sub_rows():
        label = "%s|%s|%s" % (row["event_type"], row["target"], row["action"])
        items.append({
            "event_type": row["event_type"],
            "target": row["target"],
            "action": row["action"],
            "enabled": bool(row["enabled"]),
            "budget": row["budget"],
            "spend_today": spend.get(label, 0),
        })
    return items


def finance_view():
    """财务视图：报价/合同/发票/回款/成本汇总（只读）。"""
    with closing(connect()) as con:
        def one(sql, params=()):
            row = con.execute(sql, params).fetchone()
            return row[0] if row else 0

        quote_n = one("SELECT COUNT(*) FROM quotes")
        quote_amt = one("SELECT COALESCE(SUM(amount), 0) FROM quotes")
        quote_cost = one("SELECT COALESCE(SUM(cost), 0) FROM quotes")
        contract_n = one("SELECT COUNT(*) FROM contracts")
        contract_amt = one("SELECT COALESCE(SUM(amount), 0) FROM contracts")
        invoiced = one("SELECT COALESCE(SUM(amount), 0) FROM invoices"
                       " WHERE status = 'issued'")
        paid = one("SELECT COALESCE(SUM(amount), 0) FROM payments")
        overdue_n = one("SELECT COUNT(*) FROM invoices WHERE status = 'issued'"
                        " AND due_at IS NOT NULL AND due_at < ?",
                        (ag.today().isoformat(),))
        overdue_amt = one("SELECT COALESCE(SUM(amount), 0) FROM invoices"
                          " WHERE status = 'issued' AND due_at IS NOT NULL"
                          " AND due_at < ?", (ag.today().isoformat(),))
        cost_amt = one("SELECT COALESCE(SUM(amount), 0) FROM costs")
        invoices = [dict(r) for r in con.execute(
            "SELECT no, amount, issued_at, due_at, status FROM invoices"
            " ORDER BY COALESCE(due_at, issued_at) DESC LIMIT 6")]
    return {
        "quotes": {"count": quote_n, "amount": round(quote_amt, 2),
                   "cost": round(quote_cost, 2)},
        "contracts": {"count": contract_n, "amount": round(contract_amt, 2)},
        "invoices": {"issued": round(invoiced, 2), "received": round(paid, 2),
                     "outstanding": round(max(invoiced - paid, 0), 2),
                     "overdue_count": overdue_n, "overdue": round(overdue_amt, 2)},
        "costs": round(cost_amt, 2),
        "list": invoices,
    }


def event_list(limit=20):
    """事件流：最近 N 条真实事件（只读）。"""
    with closing(connect()) as con:
        rows = con.execute(
            "SELECT type, task_id, payload, created_at, delivered_at FROM events"
            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    items = []
    for r in rows:
        try:
            payload = json.loads(r[2] or "{}")
        except json.JSONDecodeError:
            payload = {}
        items.append({
            "type": r[0],
            "task": r[1],
            "text": payload.get("detail") or payload.get("text") or "",
            "at": r[3],
            "delivered": bool(r[4]),
        })
    return items


def facts_view(limit=200):
    """档案室知识条目：facts 表（只读，空表就返回空）。"""
    with closing(connect()) as con:
        rows = con.execute(
            "SELECT id, content, source_level, source_ref, state, created_at"
            " FROM facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r[0], "content": r[1], "level": r[2], "ref": r[3],
             "state": r[4], "at": r[5]} for r in rows]


def sync_status():
    with closing(connect()) as con:
        raw = ag.cfg_get(con, "sync.last")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def log_failed_run(source, error):
    """控制台运行失败也落 agent_runs，避免只弹窗不留痕。"""
    with closing(connect()) as con, con:
        con.execute(
            "INSERT INTO agent_runs(role, task_id, model, prompt_hash,"
            " output_path, tokens_in, tokens_out, ok, error, created_at)"
            " VALUES ('console', NULL, ?, '', '', 0, 0, 0, ?, ?)",
            (source, str(error)[:1000], dbm.now()))


def run_sync():
    """「推进一轮」：子进程跑 agency.py sync，避免阻塞页面。"""
    global SYNCING
    with SYNC_LOCK:
        if SYNCING:
            raise ValueError("上一轮还在投递，稍后再试")
        SYNCING = True
    started = time.time()
    try:
        proc = subprocess.run([sys.executable, str(ROOT / "agency.py"), "sync"],
                              cwd=ROOT, capture_output=True, text=True,
                              timeout=SYNC_TIMEOUT)
        ok = proc.returncode == 0
        if not ok:
            log_failed_run("sync", (proc.stderr or "").strip()
                           or (proc.stdout or "")[-800:] or "投递失败")
        return {
            "ok": ok,
            "output": (proc.stdout or "")[-MAX_OUTPUT:],
            "error": "" if ok else ((proc.stderr or "").strip()
                                    or (proc.stdout or "")[-800:] or "投递失败"),
            "elapsed": round(time.time() - started, 1),
        }
    except subprocess.TimeoutExpired:
        log_failed_run("sync", f"投递超时（{SYNC_TIMEOUT} 秒）")
        return {"ok": False, "output": "", "error": f"投递超时（{SYNC_TIMEOUT} 秒）",
                "elapsed": SYNC_TIMEOUT}
    finally:
        with SYNC_LOCK:
            SYNCING = False


def toggle_subscription(data):
    event_type = str(data.get("event_type") or "").strip()
    target = str(data.get("target") or "").strip()
    action = str(data.get("action") or "").strip()
    if not event_type or not target or not action:
        raise ValueError("缺少订阅标识")
    try:
        ag.sub_toggle(event_type, target, action, bool(data.get("enabled")))
    except SystemExit as exc:
        raise ValueError(str(exc)) from None
    return {"event_type": event_type, "target": target, "action": action,
            "enabled": bool(data.get("enabled"))}


def state_etag():
    """用便宜的库内版本号算 ETag，命中时不必构建整包 snapshot。"""
    with closing(connect()) as con:
        t = con.execute("SELECT COUNT(*), COALESCE(MAX(updated_at), '')"
                        " FROM tasks").fetchone()
        e = con.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        r = con.execute("SELECT COALESCE(MAX(id), 0) FROM agent_runs").fetchone()[0]
    raw = "|".join([str(t[0]), str(t[1]), str(e), str(r), str(len(RUNNING))])
    return '"' + hashlib.sha1(raw.encode("utf-8")).hexdigest() + '"'


def snapshot():
    tasks = ag.read_tasks()
    ref = ag.today()
    for task in tasks:
        task["state"] = ag.task_state(task, ref)
        overdue = 0
        if task["state"].startswith("逾期"):
            overdue = int(re.sub(r"\D", "", task["state"]) or 0)
        task["overdue_days"] = overdue

    open_tasks = [t for t in tasks if t["db_status"] in ("todo", "doing", "blocked", "review")]
    closed_tasks = [t for t in tasks if t["db_status"] in ("done", "canceled")]
    done_tasks = [t for t in tasks if t["db_status"] == "done"]
    canceled_tasks = [t for t in tasks if t["db_status"] == "canceled"]
    owners = {}
    for task in tasks:
        slot = owners.setdefault(task["owner"], {"name": task["owner"], "open": 0,
                                                  "closed": 0, "hours": 0.0})
        slot["closed" if task["db_status"] in ("done", "canceled") else "open"] += 1
        slot["hours"] = round(slot["hours"] + (task["hours"] or 0), 1)

    gates = ag.read_gates()
    gate = gates.get("OPC-002") or (next(iter(gates.values())) if gates else {})
    with closing(connect()) as con:
        runs = [dict(r) for r in con.execute(
            "SELECT id, role, task_id, model, output_path, tokens_in, tokens_out,"
            " ok, error, created_at FROM agent_runs ORDER BY id DESC LIMIT 8")]
        counts = dict(con.execute(
            "SELECT task_id, COUNT(*) FROM agent_runs"
            " WHERE task_id IS NOT NULL AND task_id <> '' GROUP BY task_id"))
        last = {}
        for row in con.execute(
                "SELECT task_id, role, created_at, ok, output_path, tokens_in, tokens_out"
                " FROM agent_runs WHERE task_id IS NOT NULL AND task_id <> ''"
                " ORDER BY id DESC"):
            if row[0] in last:
                continue
            last[row[0]] = {
                "at": row[2], "role": row[1], "ok": bool(row[3]),
                "path": row[4], "tokens": int((row[5] or 0) + (row[6] or 0)),
                "count": counts.get(row[0], 0),
            }

    for task in tasks:
        task["last_run"] = last.get(task["id"])

    return {
        "today": ref.isoformat(),
        "generated_at": ag.utc_now(),
        "project": {"code": "OPC-002", "gate": gate},
        "llm": {
            "model": os.environ.get("AGENCY_MODEL") or ag.CFG["llm"]["model"],
            "tokens_today": ag.daily_token_usage(),
            "token_limit": int(os.environ.get("AGENCY_DAILY_TOKEN_LIMIT", "200000")),
            "running": sorted(RUNNING),
        },
        "metrics": {
            "total": len(tasks),
            "open": len(open_tasks),
            "closed": len(closed_tasks),
            "done": len(done_tasks),
            "canceled": len(canceled_tasks),
            "overdue": sum(1 for t in open_tasks if t["state"].startswith("逾期")),
            "due_today": sum(1 for t in open_tasks if t["due"] == ref.isoformat()),
            "hours_total": round(sum(t["hours"] or 0 for t in tasks), 1),
            "hours_done": round(sum(t["hours"] or 0 for t in done_tasks), 1),
            "agents": len(list((ROOT / "agents").glob("*/ROLE.md"))),
        },
        "tasks": tasks,
        "milestones": milestone_groups(tasks),
        "owners": sorted(owners.values(), key=lambda o: (-o["open"], -o["hours"])),
        "agents": agent_roster(),
        "runs": runs,
        "events": {
            "pending": ag.pending_event_count(),
            "last": sync_status(),
            "list": event_list(),
        },
        "facts": facts_view(),
        "finance": finance_view(),
        "subscriptions": subscriptions_view(),
    }


def client_state(data, projects):
    """客户角色视图：只返回自己项目内、可对外展示的任务字段。

    财务、事实台账、Agent 运行记录、事件与订阅都属于内部数据，
    客户身份不能读取，也不能借此看到其他项目的数字。
    """
    projects = set(projects or [])
    keep = ("id", "title", "project", "owner", "due", "status", "state",
            "overdue_days", "hours", "deliverable", "milestone", "closed_at")
    tasks = [{k: t.get(k) for k in keep}
             for t in data.get("tasks", [])
             if (t.get("project") or "") in projects]
    open_n = sum(1 for t in tasks if t.get("status") == "open")
    return {
        "today": data.get("today"),
        "generated_at": data.get("generated_at"),
        "project": {"code": sorted(projects)[0] if projects else "", "gate": {}},
        "tasks": tasks,
        "metrics": {
            "total": len(tasks),
            "open": open_n,
            "closed": len(tasks) - open_n,
            "overdue": sum(1 for t in tasks if str(t.get("state") or "").startswith("逾期")),
            "hours_total": round(sum(t.get("hours") or 0 for t in tasks), 1),
        },
    }


# ===== 定时任务清单 =====
SCHEDULE_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_SPEC = {
    "prod": [
        ("listener", "com.fde.listener", "飞书消息监听", "常驻",
         "长连接接收指令；只读展示，不在这里改配置"),
        ("console", "com.fde.console", "3D 办公室控制台", "常驻",
         "对外展示页，127.0.0.1:8787"),
        ("board", "com.fde.board", "管理看板服务", "常驻",
         "对内看板，127.0.0.1:8797"),
        ("tick", "com.longyi.fde.agency.tick", "定时器总入口", "",
         "串联下面 5 个内部到点动作"),
        ("selfcheck", "com.fde.selfcheck", "每日自检", "",
         "监听器/看板/定时器/飞书凭据/死信"),
        ("git-sync", "com.fde.git-sync", "夜间代码同步", "",
         "每小时拉 GitHub main，HEAD 变化才做 bundle 备份"),
    ],
    "dev": [
        ("listener", "com.fde.dev.listener", "飞书消息监听", "常驻",
         "长连接接收指令；只读展示，不在这里改配置"),
        ("console", "com.fde.dev.console", "3D 办公室控制台", "常驻",
         "开发副本，127.0.0.1:8788"),
        ("board", "com.fde.dev.board", "管理看板服务", "常驻",
         "开发看板，127.0.0.1:8798"),
        ("tick", "com.longyi.fde.dev.tick", "定时器总入口", "",
         "串联下面 5 个内部到点动作"),
        ("selfcheck", "com.fde.dev.selfcheck", "每日自检", "",
         "监听器/看板/定时器/飞书凭据/死信"),
        ("git-sync", "com.fde.git-sync", "夜间代码同步", "",
         "每小时拉 GitHub main，HEAD 变化才做 bundle 备份"),
    ],
}
INTERNAL_SPEC = [
    ("due_scan", "到期扫描", "临期/逾期任务、逾期发票、逾期依赖"),
    ("event_sync", "事件投递", "事件投递飞书/唤醒订阅角色，含自动派活"),
    ("content_due", "内容日历到点", "计划发布日已到的内容置为到期并发事件"),
    ("daily_brief", "每日态势", "每天 09:00 后首轮 tick 出一次态势"),
    ("dead_letter_retry", "死信重试", "模型不可用的失败运行按退避自动重跑"),
]


def _plist_of(label):
    p = SCHEDULE_DIR / (label + ".plist")
    if not p.exists():
        return {}
    try:
        with open(p, "rb") as f:
            return plistlib.load(f)
    except Exception:
        return {}


def _schedule_text(plist, fallback="常驻"):
    sec = plist.get("StartInterval")
    if sec:
        return ("每 %d 分钟" % (int(sec) // 60)) if int(sec) % 60 == 0 \
            else ("每 %d 秒" % int(sec))
    cal = plist.get("StartCalendarInterval")
    if isinstance(cal, dict):
        return "每天 %02d:%02d" % (cal.get("Hour", 0), cal.get("Minute", 0))
    if isinstance(cal, list) and cal:
        hours = sorted(int(c.get("Hour", 0)) for c in cal)
        if len(hours) > 2 and hours == list(range(hours[0], hours[-1] + 1)):
            return "每天 %02d:00–%02d:00 每小时" % (hours[0], hours[-1])
        return "、".join("每天 %02d:%02d" % (c.get("Hour", 0), c.get("Minute", 0))
                        for c in cal)
    return fallback


def _iso_local(iso):
    """UTC ISO → 本地 aware 时间，供计算下次预计。"""
    try:
        t = dt.datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone()


def _git_sync_status():
    """夜间同步没有进 job_runs（shell 脚本），直接读它自己的日志最后一行。"""
    p = ROOT / "logs" / "git-sync.log"
    if not p.exists():
        return "", None, "还没有执行记录"
    lines = [x for x in p.read_text(encoding="utf-8", errors="replace").splitlines()
             if x.strip()]
    if not lines:
        return "", None, "日志为空"
    ts, _, rest = lines[-1].partition(" | ")
    ok = not (rest.startswith("失败") or rest.startswith("跳过"))
    return ts, ok, rest


def scheduled_jobs():
    """launchd + 程序内部到点动作的统一只读清单。"""
    env = ag.APP_ENV
    with closing(connect()) as con:
        con.row_factory = sqlite3.Row
        last = {r["job"]: dict(r) for r in con.execute(
            "SELECT job, started_at, finished_at, ok, detail, trigger"
            " FROM job_runs WHERE id IN (SELECT MAX(id) FROM job_runs GROUP BY job)")}
        recent = [dict(r) for r in con.execute(
            "SELECT job, started_at, finished_at, ok, detail, trigger"
            " FROM job_runs ORDER BY id DESC LIMIT 30")]
    now = dt.datetime.now().astimezone()
    rows = []
    tick_interval = 900
    tick_next = None
    for name, label, title, sched_hint, what in LAUNCHD_SPEC[env]:
        plist = _plist_of(label)
        info = ag._service_info(label)
        if name == "tick":
            tick_interval = int(plist.get("StartInterval") or 900)
        row = {
            "name": title,
            "kind": "launchd",
            "label": label,
            "what": what,
            "schedule": _schedule_text(plist, sched_hint),
            "running": bool(info.get("running")),
            "pid": info.get("pid") or "",
            "loaded": bool(info.get("loaded")),
            "last_at": "", "last_ok": None, "last_detail": "",
            "next_at": "", "ok": True, "note": "",
        }
        if name in ("tick", "selfcheck"):
            rec = last.get(name)
            if rec:
                row["last_at"] = rec["started_at"]
                row["last_ok"] = bool(rec["ok"])
                row["last_detail"] = rec["detail"] or ""
            if name == "tick":
                base = _iso_local(rec["started_at"]) if rec else None
                if base:
                    tick_next = base + dt.timedelta(seconds=tick_interval)
                    row["next_at"] = tick_next.isoformat()
                    overdue = (now - base).total_seconds() > tick_interval * 2
                    row["ok"] = bool(rec["ok"]) and not overdue
                    if overdue:
                        row["note"] = "超过 2 个周期未运行"
                    elif not rec["ok"]:
                        row["note"] = "上轮失败"
                else:
                    row["ok"] = False
                    row["note"] = "没有任何执行记录"
            else:
                cal = plist.get("StartCalendarInterval") or {}
                hour, minute = cal.get("Hour", 9), cal.get("Minute", 0)
                nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if nxt <= now:
                    nxt += dt.timedelta(days=1)
                row["next_at"] = nxt.isoformat()
                if rec:
                    row["ok"] = bool(rec["ok"])
                    if not rec["ok"]:
                        row["note"] = "上次自检红灯"
                else:
                    row["ok"] = None
                    row["note"] = "暂无台账记录（自检结果见日志）"
        elif name == "git-sync":
            ts, ok, detail = _git_sync_status()
            row["last_at"] = ts
            row["last_ok"] = ok
            row["last_detail"] = detail
            hours = [1, 2, 3, 4, 5, 6, 7]
            future = [h for h in hours if h > now.hour]
            nxt = now.replace(hour=future[0] if future else 1, minute=0,
                              second=0, microsecond=0)
            if not future:
                nxt += dt.timedelta(days=1)
            row["next_at"] = nxt.isoformat()
            in_window = 1 <= now.hour <= 7
            try:
                base = dt.datetime.fromisoformat(ts).astimezone() if ts else None
            except ValueError:
                base = None
            stale = bool(in_window and base and
                         (now - base).total_seconds() > 75 * 60)
            row["ok"] = (ok is not False) and not stale
            if ok is False:
                row["note"] = "上轮失败或跳过"
            elif stale:
                row["note"] = "本夜超过 75 分钟未运行"
            elif not in_window:
                row["note"] = "仅夜间 01:00–07:00 触发"
        else:
            row["ok"] = bool(info.get("running"))
            if not row["ok"]:
                row["note"] = "未运行"
        rows.append(row)
    # 内部到点动作：与 tick 同周期
    for job, title, what in INTERNAL_SPEC:
        rec = last.get(job)
        row = {
            "name": title, "kind": "内部", "label": job, "what": what,
            "schedule": "每 %d 分钟" % (tick_interval // 60),
            "running": rec is not None and rec.get("finished_at") in (None, ""),
            "pid": "", "loaded": True,
            "last_at": rec["started_at"] if rec else "",
            "last_ok": bool(rec["ok"]) if rec else None,
            "last_detail": (rec["detail"] or "") if rec else "",
            "next_at": tick_next.isoformat() if tick_next else "",
            "ok": True, "note": "",
        }
        if not rec:
            row["ok"] = None
            row["note"] = "还没有执行过"
        else:
            base = _iso_local(rec["started_at"])
            # 死信重试只在投递时段（09:00-23:00）执行，夜间不跑属正常
            window_limited = job == "dead_letter_retry"
            in_window = 9 <= now.hour < 23
            overdue = bool(base and (now - base).total_seconds() > tick_interval * 2)
            stale = overdue and (in_window or not window_limited)
            row["ok"] = bool(rec["ok"]) and not stale
            if not rec["ok"]:
                row["note"] = "上轮失败"
            elif stale:
                row["note"] = "超过 2 个周期没有记录"
            elif window_limited and not in_window:
                row["note"] = "非投递时段，夜间不重试"
        rows.append(row)
    return {"env": env, "jobs": rows, "recent": recent,
            "generated_at": dbm.now()}


def board_view(port=None):
    """创始人管理看板：一次取全首屏所需数据，全部只读。

    在 snapshot() 基础上补三块台账细节：待确认依赖、今天的活动、最近的异常。
    """
    data = snapshot()
    day = data["today"]
    with closing(connect()) as con:
        con.row_factory = sqlite3.Row
        deps = [dict(r) for r in con.execute(
            "SELECT id, need, impact, assignee, due_at, status, note"
            " FROM dependencies WHERE provided_at IS NULL"
            " ORDER BY due_at, id")]
        closed_today = [dict(r) for r in con.execute(
            "SELECT id, title, owner, kind, hours, closed_at FROM tasks"
            " WHERE closed_at IS NOT NULL AND date(closed_at, 'localtime') = ?"
            " ORDER BY closed_at DESC", (day,))]
        runs_today = [dict(r) for r in con.execute(
            "SELECT role, task_id, model, ok, error, created_at, tokens_in, tokens_out"
            " FROM agent_runs WHERE date(created_at, 'localtime') = ?"
            " ORDER BY id DESC LIMIT 50", (day,))]
        last_runs = {r["role"]: dict(r) for r in con.execute(
            "SELECT role, task_id, ok, error, created_at FROM agent_runs"
            " WHERE id IN (SELECT MAX(id) FROM agent_runs GROUP BY role)")}
        failed = [dict(r) for r in con.execute(
            "SELECT role, task_id, error, created_at FROM agent_runs"
            " WHERE ok = 0 ORDER BY id DESC LIMIT 15")]
        dead = [dict(r) for r in con.execute(
            "SELECT role, error, attempts, created_at FROM dead_letter"
            " WHERE resolved_at IS NULL ORDER BY id DESC LIMIT 15")]
    health = ag.selfcheck_data()
    return {
        "state": data,
        "health": health,
        "version": {
            "env": ag.APP_ENV,
            "db": str(dbm.DB_PATH),
            "port": int(os.environ.get(
                "AGENCY_PORT", "8788" if ag.APP_ENV == "dev" else "8787")),
            "board_port": int(port) if port else None,
            "gate_mode": ag.finance_gate_mode(),
            "roundtable_tokens": ag.roundtable_cfg()["max_tokens"],
        },
        "deps": deps,
        "jobs": scheduled_jobs(),
        "closed_today": closed_today,
        "runs_today": runs_today,
        "last_runs": last_runs,
        "failed_runs": failed,
        "dead_letters": dead,
    }


def is_agent_role(name):
    return name in dbm.ROLE


def create_task(data):
    title = str(data.get("title") or "").strip()
    owner = str(data.get("owner") or "").strip()
    done_when = str(data.get("done_when") or "").strip()
    project = str(data.get("project") or "OPC-002").strip()
    kind = str(data.get("kind") or "general").strip()
    missing = [name for name, value in
               [("标题", title), ("负责人", owner), ("完成标准", done_when)] if not value]
    if missing:
        raise ValueError("要素不全，缺少：" + "、".join(missing))
    if not is_agent_role(owner):
        raise ValueError("负责人必须是已注册角色：%s" % "、".join(dbm.ROLE))
    start = parse_day(data.get("start"), "开始日期")
    due = parse_day(data.get("due"), "截止日期")
    if start > due:
        raise ValueError("开始日期不能晚于截止日期")
    if kind == "code" and not ag.gate_ok(project):
        raise ValueError(f"开工闸门未通过：{project} 尚无确认单，code 任务不得开工")
    hours = data.get("hours")
    hours = float(hours) if hours not in (None, "") else None

    return ag.create_task_record({
        "title": title, "owner": owner, "done_when": done_when,
        "project": project, "kind": kind, "start": start.isoformat(),
        "due": due.isoformat(), "hours": hours, "dep": data.get("dep"),
        "deliverable": data.get("deliverable"), "milestone": data.get("milestone"),
    })


def close_task(task_id, data):
    evidence = str(data.get("evidence") or "").strip()
    if not evidence:
        raise ValueError("没有证据不能关闭任务")
    ag.close_task_record(task_id, evidence, str(data.get("verify") or "").strip())
    return task_id


def update_task(task_id, data):
    allowed = ("start", "due", "owner", "hours", "dep", "done_when",
               "deliverable", "milestone")
    fields = {}
    for key in allowed:
        if key not in data:
            continue
        value = data[key]
        if key in ("start", "due"):
            value = parse_day(value, "开始日期" if key == "start" else "截止日期").isoformat()
            fields["start_at" if key == "start" else "due_at"] = value
        elif key == "hours":
            fields["hours"] = float(value) if value not in (None, "") else None
        else:
            fields[key] = str(value or "").strip()
    if not fields:
        raise ValueError("没有要更新的字段")

    with closing(connect()) as con, con:
        row = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise ValueError("任务不存在：" + task_id)
        start = fields.get("start_at", row["start_at"])
        due = fields.get("due_at", row["due_at"])
        if start and due and parse_day(start, "开始日期") > parse_day(due, "截止日期"):
            raise ValueError("开始日期不能晚于截止日期")
        merged = {**dict(row), **fields}
        sets = ", ".join(f"{key} = ?" for key in fields)
        con.execute(
            f"UPDATE tasks SET {sets}, content_hash = ?, updated_at = ? WHERE id = ?",
            (*fields.values(),
             ag.task_content_hash(merged["project_id"], merged["title"],
                                  merged["owner"], merged["done_when"],
                                  merged["due_at"] or ""),
             dbm.now(), task_id),
        )
    return task_id


def _validate_run(role, data):
    roles = {item["name"] for item in agent_roster()}
    if role not in roles:
        raise ValueError("角色不存在：" + role)
    task_id = str(data.get("task") or "").strip()
    note = str(data.get("note") or "").strip()
    if len(note) > 2000:
        raise ValueError("指令太长，控制在 2000 字以内")
    warning = ""
    if task_id:
        with closing(connect()) as con:
            row = con.execute("SELECT owner FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                raise ValueError("任务不存在：" + task_id)
            if row[0] and row[0] != role:
                warning = f"{task_id} 的负责人是 {row[0]}，本次由 {role} 跨角色执行"
    return task_id, note, warning


def _execute(role, task_id, note):
    started = time.time()
    cmd = [sys.executable, str(ROOT / "agency.py"), "run", role]
    if task_id:
        cmd += ["--task", task_id]
    if note:
        cmd += ["--note", note]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              timeout=RUN_TIMEOUT)
        ok = proc.returncode == 0
        row = None
        if ok:
            with closing(connect()) as con:
                row = con.execute(
                    "SELECT model, output_path, tokens_in, tokens_out FROM agent_runs"
                    " WHERE role = ? ORDER BY id DESC LIMIT 1", (role,)).fetchone()
        return {
            "ok": ok,
            "output": (proc.stdout or "")[-MAX_OUTPUT:],
            "error": "" if ok else ((proc.stderr or "").strip() or (proc.stdout or "")[-800:]
                                    or "执行失败"),
            "path": row["output_path"] if row else "",
            "model": row["model"] if row else "",
            "tokens": (int(row["tokens_in"] or 0) + int(row["tokens_out"] or 0)) if row else 0,
            "elapsed": round(time.time() - started, 1),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "path": "", "model": "", "tokens": 0,
                "error": f"执行超时（{RUN_TIMEOUT} 秒），进程已终止", "elapsed": RUN_TIMEOUT}


def _trim_jobs(limit=30):
    for jid in sorted(JOBS, key=lambda x: int(x.split("-")[1]))[:-limit]:
        JOBS.pop(jid, None)


def _retryable(error):
    """限流类（分钟窗口 TPM/RPM）可退避重试；模型不存在、权限不足属永久错误。"""
    low = (error or "").lower()
    if any(k in low for k in ("model_not_found", "not found", "does not exist",
                              "no such model", "invalid_api_key", "permission",
                              "not authorized", "401", "403", "404")):
        return False
    return any(k in low for k in ("insufficient_quota", "rate_limit", "too many requests",
                                  "429", "rpm exhausted", "rps exhausted", "quota_exceeded",
                                  "超时", "连接失败"))


def _job_worker(jid, role, task_id, note):
    attempt = 0
    try:
        while True:
            if attempt:
                with JOB_LOCK:
                    job = JOBS.get(jid)
                    if job:
                        job.update(state="running", retry_in=0)
            result = _execute(role, task_id, note)
            if result["ok"] or not _retryable(result["error"]) or attempt >= RETRY_MAX:
                break
            attempt += 1
            wait = RETRY_BASE * (2 ** (attempt - 1))
            with JOB_LOCK:
                job = JOBS.get(jid)
                if job:
                    job.update(state="retrying", attempt=attempt,
                               retry_in=int(wait), retry_total=RETRY_MAX,
                               error=result["error"][:300])
            time.sleep(wait)
    except Exception as exc:  # noqa: BLE001 — 后台任务必须落状态
        result = {"ok": False, "output": "", "error": str(exc), "path": "",
                  "model": "", "tokens": 0, "elapsed": 0}
    finally:
        with RUN_LOCK:
            RUNNING.discard(role)
    with JOB_LOCK:
        job = JOBS.get(jid)
        if job:
            job.update(state="done" if result["ok"] else "failed", ok=result["ok"],
                       output=result["output"], error=result["error"],
                       path=result["path"], model=result["model"],
                       tokens=result["tokens"], elapsed=result["elapsed"],
                       retry_in=0, finished_at=ag.utc_now())
            _trim_jobs()


def start_run(role, data):
    """提交后台执行：立即返回 job，进程跑完把结果写进 JOBS。"""
    task_id, note, warning = _validate_run(role, data)
    with RUN_LOCK:
        if role in RUNNING:
            raise ValueError("该角色正在执行，等它跑完再派")
        # 并发闸门：上限来自 token plan 实测（flash-lite 并发 4 全通、8 有 3/8 被拒）
        if len(RUNNING) >= MAX_PARALLEL:
            raise ValueError(f"已有 {len(RUNNING)} 个任务在跑，达到实测安全并发 {MAX_PARALLEL}，等一个结束再派")
        RUNNING.add(role)
        with JOB_LOCK:
            JOB_SEQ[0] += 1
            jid = "R-%04d" % JOB_SEQ[0]
            JOBS[jid] = {
                "id": jid, "role": role, "task": task_id, "warning": warning,
                "state": "running", "started_at": ag.utc_now(),
                "started": time.time(), "ok": None, "output": "", "error": "",
                "path": "", "model": "", "tokens": 0, "elapsed": 0,
                "attempt": 0, "retry_in": 0, "retry_total": RETRY_MAX,
            }
            job = dict(JOBS[jid])
    threading.Thread(target=_job_worker, args=(jid, role, task_id, note),
                     daemon=True).start()
    return job


def jobs_view(limit=20):
    with JOB_LOCK:
        items = []
        for job in sorted(JOBS.values(), key=lambda x: -int(x["id"].split("-")[1]))[:limit]:
            item = {k: v for k, v in job.items() if k != "started"}
            if job["state"] == "running":
                item["elapsed"] = round(time.time() - job["started"], 1)
            items.append(item)
    return items


# ===== 意图识别 =====
# 只做 evidence 采集与规则判定，决策由评分模型驱动（经验 1276236）
NON_AGENT_PATTERNS = [
    (re.compile(r"^开个|^开 |^创建|^新建|^加个任务|^加一个任务"), "create_task"),
    (re.compile(r"^关[一1个]?任务|^close |^关闭任务|^完成任务"), "close_task"),
    (re.compile(r"^查[一1下]|^看看|^查看|^列出|^list "), "view"),
    (re.compile(r"^同步|^sync|^推进一轮"), "sync"),
    (re.compile(r"^统计|^报个数|^报表|^数据"), "report"),
]


def diagnose_intent(data):
    """意图识别：统一走 intent.classify_instruction（关键字 + bundle 打分）。"""
    return intent.classify_instruction(
        data.get("instruction"), ROLE_MISSIONS, INTENT_BUNDLES, NON_AGENT_PATTERNS)

def biz_view():
    """商务页数据：五类单据 + 汇总（已开票/已回款/未回款/逾期）。"""
    with closing(connect()) as con:
        con.row_factory = sqlite3.Row
        data = {}
        for table in ("clients", "quotes", "contracts", "invoices", "payments"):
            data[table] = [dict(r) for r in con.execute(
                "SELECT * FROM " + table + " ORDER BY rowid DESC LIMIT 50")]
        invoiced = con.execute("SELECT COALESCE(SUM(amount),0) AS s FROM invoices"
                               " WHERE status != 'void'").fetchone()["s"]
        paid = con.execute("SELECT COALESCE(SUM(amount),0) AS s FROM payments"
                           ).fetchone()["s"]
        rows = con.execute(
            "SELECT i.amount, i.due_at, i.status,"
            " COALESCE((SELECT SUM(amount) FROM payments p WHERE p.invoice_id=i.id),0)"
            " AS paid FROM invoices i").fetchall()
    unpaid = overdue = 0.0
    n_over = 0
    ref = ag.today().isoformat()
    for r in rows:
        if r["status"] == "void":
            continue
        remain = float(r["amount"]) - float(r["paid"])
        if remain <= 0:
            continue
        unpaid += remain
        if r["due_at"] and r["due_at"] < ref:
            overdue += remain
            n_over += 1
    data["summary"] = {"invoiced": invoiced, "paid": paid, "unpaid": unpaid,
                       "overdue": overdue, "overdue_count": n_over}
    return data


def content_view():
    """内容日历：本月计划、待发布、按渠道汇总。"""
    with closing(connect()) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM content_plan ORDER BY planned_at, id LIMIT 200")]
        summary = [dict(r) for r in con.execute(
            "SELECT channel, COUNT(*) AS n,"
            " SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published,"
            " SUM(views) AS views, SUM(likes) AS likes,"
            " SUM(comments) AS comments, SUM(dms) AS dms"
            " FROM content_plan GROUP BY channel ORDER BY channel")]
    month = ag.today().strftime("%Y-%m")
    return {"plans": rows, "summary": summary, "month": month,
            "this_month": [r for r in rows
                           if (r.get("planned_at") or "").startswith(month)],
            "pending": [r for r in rows if r.get("status") == "planned"]}


def content_cli(payload):
    kind = (payload.get("kind") or "").strip()
    args = [sys.executable, str(ROOT / "agency.py"), "content"]
    if kind == "add":
        if not (payload.get("title") or "").strip():
            return {"ok": False, "error": "标题不能为空"}
        args += ["add", "--channel", str(payload.get("channel") or ""),
                 "--title", str(payload.get("title")),
                 "--planned", str(payload.get("planned") or "")]
    elif kind == "update":
        if not payload.get("id"):
            return {"ok": False, "error": "缺少计划编号"}
        args += ["update", str(payload["id"])]
        for key, flag in (("status", "--status"), ("published_at", "--published-at")):
            if payload.get(key):
                args += [flag, str(payload[key])]
        for key in ("views", "likes", "comments", "dms"):
            if str(payload.get(key) or "").strip():
                args += ["--" + key, str(payload[key])]
    else:
        return {"ok": False, "error": "未知操作"}
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30)
    return {"ok": proc.returncode == 0,
            "output": (proc.stdout or proc.stderr).strip()[-800:]}


def finance_cli(payload):
    """商务表单 → 调 CLI（单一校验入口，避免前端重复实现规则）。"""
    kind = (payload.get("kind") or "").strip()
    args = [sys.executable, str(ROOT / "agency.py"), "finance"]
    if kind == "client":
        name = (payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "客户名称不能为空"}
        args += ["client", "add", name]
        for key in ("contact", "phone", "email", "note"):
            if payload.get(key):
                args += ["--" + key, str(payload[key])]
    elif kind in ("quote", "contract"):
        project = (payload.get("project") or "").strip()
        if not project:
            return {"ok": False, "error": "项目不能为空"}
        args += [kind, "add", project, "--amount", str(payload.get("amount") or 0)]
        for key, flag in (("quote_id", "--quote-id"), ("client_id", "--client-id"),
                          ("cost", "--cost"), ("valid_until", "--valid-until"),
                          ("signed_at", "--signed-at")):
            if payload.get(key):
                args += [flag, str(payload[key])]
    elif kind == "invoice":
        contract = (payload.get("contract_id") or "").strip()
        if not contract:
            return {"ok": False, "error": "合同不能为空"}
        args += ["invoice", "add", contract, "--no", str(payload.get("no") or ""),
                 "--amount", str(payload.get("amount") or 0),
                 "--issued-at", str(payload.get("issued_at") or ""),
                 "--due-at", str(payload.get("due_at") or "")]
    elif kind == "payment":
        invoice = (payload.get("invoice_id") or "").strip()
        if not invoice:
            return {"ok": False, "error": "发票不能为空"}
        args += ["payment", "add", invoice, "--amount", str(payload.get("amount") or 0),
                 "--paid-at", str(payload.get("paid_at") or "")]
    else:
        return {"ok": False, "error": "未知单据类型"}
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30)
    return {"ok": proc.returncode == 0,
            "output": (proc.stdout or proc.stderr).strip()[-800:]}


class Handler(BaseHTTPRequestHandler):
    server_version = "FDEConsole/1.0"

    def _send(self, status, body, content_type, headers=None):
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def _host_ok(self):
        """只接受本机 Host，挡住 DNS rebinding。"""
        host = urlparse("//" + (self.headers.get("Host") or "")).hostname or ""
        return host.lower() in ALLOWED_HOSTS

    def _origin_ok(self):
        """写请求若带 Origin，必须是同源本机；无 Origin 的本地脚本放行。"""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        if parsed.scheme != "http" or (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
            return False
        host = (self.headers.get("Host") or "").lower()
        return parsed.netloc.lower() == host

    def _token_ok(self):
        return self._principal() is not None

    def _principal(self):
        """Legacy token auth is retained only until the new identity API cutover."""
        if not LEGACY_CONSOLE_AUTH_ENABLED:
            return None
        if getattr(self, "_p_cache", False):
            return self._p_cache
        supplied = self.headers.get(TOKEN_HEADER) or ""
        if not supplied:
            supplied = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
        if not supplied:
            return None
        if hmac.compare_digest(supplied, CONSOLE_TOKEN):
            principal = {"role": "founder", "projects": [], "name": "创始人"}
        else:
            with closing(connect()) as con:
                con.row_factory = sqlite3.Row
                row = con.execute("SELECT * FROM users WHERE console_token = ?",
                                  (supplied,)).fetchone()
            if not row:
                return None
            principal = {"role": row["role"], "name": row["name"],
                         "projects": [x for x in (row["projects"] or "").split(",") if x]}
        self._p_cache = principal
        return principal

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("请求体为空或过大")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("请求体不是合法 JSON") from None

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._host_ok():
            self._json({"ok": False, "error": "Host 不允许"}, 403)
            return
        if path.startswith("/d/"):
            self._serve_delivery(path[3:])
            return
        if BOARD_ONLY and path not in ("/board", "/board.html",
                                       "/api/board", "/api/scheduled"):
            self._json({"ok": False, "error": "本服务只提供看板：/board 与 /api/board"}, 404)
            return
        if path in ("/board", "/board.html"):
            if not BOARD_ONLY:
                self._send(404, "管理看板已迁到独立端口，本控制台不再提供。",
                           "text/plain; charset=utf-8")
                return
            if not BOARD_PATH.exists():
                self._send(500, "缺少 dashboard.html", "text/plain; charset=utf-8")
                return
            self._send(200, BOARD_PATH.read_text(encoding="utf-8"),
                       "text/html; charset=utf-8")
            return
        if path.startswith("/api"):
            principal = self._principal()
            if not principal:
                self._json({"ok": False, "error": "缺少或错误的访问令牌"}, 401)
                return
            if principal["role"] == "client" and path != "/api/state":
                self._json({"ok": False, "error": "该角色无权访问"}, 403)
                return
            if principal["role"] == "client":
                want = (parse_qs(urlparse(self.path).query).get("project") or [""])[0]
                if want and want not in principal["projects"]:
                    self._json({"ok": False, "error": "跨项目访问被拒绝"}, 403)
                    return
        try:
            if path in ("/", "/index.html"):
                # 3D 办公室页面已退役：旧系统只提供 API 与独立看板（/board）。
                self._send(410, "3D 办公室页面已退役，请使用平台 /app/workspace/office；"
                                "旧系统看板见 /board", "text/plain; charset=utf-8")
                return
            elif path == "/api/state":
                # ETag + 304：轮询时状态未变就不构建整包，也不重复传输
                etag = state_etag()
                if self.headers.get("If-None-Match") == etag:
                    self._send(304, b"", "application/json; charset=utf-8",
                               {"ETag": etag})
                else:
                    data = snapshot()
                    if principal and principal["role"] == "client":
                        data = client_state(data, principal["projects"])
                    self._send(200,
                               json.dumps({"ok": True, "data": data},
                                          ensure_ascii=False),
                               "application/json; charset=utf-8", {"ETag": etag})
            elif path == "/api/jobs":
                self._json({"ok": True, "data": jobs_view()})
            elif path == "/api/board":
                if not BOARD_ONLY:
                    self._json({"ok": False, "error": "看板接口已迁到独立端口"}, 404)
                    return
                self._json({"ok": True, "data": board_view(
                    port=self.server.server_address[1])})
            elif path == "/api/scheduled":
                self._json({"ok": True, "data": scheduled_jobs()})
            elif path == "/api/finance":
                self._json({"ok": True, "data": biz_view()})
            elif path == "/api/content":
                self._json({"ok": True, "data": content_view()})
            elif path == "/api/roundtable":
                self._json({"ok": True, "data": ag._roundtable_sessions()})
            elif path == "/api/selfcheck":
                data = ag.selfcheck_data()
                data["version"] = {
                    "env": ag.APP_ENV,
                    "db": str(dbm.DB_PATH),
                    "port": int(os.environ.get(
                        "AGENCY_PORT", "8788" if ag.APP_ENV == "dev" else "8787")),
                    "gate_mode": ag.finance_gate_mode(),
                    "roundtable_tokens": ag.roundtable_cfg()["max_tokens"],
                }
                self._json({"ok": True, "data": data})
            elif path == "/api/output":
                self._serve_output()
            else:
                self._json({"ok": False, "error": "未找到该地址"}, 404)
        except sqlite3.Error as exc:
            self._json({"ok": False, "error": "数据库错误：" + str(exc)}, 500)
        except Exception as exc:  # noqa: BLE001 — 本地工具，错误直接回前端
            self._json({"ok": False, "error": str(exc)}, 500)

    def _serve_output(self):
        """把产出文件直接回显给页面：只允许 agency/outputs/<项目>/ 内的文件。

        带 project 参数时按项目隔离：路径首段与 project 不一致直接 403。
        """
        query = parse_qs(urlparse(self.path).query)
        raw = (query.get("path") or [""])[0]
        project_id = (query.get("project") or [""])[0].strip()
        root = ag.OUTPUT_DIR.resolve()
        rel = Path(raw)
        target = (rel if rel.is_absolute() else ROOT / rel).resolve()
        if root not in target.parents:
            target = (root / rel).resolve()
        if root not in target.parents:
            self._json({"ok": False, "error": "只能打开产出目录内的文件"}, 403)
            return
        parts = target.relative_to(root).parts
        if not parts:
            self._json({"ok": False, "error": "产出路径缺少项目目录"}, 403)
            return
        target_project = parts[0]
        if project_id and target_project != project_id:
            self._json({"ok": False, "error": "跨项目访问被拒绝"}, 403)
            return
        if not target.is_file():
            self._json({"ok": False, "error": "文件不存在：" + raw}, 404)
            return
        self._send(200, target.read_text(encoding="utf-8", errors="ignore"),
                   "text/plain; charset=utf-8")

    def _serve_delivery(self, token):
        """只读交付物：错误 token 404、过期 410、跨项目 403。"""
        with closing(connect()) as con:
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM deliveries WHERE token = ?",
                              (token,)).fetchone()
        if not row or row["revoked_at"]:
            self._send(404, "交付物不存在", "text/plain; charset=utf-8")
            return
        if row["expires_at"] and row["expires_at"] < ag.dbm.now():
            self._send(410, "交付物链接已过期", "text/plain; charset=utf-8")
            return
        project = (parse_qs(urlparse(self.path).query).get("project") or [""])[0]
        if project and row["project_id"] and project != row["project_id"]:
            self._send(403, "跨项目访问被拒绝", "text/plain; charset=utf-8")
            return
        principal = self._principal()
        if principal and principal["role"] == "client" and row["project_id"] \
                and row["project_id"] not in principal["projects"]:
            self._send(403, "跨项目访问被拒绝", "text/plain; charset=utf-8")
            return
        target = Path(row["path"]).resolve()
        root = ag.DELIVER_DIR.resolve()
        if root not in target.parents or not target.is_file():
            self._send(404, "交付物文件不存在", "text/plain; charset=utf-8")
            return
        self._send(200, target.read_text(encoding="utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        if BOARD_ONLY:
            self._json({"ok": False, "error": "看板服务只读，不提供写接口"}, 404)
            return
        path = urlparse(self.path).path
        if not self._host_ok() or not self._origin_ok():
            self._json({"ok": False, "error": "跨站请求被拒绝"}, 403)
            return
        principal = self._principal()
        if not principal:
            self._json({"ok": False, "error": "缺少或错误的访问令牌"}, 401)
            return
        if principal["role"] == "client":
            self._json({"ok": False, "error": "只读角色不能修改数据"}, 403)
            return
        if path == "/api/finance" and principal["role"] != "founder":
            self._json({"ok": False, "error": "只有创始人可以修改商务金额"}, 403)
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._json({"ok": False, "error": "Content-Type 必须为 application/json"}, 415)
            return
        try:
            data = self._body()
            if path == "/api/dispatch/diagnose":
                self._json(diagnose_intent(data))
                return
            if path == "/api/tasks":
                task_id = create_task(data)
                self._json({"ok": True, "id": task_id}, 201)
                return
            agent = AGENT_RUN.match(path)
            if agent:
                self._json({"ok": True, "data": start_run(agent.group(1), data)})
                return
            if path == "/api/finance":
                res = finance_cli(data)
                self._json(res, 201 if res.get("ok") else 400)
                return
            if path == "/api/content":
                res = content_cli(data)
                self._json(res, 201 if res.get("ok") else 400)
                return
            if path == "/api/roundtable":
                topic = (data.get("topic") or "").strip()
                if not topic:
                    raise ValueError("议题不能为空")
                try:
                    result = ag.run_roundtable(
                        topic, roles=data.get("roles") or "",
                        rounds=int(data.get("rounds") or 0),
                        project=data.get("project") or "")
                except ag.RoundtableError as exc:
                    raise ValueError(str(exc)) from None
                self._json({"ok": True, "data": {
                    "path": result["path"], "markdown": result["markdown"],
                    "roles": result["roles"], "tokens": result["tokens"],
                    "elapsed": result["elapsed"], "stopped": result["stopped"],
                    "violations": result["violations"],
                }})
                return
            if path == "/api/sync":
                self._json(run_sync())
                return
            if path == "/api/subscriptions/toggle":
                self._json({"ok": True, "data": toggle_subscription(data)})
                return
            match = TASK_ID.match(path)
            if not match:
                self._json({"ok": False, "error": "未找到该地址"}, 404)
                return
            task_id, action = match.groups()
            if action == "close":
                close_task(task_id, data)
            else:
                update_task(task_id, data)
            self._json({"ok": True, "id": task_id})
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
        except sqlite3.Error as exc:
            self._json({"ok": False, "error": "数据库错误：" + str(exc)}, 500)
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)}, 500)

    def log_message(self, fmt, *args):
        print("[console] " + fmt % args)


class ConsoleServer(ThreadingHTTPServer):
    """控制台 HTTP 服务：放大监听队列，避免批量建单时连接被内核 reset。"""
    request_queue_size = 64


def main():
    global BOARD_ONLY
    dbm.require_env()
    parser = argparse.ArgumentParser(description="龍奕FDE工作室 · 本地操作控制台")
    parser.add_argument("--board-only", action="store_true",
                        help="只提供创始人管理看板（/board 与 /api/board），与操作台分开端口")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    BOARD_ONLY = bool(args.board_only)
    if args.port is None:
        default = os.environ.get("AGENCY_BOARD_PORT") if BOARD_ONLY else None
        if not default:
            default = ("8798" if ag.APP_ENV == "dev" else "8797") if BOARD_ONLY \
                else ("8788" if ag.APP_ENV == "dev" else "8787")
        args.port = int(default)
    server = ConsoleServer(("127.0.0.1", args.port), Handler)
    if BOARD_ONLY:
        print(f"管理看板已启动：http://127.0.0.1:{args.port}/board?token={CONSOLE_TOKEN}"
              "  （仅本机回环；Ctrl+C 停止）")
    else:
        print(f"控制台已启动：http://127.0.0.1:{args.port}/?token={CONSOLE_TOKEN}"
              "  （Ctrl+C 停止；令牌只在本机终端显示）")
    print(f"事实源：{dbm.DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
