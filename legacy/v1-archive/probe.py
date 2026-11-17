#!/usr/bin/env python3
"""probe.py — 探针能力包（P3 · T-0026）：未知应用的一次性可行性勘查。

约束（方案 5.10 / 26.x）：
  1. 默认关闭：只有 `gui probe ...` 或 `gui run --probe` 显式触发才运行。
  2. 产物只落 `agency/state/gui/probe/<probe>/`，不写台账、不建操作会话。
  3. 结论先固化到探针目录；`probe-promote` 才生成注册表草稿，
     `verified_at` 留空，人工验证后才能用于直调。
  4. 同一应用同一场景只探一次；探完应改为注册表直调。

OpenClaw agent 需要 gateway 凭据；未配置时探针不静默失败，
而是把 agent 错误写进结论，并给出规则判定（元素树统计）。
"""

import datetime as dt
import json
import os
import re
import secrets
import shutil
from pathlib import Path

import gui as guim

ROOT = Path(__file__).resolve().parent
PROBE_DIR = ROOT / "agency" / "state" / "gui" / "probe"
APPS_DIR = guim.APPS_DIR
OPENCLAW = (os.environ.get("OPENCLAW_BIN")
            or shutil.which("openclaw")
            or "openclaw")
AGENT_TIMEOUT = 60
PROBE_RE = re.compile(r"^probe-\d{8}-\d{6}-[0-9a-f]{4}$")
ACTION_ROLES = ("AXButton", "AXTextField", "AXTextArea", "AXCheckBox",
                "AXRadioButton", "AXPopUpButton", "AXMenuItem", "AXTab",
                "AXLink", "AXComboBox")
MAX_KEY_ELEMENTS = 12


class ProbeError(guim.GuiError):
    """探针可预期错误。"""


def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def new_probe_id(now=None):
    now = now or dt.datetime.now()
    return "probe-%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), secrets.token_hex(2))


def probe_dir(probe_id, base=PROBE_DIR):
    if not PROBE_RE.match(str(probe_id)):
        raise ProbeError("非法探针 id：%s" % probe_id)
    return Path(base) / probe_id


def slug(text, fallback="item"):
    out = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return out[:32] or fallback


def key_elements(cap):
    """从元素树里挑可用候选，供注册表 interactive 使用。"""
    rows, seen = [], set()
    for element in cap.get("elements") or []:
        role = element.get("ax_role") or ""
        if role not in ACTION_ROLES or not element.get("is_actionable"):
            continue
        title = str(element.get("title") or element.get("label") or "").strip()
        if (role, title) in seen:
            continue
        seen.add((role, title))
        rows.append({"role": role, "title": title or "未命名"})
        if len(rows) >= MAX_KEY_ELEMENTS:
            break
    return rows


def analyze(app, before, after):
    """规则判定：能否自动化、卡点、下一步。"""
    elements = before.get("elements") or []
    actionable = [e for e in elements if e.get("is_actionable")]
    roles = {e.get("ax_role") for e in elements}
    web = "AXWebArea" in roles
    blockers = []
    if not before.get("ok"):
        feasible, summary = "unknown", "感知失败：%s" % before.get("message")
        blockers.append("无法读取窗口元素树")
    elif not elements:
        feasible, summary = "no", "元素树为空，疑似自绘/WebView 界面"
        blockers.append("无元素可定位")
    elif before.get("is_dialog"):
        feasible, summary = "yes", "存在弹窗，需先处理弹窗或加入 dialogs 清单"
        blockers.append("弹窗：%s" % (before.get("window_title") or "未命名"))
    elif web and len(actionable) < 10:
        feasible = "browser"
        summary = "主内容为 WebView（%d 个可操作元素），建议走浏览器后端" \
            % len(actionable)
        blockers.append("App 元素树过稀，定位不可靠")
    elif not actionable:
        feasible, summary = "no", "没有可操作元素，只能截图/坐标兜底"
        blockers.append("无可操作元素")
    else:
        feasible = "yes"
        summary = "可直调：%d/%d 个元素可操作" % (len(actionable), len(elements))
    if after.get("is_dialog") and not before.get("is_dialog"):
        blockers.append("探针期间弹出新窗口：%s"
                        % (after.get("window_title") or "未命名"))
    return {"feasible": feasible, "summary": summary, "blockers": blockers,
            "key_elements": key_elements(before),
            "counts": {"elements": len(elements),
                       "actionable": len(actionable),
                       "web_area": web}}


def build_prompt(app, goal, before):
    roles = {}
    for element in before.get("elements") or []:
        role = element.get("ax_role") or "?"
        roles[role] = roles.get(role, 0) + 1
    top = sorted(roles.items(), key=lambda kv: -kv[1])[:10]
    return ("你在做 macOS 应用自动化可行性勘查。应用：%s（%s）。目标：%s。"
            "窗口：%s；元素 %d 个；主要角色：%s。"
            "请用 200 字内回答：能否自动化、关键元素、卡点、建议走 App 还是浏览器。"
            % (app.get("name", ""), app.get("bundle_id", ""), goal or "—",
               before.get("window_title") or "—",
               len(before.get("elements") or []),
               "、".join("%s×%d" % kv for kv in top) or "无"))


def ask_agent(prompt, run=guim.run_cmd, timeout=AGENT_TIMEOUT):
    """调用 OpenClaw agent；缺凭据时把错误原样带回，不抛异常。"""
    argv = [OPENCLAW, "agent", "--json", "-m", prompt]
    started = dt.datetime.now()
    code, out, err = run(argv, timeout)
    ms = int((dt.datetime.now() - started).total_seconds() * 1000)
    try:
        data = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        data = {}
    text = ""
    for key in ("text", "result", "message", "reply"):
        if isinstance(data.get(key), str) and data[key].strip():
            text = data[key].strip()
            break
    ok = code == 0 and data.get("ok") is not False and bool(text)
    message = ((data.get("error") or {}).get("message")
               or err.strip() or ("退出码 %s" % code))
    return {"ok": ok, "command": argv, "ms": ms, "text": text,
            "error": "" if ok else message}


def run(app, goal="", max_steps=10, base=PROBE_DIR, run=guim.run_cmd,
        capture=guim.capture, agent_fn=ask_agent, use_agent=True, now=None,
        probe_id=None):
    """执行一次探针：前截图 → 元素树 → agent（可选）→ 后截图 → 结论。"""
    if isinstance(app, str):
        app = guim.load_app(app)
    if max_steps < 2:
        raise ProbeError("max_steps 至少为 2（前/后各一次感知）")
    pid = probe_id or new_probe_id(now)
    root = probe_dir(pid, base)
    if root.exists():
        raise ProbeError("探针已存在：%s" % pid)
    root.mkdir(parents=True)
    audit = []
    steps = 0

    before = capture(app, root / "before.png", run=run)
    steps += 1
    audit.append({"step": steps, "action": "see", "ok": before["ok"],
                  "command": before["argv"], "ms": before["ms"]})
    if not before["ok"] and steps >= max_steps:
        after = before
    else:
        after = None

    agent = {"ok": False, "text": "", "error": "未启用", "command": [], "ms": 0}
    if use_agent and before["ok"] and steps < max_steps - 1:
        agent = agent_fn(build_prompt(app, goal, before), run=run)
        steps += 1
        audit.append({"step": steps, "action": "agent", "ok": agent["ok"],
                      "command": agent["command"], "ms": agent["ms"]})
    elif use_agent and before["ok"]:
        agent["error"] = "步数不足，跳过 agent"

    if after is None:
        after = capture(app, root / "after.png", run=run)
        steps += 1
        audit.append({"step": steps, "action": "see", "ok": after["ok"],
                      "command": after["argv"], "ms": after["ms"]})

    conclusion = analyze(app, before, after)
    result = {
        "version": 1,
        "probe": pid,
        "at": now_iso(),
        "app": {"id": app["id"], "name": app["name"],
                "bundle_id": app["bundle_id"]},
        "goal": goal,
        "max_steps": int(max_steps),
        "steps": steps,
        "before": {k: before.get(k) for k in
                   ("ok", "window_title", "is_dialog", "message", "ms")},
        "after": {k: after.get(k) for k in
                  ("ok", "window_title", "is_dialog", "message", "ms")},
        "conclusion": conclusion,
        "agent": {"ok": agent["ok"], "text": agent["text"],
                  "error": agent["error"], "command": agent["command"]},
        "audit": audit,
        "artifacts": ["before.png", "after.png", "report.md", "result.json"],
    }
    guim._atomic_write(root / "result.json",
                       json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    guim._atomic_write(root / "report.md", render_report(result))
    return result


def render_report(result):
    app = result["app"]
    c = result["conclusion"]
    counts = c["counts"]
    lines = [
        "# 探针报告 %s" % result["probe"], "",
        "- 应用：%s（%s）" % (app["name"], app["bundle_id"]),
        "- 目标：%s" % (result["goal"] or "—"),
        "- 时间：%s｜步数：%d/%d" % (result["at"], result["steps"],
                                    result["max_steps"]),
        "", "## 结论", "",
        "- 可行性：%s" % c["feasible"],
        "- 判定：%s" % c["summary"],
        "- 元素：%d 个，可操作 %d 个%s"
        % (counts["elements"], counts["actionable"],
           "，含 WebView" if counts["web_area"] else ""),
        "", "## 卡点", "",
    ]
    lines += ["- %s" % b for b in c["blockers"]] or ["- 未发现"]
    lines += ["", "## 关键元素", ""]
    lines += ["- %s：%s" % (e["role"], e["title"]) for e in c["key_elements"]]
    lines += ["", "## 助手意见", "",
              result["agent"]["text"] or "（不可用：%s）" % result["agent"]["error"],
              "", "## 下一步", ""]
    if c["feasible"] == "yes":
        lines.append("- `python3 agency.py gui probe-promote %s` 生成注册表草稿，"
                     "人工验证后填 `verified_at`。" % result["probe"])
    elif c["feasible"] == "browser":
        lines.append("- 转浏览器后端：`python3 agency.py browser open <url>`。")
    else:
        lines.append("- 放弃直调，改截图/坐标兜底或人工处理。")
    lines.append("- 同一场景不再重复探针。")
    return "\n".join(lines) + "\n"


def list_probes(base=PROBE_DIR, limit=20):
    root = Path(base)
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir() or not PROBE_RE.match(path.name):
            continue
        try:
            data = json.loads((path / "result.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append({"probe": data["probe"], "at": data["at"],
                     "app": data["app"]["name"],
                     "feasible": data["conclusion"]["feasible"],
                     "summary": data["conclusion"]["summary"],
                     "promoted": data.get("promoted", "")})
        if len(rows) >= limit:
            break
    return rows


def load_probe(probe_id, base=PROBE_DIR):
    path = probe_dir(probe_id, base) / "result.json"
    if not path.exists():
        raise ProbeError("探针不存在：%s" % probe_id)
    return json.loads(path.read_text(encoding="utf-8"))


def promote(probe_id, base=PROBE_DIR, apps_dir=APPS_DIR):
    """把探针结论固化为注册表草稿（verified_at 留空，等人验证）。"""
    result = load_probe(probe_id, base)
    if result.get("promoted"):
        raise ProbeError("探针已固化：%s" % result["promoted"])
    conclusion = result["conclusion"]
    if conclusion["feasible"] != "yes":
        raise ProbeError("结论为 %s，不生成直调注册表：%s"
                         % (conclusion["feasible"], conclusion["summary"]))
    app_id = result["app"]["id"]
    path = Path(apps_dir) / ("%s.json" % app_id)
    if path.exists():
        raise ProbeError("注册表已存在：%s.json；同一场景不重复建档" % app_id)
    interactive, seen = [], set()
    for element in conclusion["key_elements"]:
        key = slug(element["title"], "item%d" % (len(interactive) + 1))
        if key in seen:
            continue
        seen.add(key)
        interactive.append({"key": key, "role": element["role"],
                            "title": element["title"]})
    draft = {
        "id": app_id,
        "name": result["app"]["name"],
        "bundle_id": result["app"]["bundle_id"],
        "launch": "peekaboo app launch --bundle-id %s --foreground"
                  % result["app"]["bundle_id"],
        "window_match": result["app"]["name"],
        "interactive": interactive,
        "dialogs": conclusion["blockers"],
        "dangerous": [],
        "verified_at": "",
    }
    guim.validate_app(draft, path.name)
    guim._atomic_write(path, json.dumps(draft, ensure_ascii=False,
                                        indent=2) + "\n")
    result["promoted"] = str(path)
    guim._atomic_write(probe_dir(probe_id, base) / "result.json",
                       json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return {"probe": probe_id, "path": str(path), "app": draft}
