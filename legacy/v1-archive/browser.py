#!/usr/bin/env python3
"""browser.py — 浏览器后端（P3 · T-0025）：OpenClaw browser 适配器。

约束：
  1. 只通过 `openclaw browser` 子命令操作，不引入 WebSocket / Playwright 依赖。
  2. 上层只依赖 observe / act / assert（方案 5.9）；动作原语是代码枚举。
  3. 命令与输出全部落本平台会话（kind=browser），token 写盘前脱敏。
  4. 只允许 http/https；默认无头运行，不抢焦点（方案 5.9 / 25.2）。
  5. 产物只回收进会话目录，OpenClaw 媒体目录仅作中转（25.3）。

环境变量：
  OPENCLAW_BIN    openclaw 可执行文件（默认 PATH 查找）
  OPENCLAW_PORT   gateway 端口（默认 18789）
  OPENCLAW_TOKEN  gateway token（缺失直接报错，不静默降级）

实测约定（OpenClaw 2026.10.7）：
  - 每个 ref 只在下一次 snapshot 之前有效，动作前必须重新 snapshot；
  - 点击触发导航时命令常报 `Unknown ref`，但页面已跳转，按 URL 变化判定成功；
  - `open` 返回 `suggestedTargetId`（如 t7），后续命令统一带 `--target-id`。
"""

import datetime as dt
import json
import os
import re
import shutil
import time
from pathlib import Path

import gui as guim

ROOT = Path(__file__).resolve().parent
OPENCLAW = (os.environ.get("OPENCLAW_BIN")
            or shutil.which("openclaw")
            or "openclaw")
PORT = os.environ.get("OPENCLAW_PORT") or "18789"
TOKEN = os.environ.get("OPENCLAW_TOKEN") or ""

STEP_TIMEOUT = 45
SESSION_TIMEOUT = 600
MAX_VALIDATE_FAILS = 2
NAVIGATION_RACE = "Unknown ref"
ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
URL_RE = re.compile(r"^https?://", re.I)

# 动作原语是代码枚举，不可通过配置新增（方案 5.13）。
BROWSER_ACTIONS = ("open", "navigate", "snapshot", "click", "type", "fill",
                   "upload", "screenshot", "wait", "assert", "close",
                   "select", "press")
WRITE_ACTIONS = ("click", "type", "fill", "upload", "select", "press")
PLAN_KEYS = {"url", "goal", "headless", "steps"}


class BrowserError(guim.GuiError):
    """浏览器后端可预期错误：调用方直接提示，不打印堆栈。"""


def gateway_args():
    if not TOKEN:
        raise BrowserError("未配置 OPENCLAW_TOKEN；先启动 gateway："
                           "openclaw gateway run --auth token --token <token>")
    return ["--port", PORT, "--token", TOKEN]


def mask_argv(argv):
    """写盘前脱敏：紧跟 --token 的值替换为 ***。"""
    out = []
    for i, item in enumerate(argv):
        if i > 0 and argv[i - 1] == "--token" and item != "--token":
            out.append("***")
        else:
            out.append(item)
    return out


def call(args, run=guim.run_cmd, timeout=STEP_TIMEOUT):
    """执行一条 openclaw browser 命令，统一返回命令、耗时与解析结果。"""
    argv = [OPENCLAW, "browser"] + [str(a) for a in args] + ["--json"] \
        + gateway_args()
    started = dt.datetime.now()
    code, out, err = run(argv, timeout)
    ms = int((dt.datetime.now() - started).total_seconds() * 1000)
    try:
        data = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        data = {}
    ok = code == 0 and data.get("ok") is not False
    message = ((data.get("error") or {}).get("message")
               or err.strip() or ("退出码 %s" % code))
    return {"ok": ok, "argv": mask_argv(argv), "code": code, "ms": ms,
            "data": data, "message": "" if ok else message}


def status(run=guim.run_cmd):
    return call(["status"], run)


def start(headless=True, run=guim.run_cmd):
    args = ["start"] + (["--headless"] if headless else [])
    return call(args, run, timeout=90)


def stop(run=guim.run_cmd):
    return call(["stop"], run)


def tabs(run=guim.run_cmd):
    res = call(["tabs"], run)
    if res["ok"]:
        res["tabs"] = res["data"].get("tabs") or []
    return res


def snapshot(tab="", run=guim.run_cmd):
    """只读感知：抓 aria/ai 快照，返回可定位的 refs。"""
    args = ["snapshot"] + (["--target-id", tab] if tab else [])
    res = call(args, run)
    data = res["data"] if res["ok"] else {}
    return {
        "ok": res["ok"], "argv": res["argv"], "ms": res["ms"],
        "url": data.get("url") or "",
        "target": data.get("targetId") or tab,
        "text": data.get("snapshot") or "",
        "refs": data.get("refs") or {},
        "data": data,
        "message": res["message"],
    }


def open_url(url, run=guim.run_cmd):
    check_url(url)
    res = call(["open", url], run)
    data = res["data"]
    res["url"] = data.get("url") or url
    res["tab"] = (data.get("suggestedTargetId") or data.get("tabId")
                  or data.get("targetId") or "")
    return res


def check_url(url):
    if not URL_RE.match(str(url or "")):
        raise BrowserError("URL 只允许 http/https：%s" % url)


# ---------------------------------------------------------------- 选择器

def normalize_selector(selector):
    """选择器优先级：ref > role+name > role+index。"""
    if not isinstance(selector, dict) or not selector:
        raise BrowserError("selector 需为非空对象")
    unknown = set(selector) - {"ref", "role", "name", "index"}
    if unknown:
        raise BrowserError("selector 含未知键 %s" % ", ".join(sorted(unknown)))
    if "ref" in selector:
        ref = str(selector["ref"]).strip()
        if not ref:
            raise BrowserError("selector.ref 不能为空")
        return {"kind": "ref", "ref": ref}
    if "role" in selector:
        role = str(selector["role"]).strip()
        if not ROLE_RE.match(role):
            raise BrowserError("selector.role 需为小写角色，如 textbox")
        if str(selector.get("name", "")).strip():
            return {"kind": "role_name", "role": role,
                    "name": str(selector["name"])}
        if "index" in selector:
            if not isinstance(selector["index"], int) or selector["index"] < 0:
                raise BrowserError("selector.index 需为非负整数")
            return {"kind": "role_index", "role": role,
                    "index": selector["index"]}
        raise BrowserError("role 选择器需带 name 或 index")
    raise BrowserError("selector 需为 ref / role+name / role+index 之一")


def resolve_ref(refs, selector):
    """在最近一次快照里定位 ref；name 先精确后包含。返回 (ref, 说明)。"""
    if selector["kind"] == "ref":
        if selector["ref"] in refs:
            return selector["ref"], ""
        return None, "快照中不存在 ref=%s" % selector["ref"]
    role = selector["role"]
    items = [(k, v) for k, v in refs.items()
             if isinstance(v, dict) and v.get("role") == role]
    if selector["kind"] == "role_index":
        if selector["index"] < len(items):
            return items[selector["index"]][0], ""
        return None, "role=%s 只有 %d 个，index=%d 越界" % (
            role, len(items), selector["index"])
    name = selector["name"]
    exact = [k for k, v in items if str(v.get("name") or "") == name]
    if exact:
        return exact[0], ""
    fuzzy = [k for k, v in items if name in str(v.get("name") or "")]
    if fuzzy:
        return fuzzy[0], ""
    return None, "role=%s 未找到 name=%s" % (role, name)


# ---------------------------------------------------------------- 计划校验

def validate_plan(plan):
    """校验浏览器计划：未知动作/未知键/非法 URL 一律拒绝。"""
    if not isinstance(plan, dict):
        raise BrowserError("计划需为 JSON 对象")
    unknown = sorted(set(plan) - PLAN_KEYS)
    if unknown:
        raise BrowserError("计划含未知键 %s" % ", ".join(unknown))
    if plan.get("url"):
        check_url(plan["url"])
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise BrowserError("计划 steps 需为非空数组")
    for i, step in enumerate(steps, 1):
        check_step(step, i)
    return [dict(step) for step in steps]


STEP_KEYS = {
    "open": {"action", "url"},
    "navigate": {"action", "url"},
    "snapshot": {"action"},
    "click": {"action", "selector"},
    "type": {"action", "selector", "text"},
    "fill": {"action", "fields"},
    "upload": {"action", "files"},
    "screenshot": {"action", "full_page"},
    "wait": {"action", "ms", "expect", "timeout"},
    "assert": {"action", "expect"},
    "close": {"action"},
    "select": {"action", "selector", "values"},
    "press": {"action", "key"},
}


def check_step(step, index):
    if not isinstance(step, dict):
        raise BrowserError("第 %d 步需为对象" % index)
    action = step.get("action")
    if action not in BROWSER_ACTIONS:
        raise BrowserError("第 %d 步动作不在白名单：%r" % (index, action))
    unknown = sorted(set(step) - STEP_KEYS[action])
    if unknown:
        raise BrowserError("第 %d 步 %s 含未知键 %s"
                           % (index, action, ", ".join(unknown)))
    if action in ("open", "navigate"):
        check_url(step.get("url"))
    if action in ("click", "type", "select"):
        normalize_selector(step.get("selector"))
    if action == "type" and not str(step.get("text", "")):
        raise BrowserError("第 %d 步 type 需带非空 text" % index)
    if action == "fill":
        fields = step.get("fields")
        if not isinstance(fields, list) or not fields:
            raise BrowserError("第 %d 步 fill 需带非空 fields" % index)
        for j, field in enumerate(fields):
            if not isinstance(field, dict) or set(field) - {"selector", "value"}:
                raise BrowserError("第 %d 步 fields[%d] 需为 {selector,value}"
                                   % (index, j))
            normalize_selector(field.get("selector"))
            if "value" not in field:
                raise BrowserError("第 %d 步 fields[%d] 缺 value" % (index, j))
    if action == "upload":
        files = step.get("files")
        if not isinstance(files, list) or not files or \
                not all(isinstance(x, str) and x for x in files):
            raise BrowserError("第 %d 步 upload 需带非空 files 字符串数组" % index)
    if action == "select":
        values = step.get("values")
        if not isinstance(values, list) or not values:
            raise BrowserError("第 %d 步 select 需带非空 values 数组" % index)
    if action == "press" and not str(step.get("key", "")).strip():
        raise BrowserError("第 %d 步 press 需带 key" % index)
    if action in ("assert", "wait"):
        if action == "assert" or "expect" in step:
            check_expect_shape(step.get("expect"))
        if action == "wait" and "expect" not in step:
            ms = step.get("ms")
            if not isinstance(ms, int) or ms < 0 or ms > 60000:
                raise BrowserError("第 %d 步 wait 需带 expect 或 0-60000 的 ms"
                                   % index)


EXPECT_KEYS = {"text_contains", "url_contains", "title_contains"}


def check_expect_shape(expect):
    if not isinstance(expect, dict) or not expect:
        raise BrowserError("expect 需为非空对象")
    unknown = sorted(set(expect) - EXPECT_KEYS)
    if unknown:
        raise BrowserError("expect 含未知键 %s" % ", ".join(unknown))


# ---------------------------------------------------------------- 校验动作

def evaluate(expr, tab="", run=guim.run_cmd):
    args = ["evaluate", "--fn", expr] + (["--target-id", tab] if tab else [])
    res = call(args, run)
    return res["ok"], res["data"].get("result"), res["argv"], res["ms"], \
        res["message"]


def check_expect(expect, tab="", run=guim.run_cmd):
    """逐项校验；返回 (ok, 说明, 毫秒, 命令)。"""
    check_expect_shape(expect)
    details, argv_all, ms_all, ok_all = [], [], 0, True
    for key, want in expect.items():
        expr = {"text_contains": "document.body.innerText",
                "url_contains": "location.href",
                "title_contains": "document.title"}[key]
        ok, value, argv, ms, message = evaluate(expr, tab, run)
        ms_all += ms
        argv_all.extend(argv)
        actual = "" if value is None else str(value)
        good = ok and str(want) in actual
        ok_all = ok_all and good
        details.append("%s=%r %s" % (key, want, "命中" if good else
                                     ("读取失败" if not ok else "未命中")))
        if not ok:
            details[-1] += "（%s）" % message
    return ok_all, "；".join(details), ms_all, argv_all


def wait_expect(expect, tab="", timeout=10, run=guim.run_cmd):
    limit = dt.datetime.now() + dt.timedelta(seconds=timeout)
    started = dt.datetime.now()
    detail = "未开始"
    argv = []
    while True:
        ok, detail, _, argv = check_expect(expect, tab, run)
        if ok or dt.datetime.now() >= limit:
            break
        time.sleep(0.5)
    ms = int((dt.datetime.now() - started).total_seconds() * 1000)
    return ok, detail, ms, argv


# ---------------------------------------------------------------- 截图回收

def capture_screenshot(state, root, index, tab="", full_page=False,
                       run=guim.run_cmd):
    """截图并回收进会话目录；OpenClaw 媒体目录只作中转。"""
    args = ["screenshot"] + ([tab] if tab else []) \
        + (["--full-page"] if full_page else [])
    res = call(args, run)
    if not res["ok"]:
        return False, res["message"], res["argv"], res["ms"], ""
    src = res["data"].get("path") or ""
    dst = root / "steps" / ("%03d-screenshot.png" % index)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(src, dst)
    except OSError as e:
        return False, "截图回收失败：%s" % e, res["argv"], res["ms"], ""
    rel = os.path.relpath(str(dst), str(root))
    if rel not in state["artifacts"]:
        state["artifacts"].append(rel)
    return True, rel, res["argv"], res["ms"], rel


# ---------------------------------------------------------------- 会话执行

def run_plan(plan, task="", goal="", base=guim.GUI_DIR, run=guim.run_cmd,
             night_window="", night_allow=None, now=None, tab=""):
    """按计划执行浏览器动作；命令与输出逐步落会话。"""
    steps = validate_plan(plan)
    if plan.get("url") and steps[0]["action"] not in ("open", "navigate"):
        steps.insert(0, {"action": "open", "url": plan["url"]})
    state = guim.create_session("browser", goal or plan.get("goal", ""),
                                task=task, kind="browser",
                                max_steps=max(len(steps) + 2,
                                              guim.DEFAULT_MAX_STEPS),
                                base=base)
    root = guim.session_dir(state["session"], base)
    deadline = dt.datetime.now() + dt.timedelta(seconds=SESSION_TIMEOUT)
    rows, fails = [], 0
    stop_reason = stop_detail = ""
    current = state.get("url", "")

    for index, step in enumerate(steps, 1):
        if dt.datetime.now() > deadline:
            stop_reason, stop_detail = "会话超时", "超过 %s 秒" % SESSION_TIMEOUT
            break
        action = step["action"]

        if (action in WRITE_ACTIONS and night_window
                and guim.in_window(now or dt.datetime.now(), night_window)
                and not guim.night_allowed("browser", action, night_allow)):
            guim.append_pending({"at": guim.now_iso(), "session": state["session"],
                                 "app": "browser", "action": action,
                                 "step": index, "url": current,
                                 "goal": state.get("goal", "")}, base)
            guim.record_action(state, "blocked",
                               {"action": action, "window": night_window},
                               ok=False, result={"reason": "夜间未授权，已转待办"},
                               base=base)
            stop_reason, stop_detail = "夜间未授权", "清单外写操作已转待办"
            break

        if action in ("open", "navigate"):
            res = (open_url(step["url"], run) if action == "open"
                   else call(["navigate", step["url"]]
                             + (["--target-id", tab] if tab else []), run))
            ok = res["ok"]
            if ok:
                tab = res.get("tab") or tab
                current = res.get("url") or step["url"]
                state["url"] = current
            guim.record_action(state, action,
                               {"url": step["url"], "command": res["argv"]},
                               ok=ok, ms=res["ms"],
                               result={} if ok else {"detail": res["message"]},
                               base=base)
            if not ok:
                stop_reason, stop_detail = "动作失败", res["message"]
                break
            rows.append((index, action, "ok"))
            continue

        if action == "snapshot":
            cap = snapshot(tab, run)
            guim._atomic_write(root / "see.json",
                               json.dumps(cap["data"], ensure_ascii=False,
                                          indent=2) + "\n")
            guim.record_action(state, "snapshot",
                               {"target": cap["target"], "command": cap["argv"]},
                               ok=cap["ok"], ms=cap["ms"],
                               result={"url": cap["url"],
                                       "refs": len(cap["refs"])} if cap["ok"]
                               else {"detail": cap["message"]}, base=base)
            if not cap["ok"]:
                stop_reason, stop_detail = "感知失败", cap["message"]
                break
            current = cap["url"] or current
            rows.append((index, action, "ok"))
            continue

        if action == "screenshot":
            ok, detail, argv, ms, rel = capture_screenshot(
                state, root, index, tab, bool(step.get("full_page")), run)
            guim.record_action(state, "screenshot",
                               {"command": argv, "target": tab}, ok=ok, ms=ms,
                               result={"path": detail} if ok
                               else {"detail": detail},
                               screenshot=rel or None, base=base)
            if not ok:
                stop_reason, stop_detail = "动作失败", detail
                break
            rows.append((index, action, "ok"))
            continue

        if action == "wait" and "expect" not in step:
            seconds = min(step["ms"] / 1000.0, 60.0)
            time.sleep(seconds)
            guim.record_action(state, "wait", {"ms": step["ms"]}, ok=True,
                               ms=int(seconds * 1000), base=base)
            rows.append((index, action, "ok"))
            continue

        if action in ("wait", "assert"):
            ok, detail, ms, argv = wait_expect(
                step["expect"], tab, step.get("timeout", 10), run) \
                if action == "wait" else check_expect(step["expect"], tab, run)
            guim.record_action(state, action,
                               {"expect": step["expect"], "command": argv},
                               ok=ok, ms=ms, result={"detail": detail},
                               base=base)
            if ok:
                fails = 0
                rows.append((index, action, "ok"))
                continue
            fails += 1
            rows.append((index, action, "fail"))
            if fails >= MAX_VALIDATE_FAILS:
                stop_reason, stop_detail = "校验失败", detail
                break
            continue

        if action in ("click", "type", "fill", "select"):
            cap = snapshot(tab, run)
            if not cap["ok"]:
                guim.record_action(state, action, {"command": cap["argv"]},
                                   ok=False, ms=cap["ms"],
                                   result={"detail": cap["message"]}, base=base)
                stop_reason, stop_detail = "感知失败", cap["message"]
                break
            pre_url = cap["url"] or current
            params, ref_error = {}, ""
            if action == "click":
                ref, ref_error = resolve_ref(
                    cap["refs"], normalize_selector(step["selector"]))
                params = {"selector": step["selector"], "ref": ref}
                cmd = ["click", ref] + (["--target-id", tab] if tab else [])
            elif action == "type":
                ref, ref_error = resolve_ref(
                    cap["refs"], normalize_selector(step["selector"]))
                params = {"selector": step["selector"], "ref": ref,
                          "text": step["text"]}
                cmd = ["type", ref, step["text"]] \
                    + (["--target-id", tab] if tab else [])
            elif action == "select":
                ref, ref_error = resolve_ref(
                    cap["refs"], normalize_selector(step["selector"]))
                params = {"selector": step["selector"], "ref": ref,
                          "values": step["values"]}
                cmd = ["select", ref] + [str(x) for x in step["values"]] \
                    + (["--target-id", tab] if tab else [])
            else:
                fields = []
                for field in step["fields"]:
                    ref, err = resolve_ref(
                        cap["refs"], normalize_selector(field["selector"]))
                    if not ref:
                        ref_error = err
                        break
                    fields.append({"ref": ref, "value": str(field["value"])})
                params = {"selector": [f["selector"] for f in step["fields"]],
                          "fields": fields}
                cmd = ["fill", "--fields",
                       json.dumps(fields, ensure_ascii=False)] \
                    + (["--target-id", tab] if tab else [])

            if ref_error:
                guim.record_action(state, action, params, ok=False, ms=cap["ms"],
                                   result={"detail": ref_error}, base=base)
                stop_reason, stop_detail = "选择器未命中", ref_error
                break

            res = call(cmd, run)
            note = ""
            if not res["ok"] and NAVIGATION_RACE in res["message"]:
                after = snapshot(tab, run)
                if after["ok"] and after["url"] and after["url"] != pre_url:
                    res["ok"] = True
                    current = after["url"]
                    state["url"] = current
                    note = "点击触发导航，ref 随页面失效；按 URL 变化判定成功"
            elif res["ok"] and action == "click":
                after = snapshot(tab, run)
                if after["ok"] and after["url"]:
                    current = after["url"]
                    state["url"] = current
            guim.record_action(state, action,
                               dict(params, command=res["argv"]), ok=res["ok"],
                               ms=res["ms"] + cap["ms"],
                               result={"note": note} if res["ok"]
                               else {"detail": res["message"]}, base=base)
            if not res["ok"]:
                stop_reason = "单步超时" if res["code"] == 124 else "动作失败"
                stop_detail = res["message"]
                break
            rows.append((index, action, "ok"))
            continue

        if action == "upload":
            for path in step["files"]:
                res = call(["upload", path]
                           + (["--target-id", tab] if tab else []), run)
                guim.record_action(state, "upload",
                                   {"file": path, "command": res["argv"]},
                                   ok=res["ok"], ms=res["ms"],
                                   result={} if res["ok"]
                                   else {"detail": res["message"]}, base=base)
                if not res["ok"]:
                    stop_reason, stop_detail = "动作失败", res["message"]
                    break
            if stop_reason:
                break
            rows.append((index, action, "ok"))
            continue

        if action == "press":
            res = call(["press", step["key"]]
                       + (["--target-id", tab] if tab else []), run)
            guim.record_action(state, "press",
                               {"key": step["key"], "command": res["argv"]},
                               ok=res["ok"], ms=res["ms"],
                               result={} if res["ok"]
                               else {"detail": res["message"]}, base=base)
            if not res["ok"]:
                stop_reason, stop_detail = "动作失败", res["message"]
                break
            rows.append((index, action, "ok"))
            continue

        if action == "close":
            res = call(["close"] + ([tab] if tab else []), run)
            guim.record_action(state, "close", {"target": tab,
                                                "command": res["argv"]},
                               ok=res["ok"], ms=res["ms"],
                               result={} if res["ok"]
                               else {"detail": res["message"]}, base=base)
            if not res["ok"]:
                stop_reason, stop_detail = "动作失败", res["message"]
                break
            tab = ""
            rows.append((index, action, "ok"))

    summary = ["# 浏览器会话 %s" % state["session"], "",
               "- 通道：OpenClaw browser（%s）" % ("无头" if plan.get("headless",
                                                                True) else "有头"),
               "- 页面：%s" % (current or "—"),
               "- 目标：%s" % (state.get("goal") or "—"),
               "- 步骤：%d/%d" % (state["step"], state["max_steps"]),
               "- 结果：%s" % ("成功" if not stop_reason else "停手"),
               "- 停手原因：%s" % (stop_reason or "—"),
               "- 说明：%s" % (stop_detail or "—"),
               "- 时间：%s" % guim.now_iso(), ""]
    for index, action, result in rows:
        summary.append("- 第 %d 步 %s：%s" % (index, action, result))
    guim._atomic_write(root / "summary.md", "\n".join(summary) + "\n")
    if str(root / "summary.md") not in state["artifacts"]:
        state["artifacts"].append(str(root / "summary.md"))
    guim.set_status(state, "stopped" if stop_reason else "done",
                    stop_reason, base)
    return {"session": state["session"], "status": state["status"],
            "stop_reason": stop_reason, "detail": stop_detail,
            "steps": rows, "summary": str(root / "summary.md"),
            "url": current, "tab": tab}


# ---------------------------------------------------------------- 单次命令

def single(step, task="", goal="", base=guim.GUI_DIR, run=guim.run_cmd,
           tab=""):
    """执行单条浏览器命令，也落会话（命令与输出可追溯）。"""
    plan = {"steps": [step]}
    return run_plan(plan, task=task, goal=goal, base=base, run=run, tab=tab)


def latest_tab(run=guim.run_cmd):
    res = tabs(run)
    if not res["ok"]:
        raise BrowserError("读取标签页失败：%s" % res["message"])
    rows = res["tabs"]
    if not rows:
        raise BrowserError("没有打开的标签页")
    return rows[-1].get("suggestedTargetId") or rows[-1].get("tabId") or ""


def list_sessions(limit=20, base=guim.GUI_DIR):
    rows = guim.list_sessions(base=base, limit=200)
    rows = [s for s in rows if s.get("kind") == "browser"]
    return rows[:limit]
