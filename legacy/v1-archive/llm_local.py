#!/usr/bin/env python3
"""llm_local.py — 本地模型（P3 · T-0027）：Ollama 直连，不引 SDK。

约束（方案 5.11）：
  1. 开跑前 `GET /api/tags` 健康检查；缺模型直接报错，不静默换模型。
  2. 只做判断与兜底；内容质量交给远程模型或豆包。
  3. 机密内容默认本地处理，转发远程必须先脱敏。
  4. 计量写 `llm.local.<日期>`，只记次数与耗时，不折算 token。
  5. 超时短：交互 5-8s，规划 60s；Ollama 单实例串行。

环境变量：OLLAMA_URL（默认 http://127.0.0.1:11434）
"""

import datetime as dt
import json
import os
import urllib.error
import urllib.request

OLLAMA_URL = (os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")


class LocalLLMError(Exception):
    """本地模型可预期错误：缺模型、不可达、返回异常。"""


def _fetch_json(path, payload=None, timeout=5):
    url = OLLAMA_URL + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise LocalLLMError("Ollama HTTP %s：%s" % (e.code, detail))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise LocalLLMError("Ollama 不可达（%s）：%s" % (OLLAMA_URL, e))


def parse_models(payload):
    names = []
    for item in payload.get("models") or []:
        name = item.get("name") or item.get("model") or ""
        if name:
            names.append(name)
    return names


def health(timeout=2, fetch=None):
    """返回 {ok, url, models, ms, error}；不可达不算异常。"""
    fetch = fetch or _fetch_json
    started = dt.datetime.now()
    try:
        payload = fetch("/api/tags", None, timeout)
        models = parse_models(payload)
        ok, error = True, ""
    except LocalLLMError as e:
        models, ok, error = [], False, str(e)
    ms = int((dt.datetime.now() - started).total_seconds() * 1000)
    return {"ok": ok, "url": OLLAMA_URL, "models": models, "ms": ms,
            "error": error}


def installed(model, models):
    if model in models:
        return True
    return any(m.split(":")[0] == model.split(":")[0]
               and (":" not in model or m == model) for m in models)


def chat(model, messages, fmt=None, timeout=60, fetch=None):
    """调用 /api/chat；模型未装则报错，不擅自替换。"""
    fetch = fetch or _fetch_json
    info = health(timeout=3, fetch=fetch)
    if not info["ok"]:
        raise LocalLLMError(info["error"])
    if not installed(model, info["models"]):
        raise LocalLLMError("模型未安装：%s（现有 %s）；按配置不静默换模型"
                            % (model, "、".join(info["models"]) or "无"))
    payload = {"model": model, "messages": messages, "stream": False}
    if fmt:
        payload["format"] = fmt
    started = dt.datetime.now()
    data = fetch("/api/chat", payload, timeout)
    ms = int((dt.datetime.now() - started).total_seconds() * 1000)
    message = data.get("message") or {}
    text = message.get("content") or ""
    if not text:
        raise LocalLLMError("Ollama 返回空内容（model=%s）" % model)
    return {"ok": True, "model": model, "text": text, "ms": ms,
            "done_reason": data.get("done_reason") or ""}


def embed(model, text, timeout=10, fetch=None):
    fetch = fetch or _fetch_json
    info = health(timeout=3, fetch=fetch)
    if not info["ok"]:
        raise LocalLLMError(info["error"])
    if not installed(model, info["models"]):
        raise LocalLLMError("模型未安装：%s" % model)
    started = dt.datetime.now()
    data = fetch("/api/embed", {"model": model, "input": text}, timeout)
    vectors = data.get("embeddings") or []
    if not vectors:
        raise LocalLLMError("Ollama 未返回向量（model=%s）" % model)
    ms = int((dt.datetime.now() - started).total_seconds() * 1000)
    return {"ok": True, "model": model, "vector": vectors[0], "ms": ms}


def meter(con, kind, ms, model, day=None):
    """累加 `llm.local.<日期>`：{"calls":n,"ms":n,"kinds":{...}}。"""
    day = day or dt.date.today().isoformat()
    key = "llm.local." + day
    row = con.execute("SELECT value FROM config_kv WHERE key = ?",
                      (key,)).fetchone()
    data = {"calls": 0, "ms": 0, "kinds": {}}
    if row and row["value"]:
        try:
            data.update(json.loads(row["value"]))
        except json.JSONDecodeError:
            pass
    data["calls"] += 1
    data["ms"] += int(ms)
    bucket = data["kinds"].setdefault(kind, {"calls": 0, "ms": 0})
    bucket["calls"] += 1
    bucket["ms"] += int(ms)
    data.setdefault("models", {})
    data["models"][model] = data["models"].get(model, 0) + 1
    con.execute("INSERT OR REPLACE INTO config_kv(key, value) VALUES (?, ?)",
                (key, json.dumps(data, ensure_ascii=False)))
    con.commit()
    return data
