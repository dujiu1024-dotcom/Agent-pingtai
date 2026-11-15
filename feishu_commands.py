#!/usr/bin/env python3
"""feishu_commands.py — 飞书指令路由（T-0021）

把飞书消息解析成指令，做发送人白名单校验，再调 `agency.py` 子进程执行。
纯标准库；`listener.py` 负责收发，本文件不依赖 lark-oapi，可单独测试。

指令：
  /help                              用法
  /brief                             一页态势
  /sync [--force]                    推进一轮事件投递
  /run <角色> [任务ID] [附加指令]     唤醒某个 Agent
"""

import datetime as dt
import os
import re
import shlex
import subprocess
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402

MAX_REPLY = 2500
SYNC_TIMEOUT = 180
BRIEF_TIMEOUT = 120
RUN_TIMEOUT = 600
PLAN_TIMEOUT = 120
TASK_ADD_TIMEOUT = 60
ROUNDTABLE_TIMEOUT = 900

# 指令清单唯一来源：既用于生成 /help，也用于测试与实际 parse 对齐
COMMAND_SPECS = [
    ("/brief", "一页态势（任务 / 临期 / 闸门）"),
    ("/roundtable <议题> [--roles a,b,c] [--rounds N] [--project X]", "圆桌会议：多角色讨论并收敛结论"),
    ("/run <角色> [任务ID] [附加指令]", "唤醒某个 Agent"),
    ("/plan [--project X] [--days N] <目标>", "把目标拆成子任务并入库"),
    ("/task add <标题> --owner <角色> [--days N] [--project X] [--check <验收命令>]", "快速建一条任务"),
    ("/dispatch [--limit N] [--project X]", "推进一轮派活（筛选 ready 任务）"),
    ("/sync [--force]", "推进一轮事件投递"),
    ("/help [--verbose]", "看这份说明"),
]

# 能力域 → 角色；角色卡新增后自动进入对应域，未知角色落到「其他」
DOMAINS = [
    ("任务与计划", ["chief-of-staff", "planner", "producer", "pm"]),
    ("内容", ["content"]),
    ("线索与客户", ["scout"]),
    ("财务", ["finance"]),
    ("审计与质量", ["auditor"]),
    ("人与协作", ["hr", "coach", "secretary"]),
]
CAPABILITIES = [
    ("任务与计划", "建任务、拆目标、排里程碑、盯交付进度"),
    ("派活", "按角色唤醒，结果直接回给你"),
    ("诊断与方案", "诊断包、需求说明、方案与验收标准"),
    ("内容", "对外文案、推文、公告、介绍"),
    ("线索", "找线索、判断值不值得跟"),
    ("财务", "报价、合同、发票、回款"),
    ("审计", "复核工时、报价、对账与证据来源"),
    ("事件与提醒", "临期/逾期提醒、事件投递、飞书同步"),
    ("导出迁移", "交付包导出与恢复自检"),
]


def _sections(text):
    """把 ROLE.md 拆成 {二级标题: 正文}。"""
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if cur:
                out[cur] = buf
            cur, buf = line[3:].strip(), []
        elif cur:
            buf.append(line)
    if cur:
        out[cur] = buf
    return out


def _first_para(lines):
    buf = []
    for line in lines:
        line = line.strip()
        if not line:
            if buf:
                break
            continue
        buf.append(line)
    return " ".join(buf)


def _bullets(lines, limit=2):
    items = []
    for line in lines:
        line = line.strip()
        m = re.match(r"^(?:\d+[.、)]|[-*·])\s*(.+)$", line)
        if m:
            items.append(m.group(1).strip())
        if len(items) >= limit:
            break
    return items


def _triggers(lines, limit=2):
    """触发事件：支持列表与 Markdown 表格（事件 | 动作）。"""
    items = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("|---") or line.startswith("| 事件"):
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] and cells[1]:
                items.append("%s → %s" % (cells[0], cells[1]))
        else:
            m = re.match(r"^(?:\d+[.、)]|[-*·])\s*(.+)$", line)
            if m:
                items.append(m.group(1).strip())
        if len(items) >= limit:
            break
    return items


def agent_cards():
    """扫描 agents/*/ROLE.md 与 agents/*.md，返回角色卡字段。"""
    cards = []
    base = ROOT / "agents"
    paths = sorted(base.glob("*/ROLE.md")) + [
        p for p in sorted(base.glob("*.md")) if p.name != "ontology.md"]
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        sections = _sections(text)
        title = ""
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        duties = [d.rstrip("。；; ") for d in _bullets(sections.get("2. 职责", []), 2)]
        triggers = _triggers(sections.get("6. 触发事件", []), 2)
        role = path.parent.name if path.name == "ROLE.md" else path.stem
        cards.append({
            "role": role,
            "name": title or role,
            "identity": _first_para(sections.get("1. 身份", [])),
            "duties": duties,
            "triggers": triggers,
            "triage": _first_para(sections.get("10. 接待与分诊", [])),
        })
    return cards


def display_name(card):
    """角色显示名：只用中文名，不露 Agent/英文标识。"""
    return re.sub(r"\s*Agent\s*[（(].*?[)）]\s*$", "", card["name"]).strip()


def plain_text(text):
    """洗掉角色卡里的模板腔（反引号、事件名、箭头、英文角色键），只留能直接读的话。"""
    t = str(text or "")
    t = re.sub(r"`[^`]*`", "", t)
    t = re.sub(r"\(?\s*kind=[\w-]+\s*\)?", "", t, flags=re.I)
    t = re.sub(r"S[1-4]\d*\s*[-–~]\s*S[1-4]\d*", "来源", t)
    t = re.sub(r"\b(task_created|task_due|task_closed|audit_flag|S[1-4]\d*)\b", "", t)
    t = t.replace("→", "，")
    t = t.replace("Agent", "内部角色").replace("agent", "内部角色")
    t = re.sub(r"\bowner\b", "负责人", t, flags=re.I)
    names = {c["role"]: role_label(c["role"]) for c in agent_cards()}
    for role in sorted(names, key=len, reverse=True):
        t = re.sub(r"(?<![\w-])%s(?![\w-])" % re.escape(role), names[role], t)
    t = re.sub(r"[（(]\s*[)）]", "", t)
    t = re.sub(r"\s*，\s*", "，", t)
    t = re.sub(r"\s*、\s*", "、", t)
    t = re.sub(r"[；;]\s*，", "，", t)
    t = re.sub(r"[：:]\s+", "：", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    t = re.sub(r"\s*，\s*[。；]", "。", t)
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s*-\s*(?=[。，、；]|$)", "", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" ，,、。；-")


def agent_section():
    cards = {c["role"]: c for c in agent_cards()}
    used = set()
    lines = ["—— 谁是谁，什么时候找他 ——"]
    for domain, roles in DOMAINS:
        entries = []
        for role in roles:
            card = cards.get(role)
            if not card:
                continue
            used.add(role)
            duty = plain_text("；".join(card["duties"]) or card["identity"])
            when = plain_text("；".join(card["triggers"]))
            line = "· %s：%s" % (display_name(card), duty)
            if when and when != "上述职责相关时":
                line += "。什么时候用：%s" % when
            if card.get("triage"):
                first = plain_text(card["triage"].split("。")[0])
                if first:
                    line += "。%s" % first
            entries.append(line)
        if entries:
            lines.append("[%s]" % domain)
            lines.extend(entries)
    others = [c for r, c in cards.items() if r not in used]
    if others:
        lines.append("[其他]")
        for card in others:
            duty = plain_text("；".join(card["duties"]) or card["identity"])
            lines.append("· %s：%s" % (display_name(card), duty))
    return "\n".join(lines)


def command_section():
    return "\n".join("%-46s %s" % (usage, desc) for usage, desc in COMMAND_SPECS)


def capabilities_text():
    caps = ["【我能做什么】"]
    for domain, desc in CAPABILITIES:
        caps.append("· %s：%s" % (domain, desc))
    return "\n".join(caps) + "\n\n" + agent_section() + "\n\n" + \
        "直接说事就行；也可以 /help 看全部指令。"


def resolve_project(text):
    """把用户说的话解析成 projects 表里的真实项目；对不上返回 None。"""
    low = (text or "").strip().lower()
    if not low:
        return None
    with closing(dbm.connect()) as con:
        rows = con.execute("SELECT code, name FROM projects").fetchall()
    for row in rows:
        if low == row["code"].lower() or low == (row["name"] or "").lower():
            return row["code"]
    for row in rows:
        code = row["code"].lower()
        name = (row["name"] or "").lower()
        if code and code in low:
            return row["code"]
        if name and len(name) >= 3 and name in low:
            return row["code"]
    return None


def project_options():
    with closing(dbm.connect()) as con:
        rows = con.execute("SELECT code, name FROM projects ORDER BY code").fetchall()
    if not rows:
        return "（暂无项目，先建项目再建任务）"
    return "、".join(r["code"] for r in rows)


def role_label(role):
    for card in agent_cards():
        if card["role"] == role:
            return card["name"].split(" Agent")[0].strip()
    return role


def next_actions(limit=3):
    """给"下一步"用的具体动作：只取支撑判断的必要数字。"""
    import agency
    today = agency.today()
    with closing(dbm.connect()) as con:
        rows = con.execute(
            "SELECT id, title, status, owner, due_at FROM tasks"
            " WHERE status IN ('todo','doing','blocked','review')"
            " ORDER BY due_at").fetchall()
    items = []
    for row in rows:
        due = row["due_at"] or ""
        try:
            days = (dt.date.fromisoformat(due) - today).days
        except ValueError:
            days = 999
        owner = role_label(row["owner"])
        if row["status"] == "review":
            items.append("%s %s（%s）今天需发起验收：确认交付物齐全，然后 close --evidence。"
                         % (row["id"], row["title"], owner))
        elif days <= 0:
            items.append("%s %s 已到期 %d 天，%s 今天需给出完成或阻塞说明。"
                         % (row["id"], row["title"], -days, owner))
        elif days <= 2:
            items.append("%s %s 距到期 %d 天（%s），%s 需先安排前置资源。"
                         % (row["id"], row["title"], days, due, owner))
        else:
            items.append("%s %s 于 %s 到期，%s 本周需确认排期与前置条件。"
                         % (row["id"], row["title"], due, owner))
        if len(items) >= limit:
            break
    if not items:
        return "当前没有待办任务。要不要建一条？告诉我做什么、给哪个项目、谁负责、什么时候要。"
    return "\n".join("· " + it for it in items)


HELP = """最常用的三句：
· 看今天有什么要盯的 → /brief
· 叫某个人干活 → /run 产品经理 T-0011 报价单改一版
· 一句话拆活排期 → /plan 完成 FDE 一人公司 MVP
想看谁是谁，回我一句「谁是谁」；全部命令：/help --verbose"""

HELP_VERBOSE = HELP + """

—— 命令明细 ——
%s

任务号形如 T-0011；不写任务号时，会自动挑这个人手上最近的一条。
例：/run 规划师 T-0019 只做 19.3
    /plan 完成 FDE 一人公司 MVP --project OPC-002
    /dispatch --limit 3""" % command_section()


HELP_PAGE_SIZE = 6


def help_page(page=1):
    """命令明细分页：每页 6 行正文 + 1 行翻页提示，共不超过 8 行。"""
    lines = HELP_VERBOSE.splitlines()
    total = max(1, (len(lines) + HELP_PAGE_SIZE - 1) // HELP_PAGE_SIZE)
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        page = 1
    page = max(1, min(page, total))
    chunk = lines[(page - 1) * HELP_PAGE_SIZE: page * HELP_PAGE_SIZE]
    if page < total:
        chunk.append("回复「/help %d」看下一页（共 %d 页）。" % (page + 1, total))
    return "\n".join(chunk)


def _extract_flag(parts, flag, type_=str):
    """从 parts 里取 --flag value，取到后移除两项。"""
    out = None
    new_parts = []
    skip_next = False
    for i, p in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        if p == flag and i + 1 < len(parts):
            try:
                out = type_(parts[i + 1])
            except (ValueError, TypeError):
                out = None
            skip_next = True
            continue
        new_parts.append(p)
    return out, new_parts


def parse(text):
    """解析消息；非指令返回 None，指令返回 dict。"""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    head = parts[0].lower()
    rest = parts[1:]

    if head in ("/help", "/帮助"):
        page = 1
        for p in rest:
            if p.isdigit():
                page = max(1, int(p))
        return {"cmd": "/help", "verbose": "--verbose" in rest, "page": page}
    if head == "/brief":
        return {"cmd": "/brief"}
    if head == "/sync":
        return {"cmd": "/sync", "force": "--force" in rest}
    if head == "/run":
        if not rest:
            return {"cmd": "/run", "error": "用法：/run <角色> [任务ID] [附加指令]"}
        tail = rest[1:]
        task = ""
        if tail and re.fullmatch(r"T-\d{4}", tail[0]):
            task = tail.pop(0)
        return {"cmd": "/run", "role": rest[0], "task": task,
                "note": " ".join(tail)}

    if head == "/task":
        if not rest or rest[0].lower() != "add":
            return {"cmd": "/task",
                    "error": "用法：/task add <标题> --owner <角色> [--days N] [--project X] [--check <验收命令>]"}
        rest = rest[1:]
        owner, rest = _extract_flag(rest, "--owner")
        days, rest = _extract_flag(rest, "--days", int)
        project, rest = _extract_flag(rest, "--project")
        check, rest = _extract_flag(rest, "--check")
        done_when, rest = _extract_flag(rest, "--done-when")
        # --start/--due 由调用方算好传进来，标题里不能残留（否则标题被参数污染）
        start, rest = _extract_flag(rest, "--start")
        due, rest = _extract_flag(rest, "--due")
        # 任何未识别的 --xxx 连同它的值一起丢掉，不拼进标题
        cleaned, skip_next = [], False
        for p in rest:
            if skip_next:
                skip_next = False
                continue
            if p.startswith("--"):
                skip_next = True
                continue
            cleaned.append(p)
        title = " ".join(cleaned).strip()
        if not title:
            return {"cmd": "/task", "error": "标题不能为空"}
        if not owner:
            return {"cmd": "/task",
                    "error": "缺少 --owner <角色>。可用角色：" + " / ".join(dbm.ROLE)}
        return {"cmd": "/task", "sub": "add", "title": title, "owner": owner,
                "days": days or 3, "project": project or "",
                "check": check or "", "done_when": done_when or "",
                "start": start or "", "due": due or ""}

    if head == "/roundtable":
        roles, rest = _extract_flag(rest, "--roles")
        rounds, rest = _extract_flag(rest, "--rounds", int)
        project, rest = _extract_flag(rest, "--project")
        topic = " ".join(rest).strip()
        if not topic:
            return {"cmd": "/roundtable",
                    "error": "用法：/roundtable <议题> [--roles a,b,c] [--rounds N] [--project X]"}
        return {"cmd": "/roundtable", "topic": topic, "roles": roles or "",
                "rounds": rounds or 0, "project": project or ""}

    if head == "/plan":
        project, rest = _extract_flag(rest, "--project")
        days, rest = _extract_flag(rest, "--days", int)
        goal = " ".join(rest).strip()
        if not goal:
            return {"cmd": "/plan",
                    "error": "用法：/plan [--project X] [--days N] <目标描述>"}
        return {"cmd": "/plan", "goal": goal, "project": project or "",
                "days": days or 5}

    if head == "/dispatch":
        limit, rest = _extract_flag(rest, "--limit", int)
        project, rest = _extract_flag(rest, "--project")
        return {"cmd": "/dispatch", "limit": limit or 5, "project": project or ""}

    return {"cmd": head, "error": "未知指令：" + head}


def allowed_ids():
    """白名单以 users 表为准；表为空时回退 env，避免迁移前把自己锁在门外。

    只允许 founder / assistant 执行指令。client 是只读客户身份，
    不能通过飞书触发派活、建任务或任何模型调用。
    """
    try:
        import db as dbm
        con = dbm.connect()
        try:
            ids = {row[0] for row in con.execute(
                "SELECT open_id FROM users WHERE role IN ('founder','assistant')")}
        finally:
            con.close()
        if ids:
            return ids
    except Exception:  # noqa: BLE001 — 库不可用时回退环境变量
        pass
    raw = os.environ.get("FEISHU_ALLOWED_OPEN_IDS", "")
    ids = {item.strip() for item in raw.split(",") if item.strip()}
    owner = os.environ.get("FEISHU_USER_OPEN_ID", "").strip()
    if owner:
        ids.add(owner)
    return ids


def is_allowed(open_id):
    return bool(open_id) and open_id in allowed_ids()


def _spawn(args, chat_id, reply_id=""):
    """慢命令脱离监听进程执行：由子进程自己回终态，监听重启不丢结果。"""
    db_path = Path(os.environ.get("AGENCY_DB") or ROOT / "agency" / "state" / "agency.db")
    logs = db_path.parent / "reply-jobs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    log = logs / ("%s-%s.log" % (stamp, args[0]))
    cmd = [sys.executable, str(ROOT / "agency.py")] + args + ["--reply-chat", chat_id]
    sender = {"run": args[1] if len(args) > 1 else "", "plan": "planner",
              "dispatch": "系统通知", "roundtable": "圆桌"}.get(args[0], "系统通知")
    cmd += ["--reply-sender", sender]
    if reply_id:
        cmd += ["--reply-id", reply_id]
    with open(log, "ab") as fh:
        subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True)
    return log


def _run(args, runner=None, timeout=SYNC_TIMEOUT, reply_chat=None, reply_id=""):
    if reply_chat and runner is None:
        _spawn(args, reply_chat, reply_id)
        return ""
    cmd = [sys.executable, str(ROOT / "agency.py")] + args
    if runner is not None:
        try:
            rc, out, err = runner(cmd)
        except Exception as exc:  # noqa: BLE001 — 子进程异常统一回给用户
            return "执行失败：" + (str(exc) or exc.__class__.__name__)
    else:
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                  timeout=timeout)
            rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired:
            return "执行超时（%d 秒）：agency.py %s" % (timeout, " ".join(args))

    # 失败时优先展示错误输出：降级日志会占满 stdout，真正原因在 stderr
    body = ((out.strip() if rc == 0 else (err.strip() or out.strip()))
            or "（无输出）")
    if len(body) > MAX_REPLY:
        body = "…（前略）\n" + body[-MAX_REPLY:]
    head = "✅ agency.py " + " ".join(args) if rc == 0 else \
        "❌ agency.py %s（退出码 %d）" % (" ".join(args), rc)
    return head + "\n" + body


def _dates(days):
    """返回 (start, due) 两个 YYYY-MM-DD 字符串。"""
    t = dt.date.today()
    return t.isoformat(), (t + dt.timedelta(days=days)).isoformat()


def handle(text, open_id, runner=None, reply_chat=None, reply_id=""):
    """返回要回给飞书的文本；非指令返回 None（由 listener 走普通回执）。

    reply_chat 非空时，慢命令改为后台执行，终态由子进程直接回给该会话。
    """
    cmd = parse(text)
    if cmd is None:
        return None
    ids = allowed_ids()
    if not ids:
        return ("未配置飞书指令白名单。把下面这行写进 .env：\n"
                "FEISHU_USER_OPEN_ID=%s" % (open_id or "<你的 open_id>"))
    if not is_allowed(open_id):
        return "无权执行指令。你的 open_id：%s" % (open_id or "未知")
    if cmd.get("error"):
        return cmd["error"] + "\n\n" + HELP

    if cmd["cmd"] == "/help":
        # 帮助是内容，但仍然受行数约束：分页发，每页不超过 8 行
        if cmd.get("verbose") or int(cmd.get("page") or 1) > 1:
            return help_page(cmd.get("page") or 1)
        return HELP
    if cmd["cmd"] == "/brief":
        return _run(["brief"], runner, BRIEF_TIMEOUT)
    if cmd["cmd"] == "/sync":
        return _run(["sync"] + (["--force"] if cmd["force"] else []), runner)

    if cmd["cmd"] == "/roundtable":
        args = ["roundtable", cmd["topic"]]
        if cmd.get("roles"):
            args += ["--roles", cmd["roles"]]
        if cmd.get("rounds"):
            args += ["--rounds", str(cmd["rounds"])]
        if cmd.get("project"):
            args += ["--project", cmd["project"]]
        return _run(args, runner, ROUNDTABLE_TIMEOUT, reply_chat, reply_id)

    if cmd["cmd"] == "/run":
        role = cmd["role"]
        if role not in dbm.ROLE:
            # 允许直接说中文角色名，界面上不出现英文标识
            role = next((r for r in dbm.ROLE if role_label(r) == role), role)
        if role not in dbm.ROLE:
            return ("角色不存在：%s\n可用：%s"
                    % (cmd["role"], "、".join(role_label(r) for r in dbm.ROLE)))
        args = ["run", role]
        if cmd["task"]:
            args += ["--task", cmd["task"]]
        if cmd["note"]:
            args += ["--note", cmd["note"]]
        return _run(args, runner, RUN_TIMEOUT, reply_chat, reply_id)

    if cmd["cmd"] == "/task":
        if cmd["owner"] not in dbm.ROLE:
            return "角色不存在：%s\n可用：%s" % (cmd["owner"], " / ".join(dbm.ROLE))
        start, due = _dates(cmd["days"])
        # 调用方已经算好的日期优先，不能再用默认 +N 天覆盖
        start = cmd.get("start") or start
        due = cmd.get("due") or due
        # 没有验收标准就不拿标题兜底：占位 + 明确提示补 --check
        done_when = ("cmd: " + cmd["check"]) if cmd.get("check") else \
            (cmd.get("done_when") or "")
        no_check = not done_when
        args = ["task", "add",
                "--title", cmd["title"],
                "--owner", cmd["owner"],
                "--start", start,
                "--due", due,
                "--done-when", done_when or "待补：验收标准"]
        if cmd["project"]:
            args += ["--project", cmd["project"]]
        reply = _run(args, runner, TASK_ADD_TIMEOUT)
        m = re.search(r"T-\d{4}", reply or "")
        if m and reply.startswith("✅"):
            text = "已建任务 %s「%s」，%s 负责，%s 截止。" % (
                m.group(0), cmd["title"], cmd["owner"], due[5:])
            if no_check:
                text += "还没填验收标准，关闭前要补。"
            return text
        if no_check:
            reply += ('\n未提供验收标准，关闭前需补 --check "<可执行命令>"。')
        return reply

    if cmd["cmd"] == "/plan":
        args = ["plan", cmd["goal"], "--days", str(cmd["days"]), "--apply"]
        if cmd["project"]:
            args += ["--project", cmd["project"]]
        return _run(args, runner, PLAN_TIMEOUT, reply_chat, reply_id)

    if cmd["cmd"] == "/dispatch":
        args = ["dispatch", "--limit", str(cmd["limit"])]
        if cmd["project"]:
            args += ["--project", cmd["project"]]
        return _run(args, runner, RUN_TIMEOUT, reply_chat, reply_id)

    return "未处理的指令：" + cmd["cmd"]
