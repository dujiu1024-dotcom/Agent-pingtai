#!/usr/bin/env python3
"""policy.py — 策略层与模型选择器（P3 · T-0027）。

原则（方案 5.13）：策略可配置，动作不可配置。

  - 策略文件 `agency/policies/gui.json`：模型路由 / 执行通道 / 风险等级 / 夜间许可。
  - 动作原语只认 `gui.ACTION_WHITELIST`，配置里出现未登记动作一律拒绝加载。
  - 运行时覆盖仍写 `config_kv`，优先级：任务级 > 应用级 > 全局默认。
  - 缺模型 / Ollama 不可达不静默换模型；只按配置里写明的降级链接续，
    链尾是 `rules` 时返回规则判定。
"""

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import gui as guim
import llm_local

ROOT = Path(__file__).resolve().parent
POLICY_PATH = ROOT / "agency" / "policies" / "gui.json"

PURPOSES = ("route", "extract", "judge", "plan", "generate", "embed")
BACKENDS = ("peekaboo", "browser", "api")
RISK_LEVELS = ("A0", "A1", "A2", "A3")
DEFAULT_ROUTES = {
    "route": {"primary": "qwen3:1.7b", "fallback": ["rules"]},
    "extract": {"primary": "qwen3:1.7b", "fallback": ["rules"]},
    "judge": {"primary": "qwen3:1.7b", "fallback": ["rules"]},
    "plan": {"primary": "remote:doubao", "fallback": ["qwen3:1.7b", "rules"]},
    "generate": {"primary": "remote:doubao", "fallback": ["qwen3:1.7b"]},
    "embed": {"primary": "bge-m3", "fallback": []},
}
DEFAULT_RISK = {action: "A0" for action in guim.ACTION_WHITELIST}
for _action in guim.WRITE_ACTIONS:
    DEFAULT_RISK[_action] = "A1"
DEFAULT_RISK["launch"] = "A2"
DEFAULT_RISK["focus"] = "A2"
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(:[A-Za-z0-9._-]+)?$")
POLICY_KEYS = {"version", "models", "apps", "risk", "night"}


class PolicyError(guim.GuiError):
    """策略文件或选择器可预期错误。"""


def _err(field, message):
    raise PolicyError("%s：%s" % (field, message))


def model_ok(model):
    return (model == "rules" or model.startswith("remote:")
            or bool(MODEL_RE.match(model or "")))


def validate_policy(data):
    """严格校验策略文件；未知键、未登记动作、非法值一律拒绝。"""
    if not isinstance(data, dict):
        _err("策略", "需为 JSON 对象")
    unknown = sorted(set(data) - POLICY_KEYS)
    if unknown:
        _err("策略", "未知键 %s" % ", ".join(unknown))
    missing = sorted(POLICY_KEYS - set(data))
    if missing:
        _err("策略", "缺键 %s" % ", ".join(missing))
    if data["version"] != 1:
        _err("version", "目前只支持 1")

    models = data["models"]
    if not isinstance(models, dict):
        _err("models", "需为对象")
    for purpose, route in models.items():
        if purpose not in PURPOSES:
            _err("models.%s" % purpose,
                 "未知用途（可选 %s）" % "/".join(PURPOSES))
        if not isinstance(route, dict) or set(route) != {"primary", "fallback"}:
            _err("models.%s" % purpose, "需为 {primary,fallback}")
        primary = route["primary"]
        if not isinstance(primary, str) or not model_ok(primary):
            _err("models.%s.primary" % purpose, "非法模型 %r" % (primary,))
        fallback = route["fallback"]
        if not isinstance(fallback, list) or \
                not all(isinstance(x, str) and model_ok(x) for x in fallback):
            _err("models.%s.fallback" % purpose, "需为合法模型数组")

    apps = data["apps"]
    if not isinstance(apps, dict):
        _err("apps", "需为对象")
    for app_id, conf in apps.items():
        if not guim.ID_RE.match(str(app_id)):
            _err("apps.%s" % app_id, "非法应用 id")
        if not isinstance(conf, dict) or set(conf) != {"backend", "verified_at"}:
            _err("apps.%s" % app_id, "需为 {backend,verified_at}")
        if conf["backend"] not in BACKENDS:
            _err("apps.%s.backend" % app_id,
                 "需为 %s" % "/".join(BACKENDS))
        verified = str(conf["verified_at"]).strip()
        if verified:
            try:
                day = dt.date.fromisoformat(verified)
            except ValueError:
                _err("apps.%s.verified_at" % app_id, "需为 YYYY-MM-DD 或空串")
            if day > dt.date.today():
                _err("apps.%s.verified_at" % app_id, "不能晚于今天")

    risk = data["risk"]
    if not isinstance(risk, dict):
        _err("risk", "需为对象")
    for action, level in risk.items():
        if action not in guim.ACTION_WHITELIST:
            _err("risk.%s" % action,
                 "未登记动作（白名单：%s）" % "/".join(guim.ACTION_WHITELIST))
        if level not in RISK_LEVELS:
            _err("risk.%s" % action, "需为 %s" % "/".join(RISK_LEVELS))

    night = data["night"]
    if not isinstance(night, dict) or set(night) != {"window", "allow"}:
        _err("night", "需为 {window,allow}")
    try:
        guim.parse_window(str(night["window"]))
    except guim.GuiError as e:
        _err("night.window", str(e))
    allow = night["allow"]
    if not isinstance(allow, list):
        _err("night.allow", "需为数组")
    for item in allow:
        if not isinstance(item, str) or ":" not in item:
            _err("night.allow", "需为 app:action，如 doubao:set")
        app_id, action = item.split(":", 1)
        if not guim.ID_RE.match(app_id):
            _err("night.allow", "非法应用 id：%s" % app_id)
        if action != "*" and action not in guim.ACTION_WHITELIST:
            _err("night.allow", "未登记动作：%s" % action)
    return data


def load_policy(path=None, must_exist=True):
    path = Path(path or POLICY_PATH)
    if not path.exists():
        if must_exist:
            raise PolicyError("策略文件不存在：%s" % path)
        data = {"version": 1, "models": DEFAULT_ROUTES, "apps": {},
                "risk": DEFAULT_RISK,
                "night": {"window": guim.NIGHT_WINDOW_DEFAULT, "allow": []}}
        return validate_policy(json.loads(json.dumps(data)))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PolicyError("策略文件不是合法 JSON：%s" % e)
    return validate_policy(data)


def meta(path=None):
    """会话审计用：策略版本与内容哈希。"""
    path = Path(path or POLICY_PATH)
    raw = path.read_bytes() if path.exists() else b""
    return {"path": str(path), "version": 1,
            "sha256": hashlib.sha256(raw).hexdigest()[:16] if raw else "",
            "loaded_at": dt.datetime.now().astimezone().isoformat(
                timespec="seconds")}


def _cfg(con, key):
    if con is None:
        return None
    row = con.execute("SELECT value FROM config_kv WHERE key = ?",
                      (key,)).fetchone()
    return row["value"] if row else None


def explain(app, action, con=None, policy=None):
    """输出生效值及其来源（config_kv > 策略文件 > 代码默认）。"""
    if action not in guim.ACTION_WHITELIST:
        raise PolicyError("未登记动作：%s" % action)
    policy = policy or load_policy()
    out = {"app": app, "action": action, "policy": meta()}

    override = _cfg(con, "policy.gui.apps.%s.backend" % app)
    if override:
        if override not in BACKENDS:
            raise PolicyError("config_kv 通道非法：%s" % override)
        out["backend"] = {"value": override, "source": "config_kv"}
    elif app in policy["apps"]:
        out["backend"] = {"value": policy["apps"][app]["backend"],
                          "source": "policy.apps"}
    else:
        out["backend"] = {"value": "peekaboo", "source": "default"}

    override = _cfg(con, "policy.gui.risk.%s" % action)
    if override:
        if override not in RISK_LEVELS:
            raise PolicyError("config_kv 风险等级非法：%s" % override)
        out["risk"] = {"value": override, "source": "config_kv"}
    elif action in policy["risk"]:
        out["risk"] = {"value": policy["risk"][action], "source": "policy.risk"}
    else:
        out["risk"] = {"value": DEFAULT_RISK.get(action, "A0"),
                       "source": "default"}

    window = _cfg(con, "policy.gui.night.window")
    raw_allow = _cfg(con, "policy.gui.night.allow")
    if window:
        out["night"] = {
            "window": {"value": window, "source": "config_kv"},
            "allow": {"value": json.loads(raw_allow) if raw_allow else [],
                      "source": "config_kv"}}
    else:
        out["night"] = {
            "window": {"value": policy["night"]["window"],
                       "source": "policy.night"},
            "allow": {"value": policy["night"]["allow"],
                      "source": "policy.night"}}
    return out


def select(purpose, con=None, sensitivity="normal", health=None,
           policy=None):
    """模型选择器：返回 {purpose, model, chain, reason, health}。"""
    if purpose not in PURPOSES:
        raise PolicyError("未知用途：%s（可选 %s）"
                          % (purpose, "/".join(PURPOSES)))
    policy = policy or load_policy()
    route = policy["models"].get(purpose) or DEFAULT_ROUTES[purpose]
    chain = [route["primary"]] + list(route["fallback"])
    origin = "policy"

    override = _cfg(con, "llm.route.%s" % purpose)
    if override and not override.startswith("{"):
        chain = [override] + [m for m in chain if m != override]
        origin = "config_kv"

    reasons = ["%s 首选 %s" % (origin, chain[0])]
    if sensitivity == "confidential":
        local = [m for m in chain
                 if m != "rules" and not m.startswith("remote:")]
        dropped = len(chain) - len(local)
        chain = (local or ["qwen3:1.7b"]) + ["rules"]
        chain = list(dict.fromkeys(chain))
        if dropped:
            reasons.append("机密内容移除 %d 个远程项" % dropped)

    health = health or llm_local.health(timeout=2)
    for model in chain:
        if model == "rules":
            return {"purpose": purpose, "model": "rules:local", "chain": chain,
                    "reason": "；".join(reasons + ["降级到规则判定"]),
                    "health": {"ok": health["ok"], "error": health["error"]}}
        if model.startswith("remote:"):
            return {"purpose": purpose, "model": model, "chain": chain,
                    "reason": "；".join(reasons + ["远程模型无需本地健康检查"]),
                    "health": {"ok": health["ok"], "error": health["error"]}}
        if health["ok"] and llm_local.installed(model, health["models"]):
            return {"purpose": purpose, "model": model, "chain": chain,
                    "reason": "；".join(reasons + ["本地已就绪"]),
                    "health": {"ok": True, "error": ""}}
        reasons.append("%s 不可用（%s）"
                       % (model, "未装" if health["ok"] else "Ollama 不可达"))
    if "rules" in chain:
        return {"purpose": purpose, "model": "rules:local", "chain": chain,
                "reason": "；".join(reasons + ["链尾规则兜底"]),
                "health": {"ok": health["ok"], "error": health["error"]}}
    return {"purpose": purpose, "model": "", "chain": chain,
            "reason": "；".join(reasons + ["无可用模型，且未配置 rules 兜底"]),
            "health": {"ok": health["ok"], "error": health["error"]}}


def list_models(con=None, health=None, policy=None):
    policy = policy or load_policy()
    health = health or llm_local.health(timeout=2)
    rows = []
    for purpose in PURPOSES:
        route = policy["models"].get(purpose) or DEFAULT_ROUTES[purpose]
        chain = [route["primary"]] + list(route["fallback"])
        marks = []
        for model in chain:
            if model == "rules":
                marks.append("rules=规则")
            elif model.startswith("remote:"):
                marks.append("%s=远程" % model)
            elif health["ok"] and llm_local.installed(model, health["models"]):
                marks.append("%s=已装" % model)
            else:
                marks.append("%s=%s"
                             % (model, "未装" if health["ok"] else "不可达"))
        rows.append({"purpose": purpose, "chain": chain,
                     "status": "，".join(marks)})
    return {"url": health["url"], "ok": health["ok"],
            "error": health["error"], "models": health["models"],
            "routes": rows, "policy": meta()}
