#!/usr/bin/env python3
"""gui.py — 电脑操作层（P3 · T-0022）：应用注册表、会话、预检、只读感知。

约束：
  1. 只读。本模块不点击、不输入、不切换前台窗口。
  2. 动作原语不在此处定义（T-0023 才引入，且为代码枚举，不可配置）。
  3. 未登记应用一律拒绝；`verified_at` 为空视为未验证，同样拒绝执行。
  4. Peekaboo v4 已移除 `image` 子命令，截图统一走 `see --no-elements`。
"""

import datetime as dt
import json
import os
import platform
import plistlib
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APPS_DIR = ROOT / "agency" / "apps"
GUI_DIR = ROOT / "agency" / "state" / "gui"

# 平台边界：操作层原语（osascript/screencapture/pmset）目前只有 macOS 实现。
# 其他平台不静默降级，也不在这里移植——统一抛 GuiError，由上层提示用户。
SUPPORTED_PLATFORM = "Darwin"

PEEKABOO = (os.environ.get("PEEKABOO_BIN")
            or shutil.which("peekaboo")
            or "/opt/homebrew/bin/peekaboo")

SESSION_STATUS = ("running", "waiting", "stopped", "done")
# 允许的状态迁移：done 为终态；stopped 可解除后从断点续跑。
TRANSITIONS = {
    "running": {"waiting", "stopped", "done"},
    "waiting": {"running", "stopped", "done"},
    "stopped": {"running", "stopped", "done"},
    "done": set(),
}
DEFAULT_MAX_STEPS = 30
STEP_TIMEOUT = 30           # 单步命令超时（秒）
SESSION_TIMEOUT = 600       # 整会话超时（秒）
MAX_VALIDATE_FAILS = 2      # 连续两次校验失败即停
BLACK_LUMA = 6.0            # 0-255 平均亮度低于此值判定黑屏
SESSION_RE = re.compile(r"^gui-\d{8}-\d{6}-[0-9a-f]{4}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
BUNDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]+$")
ROLE_RE = re.compile(r"^AX[A-Za-z]+$")


class GuiError(Exception):
    """操作层可预期错误：调用方直接提示，不打印堆栈。"""


class RegistryError(GuiError):
    """应用注册表校验失败。"""


def current_platform():
    return platform.system()


def platform_supported():
    """当前平台是否有操作层实现（目前仅 macOS）。"""
    return current_platform() == SUPPORTED_PLATFORM


def require_platform():
    """非 macOS 给出明确不支持提示；不在本模块做跨平台移植。"""
    if not platform_supported():
        raise GuiError(
            "电脑操作层当前仅支持 macOS，检测到 %s；"
            "该平台暂不支持（已登记独立债务项，不在本模块移植）。" % current_platform())


def backend_name():
    """抽象接口：返回当前平台对应的操作后端名，供上层展示与路由。"""
    return "macos" if platform_supported() else "unsupported"


def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 应用注册表

REQUIRED_APP_KEYS = ("id", "name", "bundle_id", "launch", "window_match",
                     "interactive", "dialogs", "dangerous", "verified_at")


def _err(field, msg):
    raise RegistryError("字段 %s：%s" % (field, msg))


def validate_app(data, filename=""):
    """严格校验一条注册表：未知键、缺键、非法值一律拒绝。"""
    if not isinstance(data, dict):
        _err("<root>", "应为 JSON 对象")
    unknown = sorted(set(data) - set(REQUIRED_APP_KEYS))
    if unknown:
        _err("<root>", "未知键 %s" % ", ".join(unknown))
    missing = [k for k in REQUIRED_APP_KEYS if k not in data]
    if missing:
        _err("<root>", "缺键 %s" % ", ".join(missing))
    if not ID_RE.match(str(data["id"])):
        _err("id", "需为 2-32 位小写字母/数字/下划线/连字符")
    if filename and filename != "%s.json" % data["id"]:
        _err("id", "与文件名不一致（应为 %s.json）" % data["id"])
    for key in ("name", "window_match"):
        if not str(data[key]).strip():
            _err(key, "不能为空")
    if not BUNDLE_RE.match(str(data["bundle_id"])):
        _err("bundle_id", "需为反向域名，如 com.example.app")
    launch = data["launch"]
    if isinstance(launch, list):
        if not launch or not all(isinstance(x, str) and x for x in launch):
            _err("launch", "字符串数组不能为空")
    elif not (isinstance(launch, str) and launch.strip()):
        _err("launch", "需为非空字符串或字符串数组")
    for key in ("dialogs", "dangerous"):
        if not isinstance(data[key], list) or \
                not all(isinstance(x, str) and x for x in data[key]):
            _err(key, "需为字符串数组")
    items = data["interactive"]
    if not isinstance(items, list):
        _err("interactive", "需为数组")
    seen = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            _err("interactive[%d]" % i, "需为对象")
        extra = sorted(set(item) - {"key", "role", "title"})
        if extra:
            _err("interactive[%d]" % i, "未知键 %s" % ", ".join(extra))
        for key in ("key", "role"):
            if not str(item.get(key, "")).strip():
                _err("interactive[%d].%s" % (i, key), "不能为空")
        if not ROLE_RE.match(str(item["role"])):
            _err("interactive[%d].role" % i, "需为 AX 角色，如 AXButton")
        if item["key"] in seen:
            _err("interactive[%d].key" % i, "重复的 key %s" % item["key"])
        seen.add(item["key"])
    verified = str(data["verified_at"]).strip()
    if verified:
        try:
            d = dt.date.fromisoformat(verified)
        except ValueError:
            _err("verified_at", "需为 YYYY-MM-DD 或空串")
        if d > dt.date.today():
            _err("verified_at", "不能晚于今天")
    out = dict(data)
    out["verified_at"] = verified
    out["verified"] = bool(verified)
    return out


def app_path(app_id, apps_dir=APPS_DIR):
    if not ID_RE.match(str(app_id)):
        raise RegistryError("非法应用 id：%s" % app_id)
    return Path(apps_dir) / ("%s.json" % app_id)


def load_app(app_id, apps_dir=APPS_DIR):
    path = app_path(app_id, apps_dir)
    if not path.exists():
        raise RegistryError("应用未登记：%s（参照 12-电脑操作层实现方案 5.1 建档）"
                            % app_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RegistryError("%s 不是合法 JSON：%s" % (path.name, e))
    app = validate_app(data, path.name)
    app["path"] = str(path)
    return app


def list_apps(apps_dir=APPS_DIR):
    """返回注册表清单；坏文件也返回，带 error，便于一次看清全部问题。"""
    root = Path(apps_dir)
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.glob("*.json")):
        row = {"file": path.name, "id": path.stem, "name": "", "ok": False,
               "verified": False, "verified_at": "", "error": ""}
        try:
            app = validate_app(json.loads(path.read_text(encoding="utf-8")),
                               path.name)
        except (RegistryError, json.JSONDecodeError) as e:
            row["error"] = str(e)
            rows.append(row)
            continue
        row.update({"name": app["name"], "ok": True,
                    "verified": app["verified"],
                    "verified_at": app["verified_at"],
                    "interactive": len(app["interactive"])})
        rows.append(row)
    return rows


# ---------------------------------------------------------------- 会话

def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def session_dir(session_id, base=GUI_DIR):
    if not SESSION_RE.match(str(session_id)):
        raise GuiError("非法会话 id：%s" % session_id)
    return Path(base) / session_id


def new_session_id(now=None):
    now = now or dt.datetime.now()
    return "gui-%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), secrets.token_hex(2))


def create_session(app, goal, task="", kind="observe",
                   max_steps=DEFAULT_MAX_STEPS, base=GUI_DIR, session_id=None):
    sid = session_id or new_session_id()
    root = session_dir(sid, base)
    if root.exists():
        raise GuiError("会话已存在：%s" % sid)
    (root / "steps").mkdir(parents=True)
    stamp = now_iso()
    state = {
        "version": 1,
        "session": sid,
        "kind": kind,
        "task": task or "",
        "app": app,
        "goal": goal or "",
        "status": "running",
        "step": 0,
        "max_steps": int(max_steps),
        "stop_reason": "",
        "created_at": stamp,
        "updated_at": stamp,
        "artifacts": [],
    }
    save_session(state, base)
    (root / "actions.jsonl").touch()
    return state


def load_session(session_id, base=GUI_DIR):
    root = session_dir(session_id, base)
    path = root / "state.json"
    if not path.exists():
        raise GuiError("会话不存在：%s" % session_id)
    return json.loads(path.read_text(encoding="utf-8"))


def save_session(state, base=GUI_DIR):
    state["updated_at"] = now_iso()
    _atomic_write(session_dir(state["session"], base) / "state.json",
                  json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def set_status(state, status, stop_reason="", base=GUI_DIR):
    if status not in SESSION_STATUS:
        raise GuiError("未知状态：%s" % status)
    if status == "running" and state["status"] == "running":
        pass
    elif status not in TRANSITIONS.get(state["status"], set()):
        raise GuiError("状态不可从 %s 迁移到 %s" % (state["status"], status))
    state["status"] = status
    state["stop_reason"] = stop_reason if status == "stopped" else ""
    save_session(state, base)
    return state


def record_action(state, action, params=None, ok=True, result=None, ms=0,
                  screenshot=None, base=GUI_DIR):
    """追加一步动作，写 actions.jsonl 并同步 state.json。只做记录，不执行。"""
    if state["status"] != "running":
        raise GuiError("会话状态为 %s，不能再追加动作" % state["status"])
    if state["step"] >= state["max_steps"]:
        set_status(state, "stopped", "超步数", base)
        raise GuiError("超过 max_steps=%d" % state["max_steps"])
    state["step"] += 1
    item = {
        "step": state["step"],
        "at": now_iso(),
        "action": action,
        "params": params or {},
        "ok": bool(ok),
        "result": result or {},
        "ms": int(ms),
    }
    if screenshot:
        rel = Path(screenshot)
        item["screenshot"] = str(rel)
        if str(rel) not in state["artifacts"]:
            state["artifacts"].append(str(rel))
    root = session_dir(state["session"], base)
    with open(root / "actions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    save_session(state, base)
    return item


def read_actions(session_id, base=GUI_DIR):
    path = session_dir(session_id, base) / "actions.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def finish_session(state, summary, base=GUI_DIR):
    """收尾：写 summary.md 并置为 done。"""
    root = session_dir(state["session"], base)
    text = summary.strip() + "\n"
    _atomic_write(root / "summary.md", text)
    if str(root / "summary.md") not in state["artifacts"]:
        state["artifacts"].append(str(root / "summary.md"))
    set_status(state, "done", base=base)
    return state


def list_sessions(base=GUI_DIR, limit=20):
    root = Path(base)
    if not root.is_dir():
        return []
    rows = []
    for path in root.iterdir():
        if not path.is_dir() or not SESSION_RE.match(path.name):
            continue
        try:
            state = json.loads((path / "state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append(state)
    rows.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------- 预检

def run_cmd(argv, timeout=25):
    """执行一条只读命令，返回 (退出码, stdout, stderr)。测试可注入替身。"""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, "", "命令不存在：%s" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "", "超时 %ss" % timeout
    return p.returncode, p.stdout, p.stderr


def png_luma(path):
    """纯标准库解码 PNG 求平均亮度（0-255）；仅支持 8bit 非隔行。"""
    data = Path(path).read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise GuiError("不是 PNG 文件")
    pos, idat = 8, b""
    width = height = depth = color = interlace = 0
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width = int.from_bytes(chunk[0:4], "big")
            height = int.from_bytes(chunk[4:8], "big")
            depth, color, interlace = chunk[8], chunk[9], chunk[12]
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if depth != 8 or interlace != 0 or not channels or not width or not height:
        raise GuiError("不支持的 PNG 格式")
    raw = zlib.decompress(idat)
    stride = width * channels
    prev = bytearray(stride)
    total = 0.0
    count = 0
    for y in range(height):
        off = y * (stride + 1)
        ft = raw[off]
        line = bytearray(raw[off + 1:off + 1 + stride])
        if ft == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = prev[i]
                ul = prev[i - channels] if i >= channels else 0
                p = left + up - ul
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                pred = left if (pa <= pb and pa <= pc) else (up if pb <= pc else ul)
                line[i] = (line[i] + pred) & 0xFF
        elif ft != 0:
            raise GuiError("未知 PNG 行过滤器：%d" % ft)
        for i in range(0, stride, channels):
            if channels >= 3:
                total += (line[i] + line[i + 1] + line[i + 2]) / 3.0
            else:
                total += line[i]
            count += 1
        prev = line
    return total / count if count else 0.0


def check_accessibility(run=run_cmd):
    code, out, err = run([PEEKABOO, "permissions", "--json"], 20)
    if code != 0:
        return _check("accessibility", "辅助功能", False,
                      "peekaboo 调用失败：%s" % (err.strip() or code),
                      "安装 Peekaboo：brew install steipete/tap/peekaboo")
    try:
        payload = json.loads(out)
        perms = (payload.get("data") or {}).get("permissions") or []
    except json.JSONDecodeError:
        return _check("accessibility", "辅助功能", False, "权限输出不是 JSON",
                      "重试 peekaboo permissions --json")
    granted = {p.get("name"): p.get("isGranted") for p in perms}
    ok = bool(granted.get("Accessibility"))
    return _check(
        "accessibility", "辅助功能", ok,
        "Accessibility=%s" % ("已授权" if ok else "未授权"),
        "系统设置 > 隐私与安全 > 辅助功能，勾选终端与 Peekaboo")


def check_screen(run=run_cmd):
    tmp = Path(tempfile.mkdtemp(prefix="agency-gui-"))
    shot = tmp / "screen.png"
    small = tmp / "small.png"
    try:
        code, _, err = run(["screencapture", "-x", str(shot)], 25)
        if code != 0 or not shot.exists() or shot.stat().st_size == 0:
            return _check("screen", "屏幕录制", False,
                          "截图失败：%s" % (err.strip() or code),
                          "系统设置 > 隐私与安全 > 屏幕录制，勾选终端")
        code, _, _ = run(["sips", "-Z", "64", str(shot), "--out", str(small)], 20)
        target = small if code == 0 and small.exists() else shot
        try:
            luma = png_luma(target)
        except (GuiError, zlib.error):
            # 无法解码时退化为体积判断：黑屏 PNG 压缩后极小。
            luma = 255.0 if shot.stat().st_size > 20 * 1024 else 0.0
        ok = luma >= BLACK_LUMA
        return _check("screen", "屏幕录制", ok,
                      "截图 %dKB，平均亮度 %.1f" % (shot.stat().st_size // 1024, luma),
                      "保持显示器常亮；仍为黑屏则检查屏幕录制授权")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_lock(run=run_cmd):
    code, out, err = run(["ioreg", "-n", "Root", "-d1", "-a"], 15)
    if code != 0:
        return _check("lock", "是否锁屏", False,
                      "ioreg 读取失败：%s" % (err.strip() or code),
                      "解锁后重试；若持续失败改用 console 用户自查")
    try:
        plist = plistlib.loads(out.encode("utf-8"))
    except Exception:  # noqa: BLE001  plist 解析失败统一按未知处理
        return _check("lock", "是否锁屏", False, "ioreg 输出无法解析",
                      "解锁后重试")
    users = plist.get("IOConsoleUsers") or []
    locked = bool(plist.get("IOConsoleLocked")) or any(
        u.get("CGSSessionScreenIsLocked") for u in users)
    return _check("lock", "是否锁屏", not locked,
                  "已锁屏" if locked else "已解锁",
                  "解锁屏幕后重跑；夜间保持不锁屏")


def check_sleep(run=run_cmd):
    code, out, err = run(["pmset", "-g", "assertions"], 15)
    if code != 0:
        return _check("sleep", "是否将睡眠", False,
                      "pmset 读取失败：%s" % (err.strip() or code),
                      "执行 caffeinate -d 保持不休眠")
    match = re.search(r"^\s*PreventUserIdleSystemSleep\s+([01])\s*$",
                      out, re.MULTILINE)
    blocked = bool(match and match.group(1) == "1")
    sleeping = not blocked
    detail = "已阻止空闲睡眠" if blocked else "空闲将睡眠"
    return _check("sleep", "是否将睡眠", not sleeping, detail,
                  "执行 caffeinate -d，或 sudo pmset -a disablesleep 1")


def _check(key, label, ok, detail, fix=""):
    return {"key": key, "label": label, "ok": bool(ok),
            "detail": detail, "fix": fix}


def preflight(run=run_cmd):
    """四项预检：辅助功能 / 屏幕录制 / 是否锁屏 / 是否将睡眠。"""
    require_platform()
    checks = [check_accessibility(run), check_screen(run),
              check_lock(run), check_sleep(run)]
    return {"ok": all(c["ok"] for c in checks), "at": now_iso(),
            "peekaboo": PEEKABOO, "checks": checks}


# ---------------------------------------------------------------- 感知

def _find_elements(payload):
    """抽取元素数组。v4.5.0 实测字段为 data.ui_elements，其余为兼容兜底。"""
    data = payload.get("data") or {}
    for holder in (data, data.get("ui_map"), data.get("uiMap"),
                   data.get("snapshot")):
        if isinstance(holder, dict):
            for key in ("ui_elements", "elements"):
                items = holder.get(key)
                if isinstance(items, list):
                    return items
    return []


def summarize_elements(elements):
    """按 AX 角色统计，并返回可交互数量（is_actionable 优先）。"""
    roles = {}
    actionable = 0
    for e in elements:
        if not isinstance(e, dict):
            continue
        roles[e.get("ax_role") or e.get("role") or "?"] = \
            roles.get(e.get("ax_role") or e.get("role") or "?", 0) + 1
        if e.get("is_actionable") is True:
            actionable += 1
    return {"total": len(elements), "actionable": actionable,
            "roles": dict(sorted(roles.items()))}


def capture(app, png=None, run=run_cmd, timeout=45):
    """读一次元素树（可选截图）。返回统一结构，供感知与动作层共用。"""
    argv = [PEEKABOO, "see", "--app", app["bundle_id"], "--json",
            "--timeout", "30000", "--max-elements", "300"]
    if png:
        argv += ["--path", str(png)]
    started = dt.datetime.now()
    code, out, err = run(argv, timeout)
    ms = int((dt.datetime.now() - started).total_seconds() * 1000)
    try:
        payload = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    data = payload.get("data") or {}
    ok = code == 0 and bool(payload.get("success"))
    message = ((payload.get("error") or {}).get("message")
               or err.strip() or ("退出码 %s" % code))
    return {
        "ok": ok,
        "argv": argv,
        "ms": ms,
        "payload": payload,
        "elements": _find_elements(payload) if ok else [],
        "window_title": data.get("window_title") or data.get("title") or "",
        "snapshot": data.get("snapshot_id") or "",
        "is_dialog": bool(data.get("is_dialog")),
        "error_code": (payload.get("error") or {}).get("code", ""),
        "message": "" if ok else message,
    }


def screenshot(app, png, run=run_cmd):
    """只抓窗口截图，不取元素树。"""
    argv = [PEEKABOO, "see", "--app", app["bundle_id"], "--no-elements",
            "--path", str(png), "--timeout", "20000"]
    started = dt.datetime.now()
    code, _, err = run(argv, 30)
    return code == 0, argv, int((dt.datetime.now() - started).total_seconds() * 1000), err.strip()


def observe(app_id, session_id=None, apps_dir=APPS_DIR, base=GUI_DIR,
            run=run_cmd, goal="只读感知"):
    """读取目标应用的元素树与窗口截图。不点击、不输入。"""
    require_platform()
    app = load_app(app_id, apps_dir)
    if not app["verified"]:
        raise GuiError("应用 %s 未验证（verified_at 为空），拒绝操作" % app_id)
    state = (load_session(session_id, base) if session_id
             else create_session(app_id, goal, kind="observe", base=base))
    root = session_dir(state["session"], base)
    step = state["step"] + 1
    png = root / "steps" / ("%03d-observe.png" % step)
    png.parent.mkdir(parents=True, exist_ok=True)
    cap = capture(app, png, run)
    if not cap["ok"]:
        record_action(state, "observe", {"app": app_id, "command": cap["argv"]},
                      ok=False, result={"error_code": cap["error_code"],
                                        "message": cap["message"]}, ms=cap["ms"],
                      base=base)
        set_status(state, "stopped", "感知失败", base)
        raise GuiError("感知失败：%s%s" % (
            cap["message"],
            "（%s）" % cap["error_code"] if cap["error_code"] else ""))
    elements = cap["elements"]
    stats = summarize_elements(elements)
    _atomic_write(root / "see.json",
                  json.dumps(cap["payload"], ensure_ascii=False, indent=2) + "\n")
    rel = os.path.relpath(png, root)
    record_action(state, "observe",
                  {"app": app_id, "command": cap["argv"]},
                  ok=True, result={"element_count": stats["total"],
                                   "actionable": stats["actionable"]},
                  ms=cap["ms"], screenshot=rel, base=base)
    summary = "\n".join([
        "# 感知摘要 %s" % state["session"],
        "",
        "- 应用：%s（%s）" % (app["name"], app_id),
        "- 窗口：%s" % (cap["window_title"] or "—"),
        "- 目标：%s" % state["goal"],
        "- 元素：%d 个，可交互 %d 个" % (stats["total"], stats["actionable"]),
        "- 截图：%s" % rel,
        "- 原始元素树：see.json",
        "- 时间：%s" % now_iso(),
    ])
    finish_session(state, summary, base)
    return {"session": state["session"], "app": app_id,
            "app_name": app["name"], "window_title": cap["window_title"],
            "element_count": stats["total"], "actionable": stats["actionable"],
            "roles": stats["roles"], "elements": elements,
            "screenshot": str(png),
            "snapshot_id": cap["snapshot"],
            "raw": str(root / "see.json")}


# ---------------------------------------------------------------- 动作与校验

# 动作原语是代码枚举，不可通过配置新增（方案 5.13）。
ACTION_WHITELIST = ("launch", "focus", "observe", "click", "set", "type",
                    "key", "scroll", "wait", "assert", "screenshot")
STOP_REASONS = ("未知弹窗", "验证码", "登录失效", "支付页", "授权页", "危险动作",
                "超步数", "校验失败", "单步超时", "会话超时", "动作失败",
                "感知失败", "选择器未命中", "夜间未授权", "权限缺失")
SENSITIVE_TITLE = (("验证码", "验证码"), ("支付", "支付页"),
                   ("授权", "授权页"), ("登录", "登录失效"), ("密码", "登录失效"))
# 写操作：夜间未预授权一律不入队（方案 5.8）。
WRITE_ACTIONS = ("click", "set", "type", "key", "scroll")
NIGHT_WINDOW_DEFAULT = "23:00-08:00"


def normalize_selector(selector):
    """选择器优先级：id > role+title > role+index > 坐标（坐标须写明 reason）。"""
    if not isinstance(selector, dict) or not selector:
        raise GuiError("selector 需为非空对象")
    unknown = set(selector) - {"id", "role", "title", "index", "x", "y",
                               "reason"}
    if unknown:
        raise GuiError("selector 含未知键 %s" % ", ".join(sorted(unknown)))
    if "id" in selector:
        if not str(selector["id"]).strip():
            raise GuiError("selector.id 不能为空")
        return {"kind": "id", "id": str(selector["id"])}
    if "role" in selector:
        if not ROLE_RE.match(str(selector["role"])):
            raise GuiError("selector.role 需为 AX 角色，如 AXButton")
        if str(selector.get("title", "")).strip():
            return {"kind": "role_title", "role": selector["role"],
                    "title": str(selector["title"])}
        if "index" in selector:
            if not isinstance(selector["index"], int) or selector["index"] < 0:
                raise GuiError("selector.index 需为非负整数")
            return {"kind": "role_index", "role": selector["role"],
                    "index": selector["index"]}
        raise GuiError("role 选择器需带 title 或 index")
    if "x" in selector and "y" in selector:
        if not str(selector.get("reason", "")).strip():
            raise GuiError("坐标选择器必须写明 reason")
        return {"kind": "point", "x": float(selector["x"]),
                "y": float(selector["y"]), "reason": selector["reason"]}
    raise GuiError("selector 需为 id / role+title / role+index / x+y+reason 之一")


def resolve_selector(elements, selector):
    """在最近一次元素树里定位元素；坐标选择器原样返回。"""
    if selector["kind"] == "point":
        return dict(selector)
    matches = []
    exact = []
    for e in elements:
        role = e.get("ax_role") or e.get("role")
        if selector["kind"] == "id":
            if e.get("id") == selector["id"]:
                matches.append(e)
        elif role == selector["role"]:
            if selector["kind"] == "role_index":
                matches.append(e)
            else:
                # 输入后 label 常被内容覆盖，label/title/value 任一命中即可。
                labels = [str(e.get(k) or "") for k in ("label", "title", "value")]
                if any(label == selector["title"] for label in labels):
                    exact.append(e)
                elif any(selector["title"] in label for label in labels if label):
                    matches.append(e)
    if exact:
        return exact[0]
    if not matches:
        return None
    if selector["kind"] == "role_index":
        return matches[selector["index"]] if selector["index"] < len(matches) \
            else None
    return matches[0]


def normalize_expect(expect):
    if not isinstance(expect, dict):
        raise GuiError("expect 需为对象")
    kind = expect.get("kind")
    if kind == "element_exists":
        return {"kind": kind,
                "selector": normalize_selector(expect.get("selector"))}
    if kind == "window_title_contains":
        text = str(expect.get("text", "")).strip()
        if not text:
            raise GuiError("expect.text 不能为空")
        return {"kind": kind, "text": text}
    if kind == "text_contains":
        text = str(expect.get("text", "")).strip()
        if not text:
            raise GuiError("expect.text 不能为空")
        return {"kind": kind, "text": text,
                "selector": normalize_selector(expect.get("selector"))}
    raise GuiError("expect.kind 需为 element_exists / window_title_contains / text_contains")


def validate_plan(plan):
    """严格校验动作计划：未知动作、未知键、缺参数一律拒绝。"""
    if not isinstance(plan, dict):
        raise GuiError("计划需为 JSON 对象")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise GuiError("计划缺少非空 steps 数组")
    allowed = {"action", "selector", "value", "text", "combo", "direction",
               "amount", "expect", "timeout", "foreground", "window_id"}
    out = []
    for i, raw in enumerate(steps, 1):
        if not isinstance(raw, dict):
            raise GuiError("计划第 %d 步需为对象" % i)
        action = raw.get("action")
        if action not in ACTION_WHITELIST:
            raise GuiError("计划第 %d 步：未知动作 %r（动作原语不可配置）"
                           % (i, action))
        extra = sorted(set(raw) - allowed)
        if extra:
            raise GuiError("计划第 %d 步：未知键 %s" % (i, ", ".join(extra)))
        step = {"action": action}
        if action in ("click", "set") and "selector" not in raw:
            raise GuiError("计划第 %d 步：%s 需要 selector" % (i, action))
        if "selector" in raw:
            step["selector"] = normalize_selector(raw["selector"])
        if action == "set":
            if "value" not in raw:
                raise GuiError("计划第 %d 步：set 需要 value" % i)
            step["value"] = str(raw["value"])
        if action == "type":
            if not str(raw.get("text", "")).strip():
                raise GuiError("计划第 %d 步：type 需要 text" % i)
            step["text"] = str(raw["text"])
        if action == "key":
            if not str(raw.get("combo", "")).strip():
                raise GuiError("计划第 %d 步：key 需要 combo" % i)
            step["combo"] = str(raw["combo"])
            if not raw.get("foreground") and not raw.get("window_id"):
                raise GuiError("计划第 %d 步：key 需显式 foreground=true "
                               "或提供 window_id（避免静默抢焦点）" % i)
        if action == "scroll":
            direction = raw.get("direction")
            if direction not in ("up", "down", "left", "right"):
                raise GuiError("计划第 %d 步：scroll.direction 需为 up/down/left/right" % i)
            amount = raw.get("amount", 3)
            if not isinstance(amount, int) or amount <= 0:
                raise GuiError("计划第 %d 步：scroll.amount 需为正整数" % i)
            step["direction"], step["amount"] = direction, amount
        if action in ("wait", "assert") and "expect" not in raw:
            raise GuiError("计划第 %d 步：%s 需要 expect" % (i, action))
        if "expect" in raw:
            step["expect"] = normalize_expect(raw["expect"])
        if action in ("wait", "assert"):
            step["timeout"] = int(raw.get("timeout", 10 if action == "wait" else 0))
        if raw.get("foreground"):
            step["foreground"] = True
        if raw.get("window_id"):
            step["window_id"] = int(raw["window_id"])
        out.append(step)
    return out


def check_expect(cap, expect):
    if expect["kind"] == "window_title_contains":
        title = cap["window_title"] or ""
        if expect["text"] in title:
            return True, "窗口标题含「%s」" % expect["text"]
        return False, "窗口标题为「%s」" % (title or "空")
    element = resolve_selector(cap["elements"], expect["selector"])
    if element is None:
        return False, "未找到目标元素"
    if expect["kind"] == "element_exists":
        return True, "元素存在：%s" % element.get("id", "")
    value = str(element.get("value") or element.get("label")
                or element.get("title") or "")
    if expect["text"] in value:
        return True, "元素值含「%s」" % expect["text"]
    return False, "元素值为「%s」" % value[:60]


def detect_stop(app, cap):
    """停手判定：未知弹窗 / 验证码 / 登录失效 / 支付 / 授权 / 危险词。"""
    title = cap["window_title"] or ""
    if cap["is_dialog"]:
        return "未知弹窗"
    for word, reason in SENSITIVE_TITLE:
        if word in title:
            return reason
    for word in app["dialogs"]:
        if word and word in title:
            return "未知弹窗"
    for word in app["dangerous"]:
        if word and word in title:
            return "危险动作"
    return ""


def build_command(app, step, elements, snapshot):
    """把一步动作翻译成白名单命令；返回 (argv, 错误说明)。"""
    action = step["action"]
    bundle = app["bundle_id"]
    if action == "launch":
        # 冷启动必然激活应用，Peekaboo 也只接受前台启动；已在运行的调用等价无操作。
        return [PEEKABOO, "app", "launch", "--bundle-id", bundle,
                "--wait-ready", "--foreground"], ""
    if action == "focus":
        return [PEEKABOO, "app", "switch", "--to", bundle,
                "--foreground"], ""
    if action == "click":
        element = resolve_selector(elements, step["selector"])
        if element is None:
            return None, "选择器未命中"
        if element.get("kind") == "point":
            argv = [PEEKABOO, "click", "--at",
                    "%g,%g" % (element["x"], element["y"]), "--app", bundle]
            return argv, ""
        argv = [PEEKABOO, "click", "--on", str(element["id"]),
                "--wait-for", "3000"]
        # --snapshot 已指明应用与窗口，不能再叠加 --app。
        argv += ["--snapshot", snapshot] if snapshot else ["--app", bundle]
        return argv, ""
    if action == "set":
        element = resolve_selector(elements, step["selector"])
        if element is None:
            return None, "选择器未命中"
        argv = [PEEKABOO, "set-value", step["value"], "--on",
                str(element["id"]), "--snapshot", snapshot or "latest"]
        return argv, ""
    if action == "type":
        return [PEEKABOO, "type", step["text"], "--app", bundle], ""
    if action == "key":
        argv = [PEEKABOO, "press", step["combo"], "--app", bundle]
        if step.get("foreground"):
            argv.append("--foreground")
        else:
            argv += ["--window-id", str(step["window_id"])]
        return argv, ""
    if action == "scroll":
        argv = [PEEKABOO, "scroll", "--direction", step["direction"],
                "--amount", str(step["amount"]), "--app", bundle]
        if "selector" in step:
            element = resolve_selector(elements, step["selector"])
            if element is None:
                return None, "选择器未命中"
            argv += ["--on", str(element["id"])]
        return argv, ""
    return None, "不支持的动作 %s" % action


def _rel(path, root):
    return os.path.relpath(str(path), str(root))


def _exec(argv, run, timeout):
    started = dt.datetime.now()
    code, out, err = run(argv, timeout)
    ms = int((dt.datetime.now() - started).total_seconds() * 1000)
    return code == 0, code, (err.strip() or out.strip())[:300], ms


def wait_expect(app, step, run, root, index):
    """轮询直到条件成立或超时；返回 (ok, detail, 最后截图相对路径, 毫秒)。"""
    png = root / "steps" / ("%03d-%s.png" % (index, step["action"]))
    limit = dt.datetime.now() + dt.timedelta(seconds=step.get("timeout", 10))
    detail = "未开始"
    started = dt.datetime.now()
    while True:
        cap = capture(app, png, run, STEP_TIMEOUT)
        if cap["ok"]:
            ok, detail = check_expect(cap, step["expect"])
            if ok:
                break
        else:
            detail = cap["message"]
        if dt.datetime.now() >= limit:
            return False, detail, _rel(png, root), \
                int((dt.datetime.now() - started).total_seconds() * 1000)
        time.sleep(0.5)
    return True, detail, _rel(png, root), \
        int((dt.datetime.now() - started).total_seconds() * 1000)


def parse_window(text):
    m = re.match(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$", str(text).strip())
    if not m:
        raise GuiError("夜间时间窗需为 HH:MM-HH:MM，如 23:00-08:00")
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        raise GuiError("夜间时间窗取值越界：%s" % text)
    return h1 * 60 + m1, h2 * 60 + m2


def in_window(moment, window):
    start, end = parse_window(window)
    current = moment.hour * 60 + moment.minute
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def night_allowed(app_id, action, allow):
    allow = allow or []
    return ("%s:%s" % (app_id, action) in allow
            or "%s:*" % app_id in allow)


def pending_path(base=GUI_DIR):
    return Path(base) / "pending.jsonl"


def append_pending(record, base=GUI_DIR):
    path = pending_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_pending(base=GUI_DIR):
    path = pending_path(base)
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def blocked_session(app_id, checks, goal="", task="", base=GUI_DIR):
    """预检不通过也要留痕：建一条 stopped 会话，不静默失败。"""
    state = create_session(app_id, goal or "预检未通过", task=task,
                           kind="run", base=base)
    failed = [c for c in checks if not c["ok"]]
    record_action(state, "preflight", {"checks": [c["key"] for c in checks]},
                  ok=False,
                  result={"failed": ["%s：%s" % (c["label"], c["detail"])
                                     for c in failed]}, base=base)
    root = session_dir(state["session"], base)
    lines = ["# 预检拦截 %s" % state["session"], "",
             "- 应用：%s" % app_id, "- 时间：%s" % now_iso(), ""]
    for c in checks:
        lines.append("- [%s] %s：%s" % ("OK" if c["ok"] else "失败",
                                       c["label"], c["detail"]))
    lines += ["", "处置：解锁 / 补授权后重跑。"]
    _atomic_write(root / "summary.md", "\n".join(lines) + "\n")
    state["artifacts"].append(str(root / "summary.md"))
    set_status(state, "stopped", "权限缺失", base)
    return state["session"]


def receipt(session_id=None, base=GUI_DIR):
    """一条回执：状态、停手原因、步数、截图数、待办数。"""
    if session_id:
        state = load_session(session_id, base)
    else:
        rows = list_sessions(base, 1)
        if not rows:
            raise GuiError("还没有会话")
        state = rows[0]
    root = session_dir(state["session"], base)
    steps_dir = root / "steps"
    shots = len(list(steps_dir.glob("*.png"))) if steps_dir.is_dir() else 0
    return {"session": state["session"], "status": state["status"],
            "stop_reason": state.get("stop_reason", ""),
            "step": state["step"], "screenshots": shots,
            "updated_at": state.get("updated_at", ""),
            "summary": str(root / "summary.md"),
            "pending": len(read_pending(base))}


def run_plan(app_id, plan, task="", base=GUI_DIR, apps_dir=APPS_DIR,
             run=run_cmd, goal="", night_window="", night_allow=None,
             now=None):
    """按计划执行动作，逐步截图，命中停手规则即停。返回会话摘要。"""
    # Real execution is macOS-only; injected runners are deterministic test doubles.
    if run is run_cmd:
        require_platform()
    app = load_app(app_id, apps_dir)
    if not app["verified"]:
        raise GuiError("应用 %s 未验证（verified_at 为空），拒绝操作" % app_id)
    steps = validate_plan(plan)
    state = create_session(app_id, goal or plan.get("goal", ""), task=task,
                           kind="run",
                           max_steps=max(len(steps), DEFAULT_MAX_STEPS),
                           base=base)
    root = session_dir(state["session"], base)
    deadline = dt.datetime.now() + dt.timedelta(seconds=SESSION_TIMEOUT)
    fails = 0
    rows = []
    stop_reason = ""
    stop_detail = ""

    for index, step in enumerate(steps, 1):
        if dt.datetime.now() > deadline:
            stop_reason, stop_detail = "会话超时", "超过 %s 秒" % SESSION_TIMEOUT
            break
        action = step["action"]
        cap = None
        png = None

        # 夜间清单外写操作：不入队，转待办（方案 5.8）。
        if (action in WRITE_ACTIONS and night_window
                and in_window(now or dt.datetime.now(), night_window)
                and not night_allowed(app_id, action, night_allow)):
            append_pending({"at": now_iso(), "session": state["session"],
                            "app": app_id, "action": action, "step": index,
                            "selector": step.get("selector", {}),
                            "goal": state.get("goal", "")}, base)
            record_action(state, "blocked",
                          {"action": action, "window": night_window},
                          ok=False,
                          result={"reason": "夜间未授权，已转待办"}, base=base)
            stop_reason, stop_detail = "夜间未授权", "清单外写操作已转待办"
            break

        # 动作前先快照，既拿元素 id，也做停手检查。
        if action not in ("launch", "focus", "screenshot"):
            png = root / "steps" / ("%03d-%s.png" % (index, action))
            cap = capture(app, png, run, STEP_TIMEOUT)
            if not cap["ok"]:
                record_action(state, action, {"command": cap["argv"]},
                              ok=False, result={"error": cap["message"]},
                              ms=cap["ms"], base=base)
                stop_reason, stop_detail = "感知失败", cap["message"]
                break
            reason = detect_stop(app, cap)
            if reason:
                shot = root / "steps" / ("%03d-stop.png" % index)
                screenshot(app, shot, run)
                stop_reason, stop_detail = reason, "动作前命中停手规则"
                record_action(state, "stop", {"rule": reason},
                              ok=False, result={"title": cap["window_title"]},
                              ms=0, screenshot=_rel(shot, root), base=base)
                break

        if action == "observe":
            _atomic_write(root / "see.json",
                          json.dumps(cap["payload"], ensure_ascii=False,
                                     indent=2) + "\n")
            record_action(state, "observe", {"command": cap["argv"]},
                          ok=True, result=summarize_elements(cap["elements"]),
                          ms=cap["ms"],
                          screenshot=_rel(png, root), base=base)
            rows.append((index, action, "ok"))
            continue

        if action == "screenshot":
            png = root / "steps" / ("%03d-screenshot.png" % index)
            ok, argv, ms, err = screenshot(app, png, run)
            record_action(state, "screenshot", {"command": argv}, ok=ok,
                          result={"error": err} if not ok else {}, ms=ms,
                          screenshot=_rel(png, root), base=base)
            if not ok:
                stop_reason, stop_detail = "动作失败", err or "截图失败"
                break
            rows.append((index, action, "ok"))
            continue

        if action in ("wait", "assert"):
            ok, detail, rel, ms = wait_expect(app, step, run, root, index)
            record_action(state, action, {"expect": step["expect"]}, ok=ok,
                          result={"detail": detail}, ms=ms,
                          screenshot=rel, base=base)
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

        argv, build_err = build_command(
            app, step, cap["elements"] if cap else [],
            cap["snapshot"] if cap else "")
        if argv is None:
            record_action(state, action, {"command": []}, ok=False,
                          result={"detail": build_err}, ms=0, base=base)
            stop_reason, stop_detail = "选择器未命中", build_err
            break
        ok, code, detail, ms = _exec(argv, run, STEP_TIMEOUT)
        if not ok:
            # 单步失败重试一次（方案 5.6）。
            ok, code, detail, ms2 = _exec(argv, run, STEP_TIMEOUT)
            ms += ms2
        record_action(state, action, {"command": argv}, ok=ok, ms=ms,
                      result={} if ok else {"code": code, "detail": detail},
                      screenshot=_rel(png, root) if png else None, base=base)
        if not ok:
            stop_reason = "单步超时" if code == 124 else "动作失败"
            stop_detail = detail
            break

        # 有 expect 的动作，动作后再校验一次。
        if "expect" in step:
            after = root / "steps" / ("%03d-%s-after.png" % (index, action))
            cap2 = capture(app, after, run, STEP_TIMEOUT)
            if not cap2["ok"]:
                fails += 1
                detail = cap2["message"]
            else:
                good, detail = check_expect(cap2, step["expect"])
                fails = 0 if good else fails + 1
            if fails >= MAX_VALIDATE_FAILS:
                stop_reason, stop_detail = "校验失败", detail
                break
        rows.append((index, action, "ok"))

    summary = ["# 动作会话 %s" % state["session"], "",
               "- 应用：%s（%s）" % (app["name"], app_id),
               "- 目标：%s" % (state.get("goal") or "—"),
               "- 步骤：%d/%d" % (state["step"], state["max_steps"]),
               "- 结果：%s" % ("成功" if not stop_reason else "停手"),
               "- 停手原因：%s" % (stop_reason or "—"),
               "- 说明：%s" % (stop_detail or "—"),
               "- 时间：%s" % now_iso(), ""]
    for index, action, result in rows:
        summary.append("- 第 %d 步 %s：%s" % (index, action, result))
    _atomic_write(root / "summary.md", "\n".join(summary) + "\n")
    if str(root / "summary.md") not in state["artifacts"]:
        state["artifacts"].append(str(root / "summary.md"))
    set_status(state, "stopped" if stop_reason else "done",
               stop_reason, base)
    return {"session": state["session"], "status": state["status"],
            "stop_reason": stop_reason, "detail": stop_detail,
            "steps": rows, "summary": str(root / "summary.md")}
