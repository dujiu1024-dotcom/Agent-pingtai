#!/usr/bin/env python3
"""agency.py — 龍奕FDE工作室 · Agent 公司运行底座

单文件、纯标准库、状态全部落盘，可整包迁移。

用法：
  python3 agency.py init                       初始化
  python3 agency.py doctor                     自检
  python3 agency.py task add ...               建任务
  python3 agency.py task list                  看任务
  python3 agency.py task close <id> --evidence "..."   关闭任务
  python3 agency.py check                      检查到期/临期/逾期
  python3 agency.py brief                      一页态势
  python3 agency.py run <role> --task <id>     调用某个 Agent
  python3 agency.py project init <code> <name> 建项目目录
  python3 agency.py export <file.tar.gz>       打包迁移
  python3 agency.py import <file.tar.gz> --into <dir>  恢复
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "agency" / "config.json"

DEFAULT_CONFIG = {
    "version": 1,
    "paths": {
        "agents": "agents",
        "company": "company",
        "projects": "clients",
        "state": "agency/state",
        "outputs": "agency/outputs",
    },
    "llm": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
}


def load_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_config():
    if not CONFIG_PATH.exists():
        return DEFAULT_CONFIG
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in cfg.items():
        if isinstance(v, dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


load_env()
CFG = load_config()
STATE_DIR = ROOT / CFG["paths"]["state"]
TASKS_PATH = STATE_DIR / "tasks.json"
EVENTS_PATH = STATE_DIR / "events.jsonl"
GATES_PATH = STATE_DIR / "gates.json"
OUTPUT_DIR = ROOT / CFG["paths"]["outputs"]


def today():
    override = os.environ.get("AGENCY_TODAY")
    return dt.date.fromisoformat(override) if override else dt.date.today()


def read_tasks():
    if not TASKS_PATH.exists():
        return []
    with open(TASKS_PATH, encoding="utf-8") as f:
        return json.load(f)


def write_tasks(tasks):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TASKS_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    tmp.replace(TASKS_PATH)


def read_gates():
    if not GATES_PATH.exists():
        return {}
    with open(GATES_PATH, encoding="utf-8") as f:
        return json.load(f)


def write_gates(gates):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(GATES_PATH, "w", encoding="utf-8") as f:
        json.dump(gates, f, ensure_ascii=False, indent=2)


def gate_ok(project):
    g = read_gates().get(project)
    return bool(g and g.get("confirmed"))


def log_event(kind, task_id, detail=""):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "type": kind,
        "id": task_id,
        "detail": detail,
    }
    with open(EVENTS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def parse_date(s):
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        sys.exit(f"日期格式错误：{s}，应为 YYYY-MM-DD")


def next_id(tasks):
    nums = []
    for t in tasks:
        try:
            nums.append(int(t["id"].split("-")[1]))
        except (KeyError, IndexError, ValueError):
            pass
    return "T-%04d" % (max(nums) + 1 if nums else 1)


def task_state(t, ref):
    if t.get("status") == "closed":
        return "已关闭"
    if not all(t.get(k) for k in ("start", "due", "owner", "done_when")):
        return "要素不全"
    start = dt.date.fromisoformat(t["start"])
    due = dt.date.fromisoformat(t["due"])
    if ref < start - dt.timedelta(days=1):
        return "未开始"
    if ref == start - dt.timedelta(days=1):
        return "明日开始"
    if ref > due:
        return "逾期 %d 天" % (ref - due).days
    if (due - ref).days <= 1:
        return "临期"
    return "进行中"


def cmd_init(_):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        print("创建配置：", CONFIG_PATH.relative_to(ROOT))
    if not TASKS_PATH.exists():
        write_tasks([])
        print("创建台账：", TASKS_PATH.relative_to(ROOT))
    EVENTS_PATH.touch(exist_ok=True)
    print("初始化完成。下一步：python3 agency.py doctor")


def cmd_doctor(_):
    ok = True
    for key, rel in CFG["paths"].items():
        p = ROOT / rel
        flag = "OK " if p.exists() else "缺失"
        if not p.exists():
            ok = False
        print(f"[{flag}] {key}: {rel}")

    roles_dir = ROOT / CFG["paths"]["agents"]
    roles = sorted(p.parent.name for p in roles_dir.glob("*/ROLE.md")) if roles_dir.exists() else []
    print(f"[{'OK ' if roles else '缺失'}] 角色数: {len(roles)} -> {', '.join(roles)}")

    gates = read_gates()
    confirmed = [k for k, v in gates.items() if v.get("confirmed")]
    print(f"[提示] 开工闸门: 已确认 {len(confirmed)} 个项目"
          + (f" -> {', '.join(confirmed)}" if confirmed else "（未确认前不得创建 code 类任务）"))

    try:
        tasks = read_tasks()
        print(f"[OK ] 台账: {len(tasks)} 条任务")
    except Exception as e:  # noqa: BLE001
        ok = False
        print("[错误] 台账解析失败:", e)

    key = os.environ.get("OPENAI_API_KEY")
    print(f"[{'OK ' if key else '提示'}] LLM 密钥: {'已配置' if key else '未配置（离线模式可用，run 不可用）'}")

    con = ROOT / CFG["paths"]["company"] / "公司宪法.md"
    print(f"[{'OK ' if con.exists() else '缺失'}] 公司宪法")
    print("自检结果:", "通过" if ok else "有问题")
    sys.exit(0 if ok else 1)


def cmd_task_add(args):
    tasks = read_tasks()
    start, due = parse_date(args.start), parse_date(args.due)
    if start > due:
        sys.exit("开始日期不能晚于截止日期")
    missing = [n for n, v in [("标题", args.title), ("owner", args.owner),
                              ("完成标准", args.done_when)] if not v]
    if missing:
        sys.exit("要素不全，缺少：" + "、".join(missing))
    if args.kind == "code" and not gate_ok(args.project):
        sys.exit(
            f"开工闸门未通过：{args.project} 尚无确认单。\n"
            "宪法第七条：不沟通清楚，不得开工写代码。\n"
            f"先完成沟通，再执行：python3 agency.py gate confirm {args.project} --by <确认人> --scope \"<范围>\""
        )
    t = {
        "id": next_id(tasks),
        "title": args.title,
        "project": args.project,
        "kind": args.kind,
        "owner": args.owner,
        "start": args.start,
        "due": args.due,
        "done_when": args.done_when,
        "hours": args.hours,
        "dep": args.dep,
        "deliverable": args.deliverable,
        "milestone": args.milestone,
        "status": "open",
        "evidence": "",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    tasks.append(t)
    write_tasks(tasks)
    log_event("task_created", t["id"], t["title"])
    print(f"已建 {t['id']}｜{t['title']}｜{t['owner']}｜{t['start']} → {t['due']}")


def cmd_task_list(args):
    tasks = read_tasks()
    if args.project:
        tasks = [t for t in tasks if t.get("project") == args.project]
    if not tasks:
        print("暂无任务")
        return
    ref = today()
    for t in tasks:
        print(f"{t['id']} [{task_state(t, ref)}] {t['start']}→{t['due']} @{t['owner']} {t['title']}")


def cmd_task_close(args):
    tasks = read_tasks()
    for t in tasks:
        if t["id"] == args.id:
            if not args.evidence.strip():
                sys.exit("没有证据不能关闭任务")
            t["status"] = "closed"
            t["evidence"] = args.evidence
            t["closed_at"] = dt.datetime.now().isoformat(timespec="seconds")
            write_tasks(tasks)
            log_event("task_closed", t["id"], args.evidence)
            print(f"已关闭 {t['id']}，证据：{args.evidence}")
            return
    sys.exit("任务不存在：" + args.id)


WBS_HEADERS = ["ID", "项目", "任务", "Owner", "开始", "截止", "工时(h)",
               "依赖", "产出物", "里程碑", "完成标准", "状态", "证据"]


def cmd_wbs(args):
    """导出 WBS 视图（CSV）。tasks.json 是唯一事实源，本命令只读不写。"""
    tasks = read_tasks()
    if args.project:
        tasks = [t for t in tasks if t.get("project") == args.project]
    if not args.all:
        tasks = [t for t in tasks if t.get("status") != "closed"]
    ref = today()
    out = Path(args.file).expanduser().resolve() if args.file else \
        OUTPUT_DIR / f"wbs-{args.project or 'all'}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(WBS_HEADERS)
        for t in tasks:
            w.writerow([t["id"], t.get("project", ""), t["title"], t["owner"],
                        t["start"], t["due"], t.get("hours", ""),
                        t.get("dep", ""), t.get("deliverable", ""),
                        t.get("milestone", ""), t["done_when"],
                        task_state(t, ref), t.get("evidence", "")])
    print(f"已导出 WBS：{out}")
    print(f"共 {len(tasks)} 条｜UTF-8 BOM｜可直接导入飞书多维表格或 Excel")


def cmd_check(_):
    tasks = read_tasks()
    ref = today()
    alerts = []
    for t in tasks:
        st = task_state(t, ref)
        if st in ("要素不全", "明日开始", "临期", "逾期") or st.startswith("逾期"):
            alerts.append((t, st))
    print(f"检查日期：{ref}　任务总数：{len(tasks)}")
    if not alerts:
        print("没有需要提醒的事项")
        return
    for t, st in sorted(alerts, key=lambda x: x[0]["due"]):
        print(f"⚠ {t['id']} [{st}] {t['title']}｜{t['owner']}｜{t['start']}→{t['due']}")
    log_event("check", "", f"{len(alerts)} alerts")


def cmd_brief(_):
    tasks = read_tasks()
    ref = today()
    open_tasks = [t for t in tasks if t.get("status") != "closed"]
    urgent = [t for t in open_tasks if task_state(t, ref) in ("临期", "要素不全")
              or task_state(t, ref).startswith("逾期")]
    owners = {}
    for t in open_tasks:
        owners[t["owner"]] = owners.get(t["owner"], 0) + 1
    print("【态势】", ref)
    print(f"未关 {len(open_tasks)} / 全部 {len(tasks)}；需处理 {len(urgent)}")
    if urgent:
        print("【优先处理】")
        for t in urgent[:5]:
            print(f"  - {t['id']} {t['title']}（{task_state(t, ref)}，{t['due']}）")
    print("【负荷】", "，".join(f"{k}:{v}" for k, v in sorted(owners.items())) or "无")


def build_prompt(role, role_text, task, tasks):
    ref = today()
    open_lines = [f"- {t['id']} {t['title']}（{t['owner']}，{t['due']}，{task_state(t, ref)}）"
                  for t in tasks if t.get("status") != "closed"][:20]
    task_text = json.dumps(task, ensure_ascii=False, indent=2) if task else "无指定任务"
    return f"""你是以下角色的执行体，必须严格遵守角色定义与公司宪法。

【角色定义】
{role_text}

【公司宪法要点】
1. 禁止臆造：事实分 S1 客户原话/签字、S2 客户文档、S3 公开资料、S4 推断。
2. S4 必须标注"假设，待核实"，不得写成结论。
3. 没有来源的数字不得写入交付物。
4. 对外承诺价格、工期、效果，必须人工确认。
5. 新获取的信息必须立即写入飞书对应节点；敏感信息单独写入受限节点；未落盘的视为未获取。

【当前任务】
{task_text}

【未关闭任务】
{chr(10).join(open_lines) if open_lines else "无"}

【输出要求】
直接给出可执行的产出，不要复述角色定义、不要复述本提示词、不要输出思考过程。
凡属推断的内容，逐条标注 [假设·S4]。
禁止编造输入中没有的日期、人名、行程、会议、决策记录；缺数据就写"无数据"。
"""


def call_llm(system_prompt):
    import time
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("未配置 OPENAI_API_KEY。可先离线使用 task/check/brief；run 需要密钥。")
    base = os.environ.get("OPENAI_BASE_URL", CFG["llm"]["base_url"]).rstrip("/")
    model = os.environ.get("AGENCY_MODEL", CFG["llm"]["model"])
    last = ""
    for _ in range(3):
        for effort in ("none", "low"):
            body = {
                "model": model,
                "messages": [{"role": "system", "content": system_prompt}],
                "temperature": 0.2,
                "max_tokens": 1500,
            }
            if effort:
                body["reasoning_effort"] = effort
            req = urllib.request.Request(
                base + "/chat/completions", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")
                if e.code == 400 and "ReasoningEffort" in detail:
                    continue
                if e.code == 429:
                    last = "限流：" + detail[:120]
                    time.sleep(8)
                    break
                sys.exit("LLM 调用失败：%s %s" % (e.code, detail[:300]))
            except urllib.error.URLError as e:
                sys.exit("LLM 连接失败：" + str(e.reason))
            message = data["choices"][0]["message"]
            return message.get("content") or message.get("reasoning_content") or ""
    sys.exit("LLM 调用失败：" + (last or "重试耗尽"))


def cmd_run(args):
    roles_dir = ROOT / CFG["paths"]["agents"]
    role_path = roles_dir / args.role / "ROLE.md"
    if not role_path.exists():
        avail = ", ".join(sorted(p.parent.name for p in roles_dir.glob("*/ROLE.md")))
        sys.exit(f"角色不存在：{args.role}。可用：{avail}")
    tasks = read_tasks()
    task = next((t for t in tasks if t["id"] == args.task), None) if args.task else None
    if args.task and not task:
        sys.exit("任务不存在：" + args.task)
    role_text = role_path.read_text(encoding="utf-8")
    prompt = build_prompt(args.role, role_text, task, tasks)
    out = call_llm(prompt)
    flags = audit_output(out, prompt)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{args.role}-{args.task or 'adhoc'}.md"
    path = OUTPUT_DIR / name
    path.write_text(out, encoding="utf-8")
    log_event("agent_run", args.task or "", f"{args.role} -> {name}")
    print(out)
    if flags:
        print("\n---\n审计：发现 " + str(len(flags)) + " 处疑似无来源信息，需人工核实：")
        for f in flags:
            print("  - " + f)
        log_event("audit_flag", args.task or "", "; ".join(flags))
    else:
        print("\n---\n审计：未发现无来源日期。")
    print("已保存：" + str(path.relative_to(ROOT)))


def audit_output(text, prompt):
    """粗检：输出中的日期必须在输入事实中出现过（按月-日比较，忽略年份写法）。"""
    import re
    pat = re.compile(r"20\d{2}-\d{1,2}-\d{1,2}|(?<![\d-])\d{1,2}-\d{1,2}(?![\d-])|(?<![\d-])\d{1,2}月\d{1,2}日")

    def md(s):
        nums = re.findall(r"\d+", s)
        return (int(nums[-2]), int(nums[-1]))

    known = {md(d) for d in pat.findall(prompt)}
    flags = []
    for d in sorted({md(x) for x in pat.findall(text)} - known):
        flags.append("日期 " + str(d[0]) + "-" + str(d[1]) + " 未出现在输入事实中")
    return flags


TEMPLATES = {
    "01-需求说明.md": "# 需求说明｜{name}\n\n> 事实状态：待采集。\n\n## 事实台账\n\n| # | 陈述 | 来源 | 级别 |\n|---|---|---|---|\n",
    "02-方案对比.md": "# 方案对比｜{name}\n\n## 方案\n\n| | A | B | C |\n|---|---|---|---|\n",
    "03-方案确认单.md": "# 方案确认单｜{name}\n\n- 状态：待确认（未签署不排期）\n",
    "04-项目计划.md": "# 项目计划｜{name}\n\n## WBS\n\n| 任务 | 产出 | 开始 | 截止 | owner |\n|---|---|---|---|---|\n",
    "05-验收清单.md": "# 验收清单｜{name}\n\n| 项 | 标准 | 证据 | 结论 |\n|---|---|---|---|\n",
}


def cmd_gate(args):
    gates = read_gates()
    if args.gate_cmd == "confirm":
        gates[args.project] = {
            "confirmed": True,
            "by": args.by,
            "scope": args.scope,
            "at": dt.datetime.now().isoformat(timespec="seconds"),
        }
        write_gates(gates)
        log_event("gate_confirmed", args.project, args.scope)
        print(f"闸门已开：{args.project}｜确认人：{args.by}｜范围：{args.scope}")
    elif args.gate_cmd == "revoke":
        if args.project in gates:
            gates[args.project]["confirmed"] = False
            gates[args.project]["revoked_at"] = dt.datetime.now().isoformat(timespec="seconds")
            write_gates(gates)
            log_event("gate_revoked", args.project, "")
        print(f"闸门已关：{args.project}（暂停创建 code 类任务）")
    elif args.gate_cmd == "check":
        g = gates.get(args.project)
        if g and g.get("confirmed"):
            print(f"通过：{args.project} 已于 {g['at']} 由 {g['by']} 确认")
            print("范围：" + g.get("scope", ""))
            return
        print(f"未通过：{args.project} 无有效开工确认单。宪法第七条禁止开工写代码。")
        sys.exit(1)
    elif args.gate_cmd == "list":
        if not gates:
            print("暂无闸门记录")
            return
        for k, v in sorted(gates.items()):
            state = "已确认" if v.get("confirmed") else "已关闭"
            print(f"{k} [{state}] {v.get('by', '')} {v.get('at', '')} {v.get('scope', '')}")


def cmd_project_init(args):
    base = ROOT / CFG["paths"]["projects"] / f"{args.code}-{args.name}"
    base.mkdir(parents=True, exist_ok=True)
    for fname, tpl in TEMPLATES.items():
        p = base / fname
        if not p.exists():
            p.write_text(tpl.format(name=args.name), encoding="utf-8")
            print("创建", p.relative_to(ROOT))
    print("项目目录：", base.relative_to(ROOT))


def cmd_export(args):
    include = ["agents", "company", CFG["paths"]["projects"],
               CFG["paths"]["state"], CFG["paths"]["outputs"],
               "agency/config.json", "agency.py"]
    skip_names = {".env", ".git", "__pycache__", "node_modules", ".DS_Store"}
    out = Path(args.file).expanduser().resolve()
    with tarfile.open(out, "w:gz") as tar:
        for rel in include:
            src = ROOT / rel
            if not src.exists():
                continue
            if src.is_file():
                tar.add(src, arcname=src.relative_to(ROOT))
                continue
            for dirpath, dirnames, filenames in os.walk(src):
                dirnames[:] = [d for d in dirnames if d not in skip_names]
                for fn in filenames:
                    if fn in skip_names:
                        continue
                    p = Path(dirpath) / fn
                    tar.add(p, arcname=p.relative_to(ROOT))
    print("已导出：", out)
    print("迁移：在新机器解包后执行 python3 agency.py doctor；密钥用环境变量，不在包内。")


def cmd_import(args):
    target = Path(args.into).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.file, "r:gz") as tar:
        for m in tar.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts:
                sys.exit("包内存在不安全路径：" + m.name)
        tar.extractall(target)
    print("已恢复到：", target)
    print("下一步：在该目录执行 python3 agency.py doctor")


def main():
    ap = argparse.ArgumentParser(prog="agency")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)

    p = sub.add_parser("task")
    tsub = p.add_subparsers(dest="task_cmd", required=True)
    a = tsub.add_parser("add")
    a.add_argument("--title", required=True)
    a.add_argument("--project", default="")
    a.add_argument("--owner", required=True)
    a.add_argument("--start", required=True)
    a.add_argument("--due", required=True)
    a.add_argument("--done-when", dest="done_when", required=True)
    a.add_argument("--kind", choices=["general", "code"], default="general")
    a.add_argument("--hours", type=float, default=None, help="预估工时")
    a.add_argument("--dep", default="", help="依赖任务 ID，逗号分隔")
    a.add_argument("--deliverable", default="", help="产出物")
    a.add_argument("--milestone", default="", help="所属里程碑")
    a.set_defaults(func=cmd_task_add)
    l = tsub.add_parser("list")
    l.add_argument("--project")
    l.set_defaults(func=cmd_task_list)
    c = tsub.add_parser("close")
    c.add_argument("id")
    c.add_argument("--evidence", required=True)
    c.set_defaults(func=cmd_task_close)

    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("brief").set_defaults(func=cmd_brief)

    w = sub.add_parser("wbs", help="导出 WBS 视图 CSV（只读）")
    w.add_argument("--project")
    w.add_argument("--file")
    w.add_argument("--all", action="store_true", help="包含已关闭任务")
    w.set_defaults(func=cmd_wbs)

    r = sub.add_parser("run")
    r.add_argument("role")
    r.add_argument("--task")
    r.set_defaults(func=cmd_run)

    g = sub.add_parser("gate")
    gsub = g.add_subparsers(dest="gate_cmd", required=True)
    gc = gsub.add_parser("confirm")
    gc.add_argument("project")
    gc.add_argument("--by", required=True)
    gc.add_argument("--scope", required=True)
    gc.set_defaults(func=cmd_gate)
    gk = gsub.add_parser("check")
    gk.add_argument("project")
    gk.set_defaults(func=cmd_gate)
    gr = gsub.add_parser("revoke")
    gr.add_argument("project")
    gr.set_defaults(func=cmd_gate)
    gl = gsub.add_parser("list")
    gl.set_defaults(func=cmd_gate)

    pj = sub.add_parser("project")
    psub = pj.add_subparsers(dest="project_cmd", required=True)
    pi = psub.add_parser("init")
    pi.add_argument("code")
    pi.add_argument("name")
    pi.set_defaults(func=cmd_project_init)

    e = sub.add_parser("export")
    e.add_argument("file")
    e.set_defaults(func=cmd_export)

    i = sub.add_parser("import")
    i.add_argument("file")
    i.add_argument("--into", required=True)
    i.set_defaults(func=cmd_import)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
