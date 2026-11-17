#!/usr/bin/env python3
"""ratelimit.py — 按 endpoint 的本地限流（token 预算 + 请求速率 + 并发闸门）

边界不是拍脑袋，来自 2026-11-12 对 token plan 的递进实测（见 agency/limits.json）：
- sensenova-6.8-flash-lite：2k 请求 1s 间隔连发 6 次全通；并发 4 全通、并发 8 有 3/8 被拒；
  单请求 24k/48k prompt 均 200 → 单请求上限按 32k 记。
- deepseek-v4-flash：冷却 180s 后仍"首次 200、后续 429"，按 tpm 4k / rpm 4 / 并发 1 保守限流。
- glm-5.2：24k prompt 通过、36k 起 429，按 24k 上限、tpm 10k / rpm 10。
- sensenova-u1.5-lite：目录里有但 404，标记不可调用。

上层只调 slot()/estimate_tokens()，换策略不改调用方。
"""

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIMITS_PATH = Path(os.environ.get("AGENCY_LIMITS_FILE", ROOT / "agency" / "limits.json"))
WINDOW = 60.0  # 滑动窗口：实测 65s 后同规模请求恢复
DEFAULT = {"callable": True, "max_prompt_tokens": 32000, "tpm": 6000,
           "rpm": 6, "concurrency": 2, "recovery_s": 60}


class PromptTooLong(Exception):
    """提示词超过该模型单请求上限，需要拆分任务而不是硬打出去。"""


def load_limits():
    try:
        data = json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("models", {})


def load_role_prefs():
    """角色 → 首选模型顺序（实测稳定性决定，见 limits.json 的 roles 段）。"""
    try:
        data = json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("roles", {})


LIMITS = load_limits()
ROLE_PREFS = load_role_prefs()
_LOCKS = {}
_GUARD = threading.Lock()


def estimate_tokens(text):
    """粗估：中文 ~1.5 字/token，英文数字 ~4 字符/token。"""
    if not text:
        return 1
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return int(cjk / 1.5 + (len(text) - cjk) / 4) + 1


def conf(model):
    item = dict(DEFAULT)
    item.update(LIMITS.get(model) or {})
    return item


def callable_models(models):
    """过滤掉实测不可调用的模型（例如 sensenova-u1.5-lite 恒 404）。"""
    out = [m for m in models if conf(m).get("callable", True)]
    return out or models


def role_models(role, models):
    """按角色偏好重排候选：稳定模型在前，其余按原序兜底。

    正确搭配优先于限流余量——pm/auditor 在 flash-lite 上会被判复读退化，
    即使 flash-lite 配额更宽，也该先走 glm-5.2。
    """
    usable = callable_models(models)
    # 实测在该角色上会退化的模型直接排除（正确搭配优先于限流余量）
    usable = [m for m in usable if role not in (conf(m).get("avoid_roles") or [])]
    prefs = [m for m in ROLE_PREFS.get(role, []) if m in usable]
    return prefs + [m for m in usable if m not in prefs]


def _bucket(model):
    with _GUARD:
        if model not in _LOCKS:
            c = conf(model)
            _LOCKS[model] = {
                "sem": threading.Semaphore(max(1, int(c["concurrency"]))),
                "lock": threading.Lock(),
                "events": [],  # [(monotonic, tokens)]
                "tpm": c["tpm"],
                "rpm": c["rpm"],
            }
        return _LOCKS[model]


def _wait_needed(bucket, est, now):
    """返回还需要等多少秒；0 表示现在就可以发。"""
    bucket["events"] = [e for e in bucket["events"] if now - e[0] < WINDOW]
    events = sorted(bucket["events"])
    used = sum(t for _, t in events)
    if used + est <= bucket["tpm"] and len(events) + 1 <= bucket["rpm"]:
        return 0.0
    # 最早的若干条滑出窗口后，token 与请求数同时回到预算内
    drop_tokens, wait = 0, WINDOW
    for idx, (ts, tok) in enumerate(events):
        drop_tokens += tok
        if used - drop_tokens + est <= bucket["tpm"] and len(events) - idx <= bucket["rpm"]:
            wait = WINDOW - (now - ts) + 0.5
            break
    return max(0.5, wait)


@contextmanager
def slot(model, est):
    """占用一个请求槽：超预算先排队等窗口，超并发先拿信号量。"""
    c = conf(model)
    if est > int(c["max_prompt_tokens"]):
        raise PromptTooLong(
            f"提示词约 {est} token，超过 {model} 单请求上限 "
            f"{c['max_prompt_tokens']}，请拆分任务后再跑")
    if est > int(c["tpm"]):
        # 单请求就吃掉整个窗口预算，等多久都发不出去，必须换模型或拆分
        raise PromptTooLong(
            f"提示词约 {est} token，超过 {model} 单分钟预算 "
            f"{c['tpm']}，排队也无法发出，请拆分任务或换模型")
    bucket = _bucket(model)
    bucket["sem"].acquire()
    try:
        while True:
            with bucket["lock"]:
                wait = _wait_needed(bucket, est, time.monotonic())
                if wait <= 0:
                    bucket["events"].append((time.monotonic(), est))
                    break
            print(f"[限流] {model} 本分钟预算不足，等待 {wait:.0f}s 后再发（实测窗口 60s）")
            time.sleep(min(wait, 30))
        yield
    finally:
        bucket["sem"].release()


def record(model, actual_tokens):
    """用真实用量修正估算，避免长期高估/低估。"""
    if not actual_tokens:
        return
    bucket = _bucket(model)
    with bucket["lock"]:
        if bucket["events"]:
            ts, _ = bucket["events"][-1]
            bucket["events"][-1] = (ts, actual_tokens)
