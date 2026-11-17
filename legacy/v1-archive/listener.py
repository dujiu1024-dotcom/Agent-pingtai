#!/usr/bin/env python3
"""listener.py — 飞书长连接监听（P1）

作用：让机器人长耳朵。普通消息回执并留档；指令消息交给
`feishu_commands.py` 解析执行（/help、/brief、/sync、/run）。

依赖：lark-oapi（只装在 .venv-listener 里，主程序仍零依赖）。
启动：.venv-listener/bin/python listener.py

前提：开发者后台 → 事件与回调 → 订阅方式选「长连接」，
      并订阅事件「接收消息 im.message.receive_v1」。
"""

import datetime as dt
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from contextvars import ContextVar
from pathlib import Path

# 当前正在处理的飞书事件 ID（用于出站去重）
_CUR_EVENT = ContextVar("fde_event_id", default="")

_EARLY_ROOT = Path(__file__).resolve().parent
if os.environ.get("AGENCY_ENV", "prod").lower() == "dev":
    os.environ.setdefault("AGENCY_DB",
                          str(_EARLY_ROOT / "agency" / "dev-state" / "agency.db"))

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

import boss
import conversation
import db as dbm
import feishu_commands as fc
import intent

ROOT = Path(__file__).resolve().parent
_DATA_DIR = (Path(os.environ["AGENCY_DB"]).parent if os.environ.get("AGENCY_DB")
             else ROOT / "agency" / "state")
LOG = _DATA_DIR / "inbound.jsonl"

sys.path.insert(0, str(ROOT))
import feishu_bot  # noqa: E402
import feishu_commands  # noqa: E402

# 可用角色清单，意图识别时让 LLM 从中挑
try:
    import db as dbm  # noqa: E402
    ROLES = list(dbm.ROLE) if hasattr(dbm, "ROLE") else []
except Exception:  # noqa: BLE001
    ROLES = []

# 幂等去重：同一 message_id 只处理一次；超过 500 条自动清理最旧的
_SEEN = OrderedDict()
_SEEN_LOCK = threading.Lock()
_SEEN_MAX = 500
_SEEN_TTL = 300  # 秒


def _dedup_check(message_id):
    """返回 True 表示首次处理；False 表示重投应跳过。

    去重键落 SQLite（feishu_seen），进程重启后仍能识别同一 message_id；
    数据库不可用时退回进程内 _SEEN，保证监听不会因存储故障中断。
    """
    if not message_id:
        return True
    dedup_key = message_id  # 与飞书事件 message_id 一致，落库为唯一键
    now = time.time()
    try:
        con = dbm.connect()
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS feishu_seen("
                " message_id TEXT PRIMARY KEY, seen_at REAL NOT NULL)")
            # 只清理 7 天前的旧记录：飞书重投可能晚到几十分钟，
            # 若在判定前就清掉，晚到的同一条消息会被当成新消息重复执行。
            con.execute("DELETE FROM feishu_seen WHERE seen_at < ?",
                        (now - 7 * 86400,))
            cur = con.execute(
                "INSERT OR IGNORE INTO feishu_seen(message_id, seen_at)"
                " VALUES (?, ?)", (dedup_key, now))
            con.commit()
            return cur.rowcount == 1
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — 落库失败时退回内存，不让监听停摆
        print("[去重降级] 落库失败，改用内存：%s" % exc, flush=True)
    with _SEEN_LOCK:
        if message_id in _SEEN:
            return False
        _SEEN[message_id] = now
        while len(_SEEN) > _SEEN_MAX:
            _SEEN.popitem(last=False)
        return True


def load_env():
    paths = [ROOT / ".env"]
    if os.environ.get("AGENCY_ENV", "prod").lower() == "prod":
        paths.append(ROOT / ".env.prod")
    else:
        paths.append(ROOT / (".env." + os.environ.get("AGENCY_ENV", "dev")))
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            # dev 的飞书凭据只能来自 .env.dev，绝不继承 prod 应用
            if path.name == ".env" and os.environ.get("AGENCY_ENV", "prod") != "prod" \
                    and k.startswith("FEISHU_"):
                continue
            if v.strip():
                os.environ[k] = v.strip()


load_env()
APP_ENV = os.environ.get("AGENCY_ENV", "prod").lower() or "prod"
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")


def text_of(message):
    try:
        return json.loads(message.content or "{}").get("text", "")
    except json.JSONDecodeError:
        return ""


def record(kind, payload):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "type": kind, **payload}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


_LAST_REPLY = {}
_RECENT_SENT = {}
DUP_WINDOW = 600


def _norm_hash(text):
    t = boss.inner(str(text or ""))
    t = re.sub(r"[\s\u3000]+", "", t)
    t = re.sub(r"[。！？，、；：·\-—]+", "", t)
    return hashlib.sha1(t.encode("utf-8")).hexdigest()


def _is_recent_dup(chat_id, body):
    h = _norm_hash(body)
    now = time.time()
    rec = [(x, ts) for x, ts in _RECENT_SENT.get(chat_id, []) if now - ts < DUP_WINDOW]
    if any(x == h for x, _ in rec):
        _RECENT_SENT[chat_id] = rec
        return True
    rec.append((h, now))
    _RECENT_SENT[chat_id] = rec
    return False


def _dedupe_with_last(body, chat_id):
    """同一句话不在两条回复里各出现一次。"""
    prev = _LAST_REPLY.get(chat_id, "")
    if not prev:
        return body
    prev_sents = {s.strip() for s in re.split(r"[。！？\n]", prev) if len(s.strip()) >= 8}
    kept = []
    for ln in body.splitlines():
        st = ln.strip()
        if len(st) >= 8 and st in prev_sents:
            continue
        kept.append(ln)
    return "\n".join(kept).strip()


def _cap_lines(text, limit=8):
    """任何自动回复不超过 limit 行；超了先发主答案，再问一句要不要展开。"""
    lines = str(text).splitlines()
    if len(lines) <= limit:
        return str(text)
    return "\n".join(lines[:limit - 1]) + "\n要不要展开？"


def _strip_section_titles(text):
    """去掉面向用户正文里的段落标题：只含【标题】/—— 标题 ——/[分类] 的行。"""
    kept = []
    for ln in str(text or "").splitlines():
        s = ln.strip()
        if re.match(r"^【[^】]{1,16}】$", s):
            continue
        if re.match(r"^[—-]{2,}\s*.+\s*[—-]{2,}$", s):
            continue
        if re.match(r"^\[[^\]]{1,12}\]$", s):
            continue
        if not s and kept and not kept[-1].strip():
            continue
        kept.append(ln)
    return "\n".join(kept).strip()


def send(text, chat_id, event_id=None, kind="", sender="系统通知", cap=True):
    if event_id is None:
        event_id = _CUR_EVENT.get("")
    body = _strip_section_titles(str(text or "")).strip()
    # 正文为空或只剩标点：一律不发，宁可少一条也不发壳子
    if not re.search(r"[\w\u4e00-\u9fff]", boss.inner(body)):
        record("dropped", {"chat_id": chat_id, "event_id": event_id,
                           "text": str(text)[:200]})
        print("[不发] 正文为空", flush=True)
        return None
    if boss.has_label_chain(body):
        fixed = boss.dechain(body)
        record("dechained", {"chat_id": chat_id, "event_id": event_id,
                             "from": body[:200], "to": fixed[:200]})
        body = fixed
    if kind in ("qa", "final") and chat_id:
        deduped = _dedupe_with_last(body, chat_id)
        if deduped:
            body = deduped
        # 既定选择（创始人 2026-11-13 拍板）：只有拍板类消息豁免 6–8 行上限，
        # 且一件都不能丢、四段写满；汇报、系统通知、help 仍然受限，
        # help 用分页控制（feishu_commands.help_page），不能借这个豁免变无上限。
        if cap and not boss.looks_like_decision(body):
            body = _cap_lines(body, 8)
        if _is_recent_dup(chat_id, body):
            record("dedup", {"chat_id": chat_id, "event_id": event_id,
                             "text": body[:200]})
            print("[不发] 与最近一条重复", flush=True)
            return None
    if event_id and not kind:
        # 按文本类别归类：同一事件同一类只发一次
        if body.startswith(boss.GREETING) and "收到" in body:
            kind = "ack_intent"
        elif body.startswith(boss.GREETING):
            kind = "ack_start" if ("结果" in body or "结论" in body) else "final"
        else:
            kind = "final"
    if kind in ("qa", "final"):
        fixed, problems = boss.ensure(body, sender)
        if not fixed.strip():
            # 拆不出编号列表：不发半成品，留档后改发一句可重跑的提示
            record("unformatted", {"chat_id": chat_id, "event_id": event_id,
                                   "problems": problems, "text": body[:400]})
            print("[退回] 未能整理成编号列表：%s" % "；".join(problems), flush=True)
            kind = "fallback"
            body = boss.FALLBACK_TEXT
        elif problems:
            record("refused", {"chat_id": chat_id, "event_id": event_id,
                               "problems": problems, "text": body[:400]})
            print("[拒发] 版式校验未通过：%s" % "；".join(problems), flush=True)
            return None
        else:
            body = fixed
        body = boss.wrap(body, sender)
    else:
        body = boss.wrap(body, sender)
    # 出站去重：同一事件 + 同一类型只发一次（落库，重启后仍有效）
    if event_id and kind and not dbm.claim_outbound(event_id, kind, chat_id):
        print("[重复发送已拦下] %s/%s" % (event_id, kind), flush=True)
        return None
    try:
        data = feishu_bot.Bot().send_text(body, receive_id=chat_id, id_type="chat_id")
        if event_id and kind:
            dbm.mark_outbound(event_id, kind, (data or {}).get("message_id", ""))
        record("reply", {"chat_id": chat_id, "text": str(body)[:400], "event_id": event_id,
                         "sender": boss.label(sender), "kind": kind, "ok": True,
                         "message_id": (data or {}).get("message_id", "")})
        if kind in ("qa", "final"):
            _LAST_REPLY[chat_id] = boss.inner(str(body))
        return data
    except Exception as e:  # noqa: BLE001
        print("[回信失败]", e, flush=True)
        record("reply", {"chat_id": chat_id, "text": str(text)[:400],
                         "ok": False, "error": str(e)})
        try:
            from alerts import local_alert
            local_alert("飞书回信失败", str(e))
        except Exception:  # noqa: BLE001
            pass


def sender_allowed(open_id):
    """白名单必须在校验入口先跑：不在名单里的人不回复、不调 LLM。"""
    return feishu_commands.is_allowed(open_id)


def _strip_cli_header(text):
    """去掉指令子进程回显的「✅ agency.py xxx」头，只留正文。"""
    lines = [ln for ln in str(text or "").splitlines()
             if not re.match(r"^[✅❌]\s*agency\.py\b", ln.strip())]
    return "\n".join(lines).strip()


ROLE_WORDS = ("agent", "角色", "助理", "秘书", "幕僚长", "教练", "人事", "财务")
# 自我介绍的分组口径：按「能帮你干什么」归类，不按组织架构铺开
INTRO_GROUPS = (
    ("接活派活的", ("chief-of-staff", "planner", "producer")),
    ("出方案的", ("pm",)),
    ("写内容的", ("content",)),
    ("找客户的", ("scout",)),
    ("算钱的", ("finance",)),
    ("把关的", ("auditor",)),
    ("招人的", ("hr",)),
    ("教方法的", ("coach",)),
    ("盯计划的", ("secretary",)),
)


ROLE_ALIASES = {
    "hr": ["hr", "人事"],
    "pm": ["pm", "产品经理"],
    "finance": ["cfo", "财务"],
    "chief-of-staff": ["cos", "幕僚长"],
    "planner": ["规划师"],
    "producer": ["制作人"],
    "content": ["内容"],
    "scout": ["商机侦察", "bd"],
    "auditor": ["审计"],
    "coach": ["教练"],
    "secretary": ["秘书", "助理"],
}

ACTION_RE = re.compile(
    r"(审查|检查|看看|看一下|查一下|整理|写|做个|做一下|跟进|盘一下|盘|评估|诊断|拆|排期|"
    r"处理|分析|改|优化|核对|确认|跑|安排|盯|review|check|write|plan|analyze)", re.I)
PURE_Q_RE = re.compile(r"(是干嘛的|是干什么的|干嘛的|做什么的|负责什么|管什么|是谁|都有谁|"
                       r"有哪些|有没有|在不在|是哪个)")
OWNER_WORDS = ("ceo", "老板", "创始人")


def _find_roles(text):
    low = (text or "").lower()
    hits = []
    for card in feishu_commands.agent_cards():
        role = card["role"]
        keys = list(ROLE_ALIASES.get(role, []))
        keys += [role, card["name"].split(" Agent")[0].strip()]
        for k in keys:
            if not k:
                continue
            if re.fullmatch(r"[a-z]+", k):
                if re.search(r"\b%s\b" % re.escape(k), low) and role not in hits:
                    hits.append(role)
            elif k in low and role not in hits:
                hits.append(role)
    return hits


def _is_owner_ref(text):
    low = (text or "").lower()
    return any(w in low for w in OWNER_WORDS)


def _is_role_question(text):
    """只有纯疑问才介绍角色；带动作的一律当派活。"""
    if not _find_roles(text) or ACTION_RE.search(text or ""):
        return False
    return bool(PURE_Q_RE.search(text or "") or re.search(r"[?？]", text or ""))


def _dispatch_target(text):
    """角色名 + 动作 → (role, 去掉称呼后的动作描述)；CEO/老板本人不派。"""
    if not ACTION_RE.search(text or ""):
        return "", ""
    if _is_owner_ref(text) and not any(r for r in _find_roles(text)):
        return "", ""
    roles = _find_roles(text)
    if len(roles) != 1:
        return "", ""
    act = text or ""
    for k in ROLE_ALIASES.get(roles[0], []) + [roles[0]]:
        act = re.sub(r"\b%s\b" % re.escape(k) if re.fullmatch(r"[a-z]+", k or "x") else re.escape(k),
                     " ", act, flags=re.I)
    act = re.sub(r"^(让|请|叫|麻烦|帮我|帮忙)\s*", "", act.strip())
    act = re.sub(r"\s+", " ", act).strip(" ，。")
    return roles[0], act


BULK_RE = re.compile(r"(所有|全部|全量|全员|每位|每个|逐个|所有人)")


def _bulk_hint(act):
    """全量类活先拆批，避免一次写不完撞上限。"""
    if not BULK_RE.search(act or ""):
        return "", ""
    return ("这件事量大，我按 5 人一批，先跑第一批；",
            "只审查前 5 名员工，每人一句话结论，不要展开分析；做完停下。")


def _card_names():
    return {c["role"]: c["name"].split(" Agent")[0].strip()
            for c in feishu_commands.agent_cards()}


def _role_overview():
    """一句话说清有多少人、分几类、能干什么；不铺职责。"""
    names = _card_names()
    parts = []
    for phrase, roles in INTRO_GROUPS:
        who = [names[r] for r in roles if r in names]
        if who:
            parts.append("%s（%s）" % (phrase, "、".join(who)))
    return ("我这边是 %d 个角色：%s。你想干什么，直接说一句就行，我自己分。"
            % (len(names), "、".join(parts)))


def _plain(text):
    """洗掉角色卡里的模板腔（反引号、事件名、箭头），只留能直接读的话。"""
    return feishu_commands.plain_text(text)


def _role_detail(card):
    """点名某个角色：两行——管什么、什么时候用。"""
    name = card["name"].split(" Agent")[0].strip()
    duty = _plain((card.get("duties") or [card.get("identity") or ""])[0])
    duty = re.sub(r"^([^：]{2,14})：", r"\1，", duty)
    when = _plain((card.get("triggers") or [""])[0])
    text = "%s：%s。" % (name, duty)
    if when:
        text += "\n什么时候用：%s。" % when
    return text


def _qa_answer(text=""):
    """能干什么 / 都有谁 → 同一条短答；只有点名某个角色才展开他一个。"""
    low = (text or "").lower()
    hits = []
    for card in feishu_commands.agent_cards():
        name = card["name"].split(" Agent")[0].strip()
        keys = [card["role"].lower(), name.lower()]
        if card["role"] == "secretary":
            keys.append("助理")
        if any(k and k in low for k in keys):
            hits.append(card)
    if len(hits) == 1:
        return _role_detail(hits[0])
    return _role_overview()


def _brief_text(raw):
    """把 brief 输出整理成一条可直接发的态势说明，不留命令行壳。"""
    lines = [ln for ln in _strip_cli_header(raw).splitlines() if ln.strip()]
    if not lines:
        return "今天没有需要盯的事。"
    if lines[0].startswith("【态势】"):
        day = lines[0].replace("【态势】", "").strip()
        lines[0] = ("今天态势（%s）：" % day) if day else "今天态势："
    return "\n".join(lines)


# ---------- LLM 意图识别 ----------

_LLM_CLIENT = None
_LLM_LOCK = threading.Lock()


def _get_llm():
    """懒加载一个 OpenAI 兼容客户端（跟 agency.py 同一套 env 配置）。"""
    global _LLM_CLIENT
    if _LLM_CLIENT is not None:
        return _LLM_CLIENT
    with _LLM_LOCK:
        if _LLM_CLIENT is not None:
            return _LLM_CLIENT
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            return None
        base = os.environ.get("OPENAI_BASE_URL",
                              "https://api.deepseek.com/v1").rstrip("/")
        model = os.environ.get("AGENCY_MODEL", "deepseek-chat")
        _LLM_CLIENT = {"key": key, "base": base, "model": model}
        return _LLM_CLIENT


def _log_llm(model, prompt, usage, ok=True, error=None):
    """把意图识别/自由问答的 token 计入 agent_runs，纳入当日预算。"""
    try:
        con = dbm.connect()
        try:
            con.execute(
                "INSERT INTO agent_runs(role, task_id, model, prompt_hash,"
                " output_path, tokens_in, tokens_out, ok, error, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("qa", None, model, dbm.content_hash(prompt), "",
                 int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                 int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                 1 if ok else 0, error, dbm.now()))
            con.commit()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 — 记账失败不影响回复
        print(f"[记账失败] {exc}", flush=True)


def _llm_chat(prompt, timeout=30, model=None):
    """极简 OpenAI 兼容调用，返回文本；失败返回 None。token 记入 agent_runs。"""
    cli = _get_llm()
    if not cli:
        return None
    model = model or cli["model"]
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 500,
    }).encode()
    req = urllib.request.Request(
        cli["base"] + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cli["key"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"].get("content", "")
        _log_llm(model, prompt, data.get("usage") or {})
        return text
    except Exception as e:  # noqa: BLE001 — 意图识别挂了就降级
        print(f"[意图识别 LLM 失败] {e}", flush=True)
        return None


INTENT_PROMPT = """你是一个意图路由器。把用户的自然语言消息分类，输出严格 JSON，不要别的内容。

角色清单：%s

分类规则：
- brief  → 用户想知道态势/进度/状态（项目咋样、看看现在、啥情况、任务状态、今日概览）
- advice → 用户在求建议/判断方向（怎么开展、方向对不对、接下来怎么打、该不该、要不要），不是派活；role 选 chief-of-staff 或 planner。
- run    → 用户明确让某个 Agent 干活（让 pm 做、叫 planner 拆、派给 auditor）。从角色清单挑最合适的，不确定就用 chief-of-staff。note 字段写原文。
- plan   → 用户明确要求把目标拆成任务入库（做个计划、拆一下、排期、规划）
- task   → 用户明确要建一条单任务（加个任务、新增一条、建个 todo）。owner 从角色清单挑。
- sync   → 用户想推进事件/消息/通知（同步一下、推进一轮、投事件）
- help   → 用户想知道能用什么（你能做啥、怎么用、help）
- chat   → 闲聊或超出能力范围（天气、夸你、再见），reply 字段给礼貌简短回答。
- unknown→ 实在拿不准，reply 字段反问一句用户想干啥。

严格输出 JSON：
{"intent": "brief|advice|run|plan|task|sync|help|chat|unknown",
 "role": "角色名(仅 run/task 时需要)",
 "note": "给 run 的附加说明(可选)",
 "title": "task 标题(仅 task 时需要)",
 "goal": "plan 目标(仅 plan 时需要)",
 "reply": "chat/unknown 时的回答"}

用户消息：""" % (", ".join(ROLES) if ROLES else "(暂无角色)")


def _intent_fallback(text):
    """关键词降级统一走 intent.fallback_message，避免两套词表漂移。"""
    return intent.fallback_message(text)


def intent_classify(text, context=""):
    """意图识别主入口。返回 dict 带 intent 字段；LLM 失败走关键词降级。"""
    if not text or not text.strip():
        return {"intent": "chat", "reply": "👌"}
    if not ROLES:
        # 角色表都没有，只能降级
        return _intent_fallback(text)

    prompt = INTENT_PROMPT + text
    if context:
        prompt = INTENT_PROMPT + "最近对话（用于理解代词和追问）：\n" + context + "\n\n用户消息：" + text
    raw = _llm_chat(prompt, model=_qa_model())
    if not raw:
        return _intent_fallback(text)

    # 从 LLM 输出里捞 JSON（有时候会带 markdown 包裹）
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 尝试找第一个 { 到最后一个 }
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start:end])
            except json.JSONDecodeError:
                return _intent_fallback(text)
        else:
            return _intent_fallback(text)

    intent = data.get("intent", "unknown")
    if intent not in ("brief", "advice", "run", "plan", "task", "sync",
                      "help", "chat", "unknown"):
        return _intent_fallback(text)
    # run/task 的角色必须在 ROLES 里
    if intent in ("run", "task"):
        role = data.get("role", "")
        if role not in ROLES:
            data["role"] = ROLES[0] if ROLES else ""
    return data


def run_command(text, open_id, chat_id, event_id=""):
    try:
        # 慢命令（run/plan/dispatch/roundtable）由子进程自己回终态，这里不重复回
        reply = feishu_commands.handle(text, open_id, reply_chat=chat_id,
                                       reply_id=event_id)
    except Exception as e:  # noqa: BLE001 — 指令执行失败也要回执
        reply = "指令执行失败：%s" % (str(e) or e.__class__.__name__)
    if reply:
        body = _strip_cli_header(str(reply))
        cmd = (feishu_commands.parse(text) or {}).get("cmd", "")
        # 帮助与态势是内容，不是系统状态：整段发，标明是谁在说
        # 帮助已分页（feishu_commands.help_page），所以这里仍走 8 行闸门
        if body and cmd in ("/help", "/brief"):
            if cmd == "/brief":
                body = _brief_text(body)
            else:
                body = re.sub(r"^【我们公司】\s*\n+", "", body)
            send(body, chat_id, event_id, "qa", sender="chief-of-staff")
            return body
        if not body.startswith("【"):
            failed = bool(re.search(r"(❌|失败|出错|错误|不存在|超时)", body))
            reply = boss.from_error(body) if failed else boss.format_system([body])
        else:
            reply = body
        send(reply, chat_id, event_id, "final", sender="系统通知")


# 慢命令的预计耗时，先回一条"已开始"，结束后由子进程补结果
SLOW_ACK = {
    "/run": "正在办，约 1–2 分钟出结果。",
    "/plan": "开始拆解目标，约 1 分钟出结果。",
    "/dispatch": "开始派活，约 1–3 分钟出结果。",
    "/roundtable": "圆桌会开始，约 3–5 分钟出结论。",
}


QA_PROMPT = """你是龍奕FDE工作室的助手，说话直接、具体，不敷衍。
结合最近对话理解用户在问什么，用中文回答；不知道就说不知道，不许编客户数据、凭据或案例。
第一句先给结论/答案，不铺垫、不复述问题。
主体 3–5 条，每条一句话；最后一条是明确动作（没有就写"不需要你做任何事"）。
不写模型名、token 数、退出码、文件路径、类名函数名等实现细节。
全文不超过 6 行，不贴大段原文。

最近对话：
%s

用户：%s
助手："""

CAPABILITY_KEYS = ("能干啥", "能做什么", "能干什么", "会什么", "有什么能力",
                   "有哪些agent", "有哪些 agent", "介绍下你", "你是谁",
                   "各角色", "都是干啥", "agent 名单", "角色清单",
                   "谁是谁", "都有谁", "有哪些角色")


def is_capability_question(text):
    low = (text or "").lower().replace(" ", "")
    return any(k.replace(" ", "") in low for k in CAPABILITY_KEYS)


def _qa_model():
    """对话类模型（自由问答/意图识别）：limits.json 的 roles.qa 优先，默认 glm-5.2。"""
    try:
        data = json.loads((ROOT / "agency" / "limits.json").read_text(encoding="utf-8"))
        models = (data.get("roles") or {}).get("qa") or []
        if models:
            return models[0]
    except (OSError, ValueError):
        pass
    return os.environ.get("AGENCY_QA_MODEL", "glm-5.2")


def free_answer(text, open_id):
    """意图未命中业务动作时的真实回答；计入 token 预算。"""
    limit = int(os.environ.get("AGENCY_DAILY_TOKEN_LIMIT", "200000"))
    try:
        import agency
        used = agency.daily_token_usage()
    except Exception:  # noqa: BLE001
        used = 0
    if limit > 0 and used >= limit:
        return "今天的模型预算已用完，先不调模型了；要办的事可以直接说「建任务 …」或「让 pm 做 …」。"
    ctx = conversation.context_text(open_id)
    answer = _llm_chat(QA_PROMPT % (ctx or "（无）", text), model=_qa_model())
    if not answer:
        return "模型这会儿没连上，你的问题我先记下了。要办的事可以直接说「建任务 …」或「让 pm 做 …」。"
    return boss.from_prose(answer.strip())


_VAGUE_ACTIONS = {"搞一下", "弄一下", "看一下", "处理一下", "搞个", "弄个",
                  "看看", "安排一下", "准备一下", "搞点", "弄点", "做一下",
                  "帮我", "帮忙", "你来", "看着办"}

# 明确动作的动词表：没有动词的句子（如"给我的第一个客户"）不能当标题
_ACTION_VERBS = re.compile(
    r"(搞|做|写|建|安排|准备|出个|出份|出一|整理|跟进|联系|拉齐|汇报|"
    r"发|跑|测|复盘|排期|改|补|搭|拉|收|寄|报)")

# 用户明显在切换话题：别再拿旧的建单待确认项去接话
_SWITCH_RE = re.compile(r"(咋样|怎么样|什么情况|什么进度|进展如何|态势|概览|下一步|接下来|然后呢)")


def _clean_action(text):
    """去掉口语前缀，标题只留用户明确说出的动作。"""
    action = (text or "").strip()
    for prefix in ("麻烦你", "麻烦", "请帮我", "帮我", "帮忙", "建个任务", "建一条任务",
                   "建任务", "新增任务", "加个任务", "派活"):
        if action.startswith(prefix):
            action = action[len(prefix):].strip()
            break
    return action.strip(" 。，,.")


_ADVICE_PAT = re.compile(
    r"(怎么|如何|该.{0,6}(什么|哪些|哪条|选)|对不对|行不行|建议|要不要|值不值|策略|打法)")


def _advice_hit(text):
    """咨询/战略问句的规则命中词；先于意图模型，避免被判成建单。"""
    m = _ADVICE_PAT.search(text or "")
    return m.group(0) if m else ""


def _explicit_task(text):
    """明确说"建任务/派活"才算建单，光有动词不算。"""
    return any(k in (text or "") for k in ("建任务", "建个任务", "新增任务",
                                          "加个任务", "建一条", "建单", "派活"))


def _dispatch_role(text):
    """自然语言派活：「让 pm 做…」「叫产品经理出一份…」→ 返回角色名。"""
    t = (text or "").strip()
    if not t:
        return ""
    for card in fc.agent_cards():
        label = card["name"].split(" Agent")[0].strip()
        for name in (card["role"], label):
            if name and re.search(r"(让|叫|请|派给|交给|安排)\s*%s" % re.escape(name), t):
                return card["role"]
    return ""


def _strip_meta(action):
    """去掉动作里的责任人/期限补语——这些信息已经落到 owner、期限字段。"""
    roles = set(dbm.ROLE)
    labels = {card["name"].split(" Agent")[0].strip() for card in fc.agent_cards()}
    keep = []
    for seg in re.split(r"[，,。;；]", action or ""):
        s = seg.strip()
        if not s:
            continue
        if _WHEN_SEG.fullmatch(s):
            continue
        m = re.fullmatch(r"(?:由|让|交给|请)\s*([a-zA-Z\-]+|[\u4e00-\u9fa5]+)", s)
        if m and (m.group(1) in roles or m.group(1) in labels):
            continue
        m = re.fullmatch(r"(?:由|让|交给|请)?\s*([a-zA-Z\-]+|[\u4e00-\u9fa5]+)\s*(?:来)?(?:负责|做|跟进)",
                         s)
        if (m and (m.group(1) in roles or m.group(1) in labels)) or s in roles or s in labels:
            continue
        keep.append(s)
    return "，".join(keep).strip(" 。，,；;")


# 期限补语整段识别：只认「整段都是时间」的片段，避免误删标题内容
_WHEN_SEG = re.compile(
    r"(?:今天|明天|后天|大后天|本?周[一二三四五六日天]?|下?周[一二三四五六日天]?|"
    r"本月|月底|本月底|"
    r"\d+\s*(?:个)?(?:天|日|工作日)|"
    r"\d{1,2}\s*月\s*\d{1,2}\s*[日号])"
    r"(?:内|后|之前|前)?(?:要|完成|交付|就行)?")


def _due_from_text(text, base):
    """把「明天 / 3 天内 / 下周五 / 11 月 20 日」解析成日期；解析不到返回空。"""
    t = text or ""
    m = re.search(r"(大后天|后天|明天|今天)", t)
    if m:
        return (base + dt.timedelta(days={"今天": 0, "明天": 1, "后天": 2, "大后天": 3}[m.group(1)])
                ).isoformat()
    m = re.search(r"(\d+)\s*天", t)
    if m:
        return (base + dt.timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", t)
    if m:
        try:
            due = dt.date(base.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return ""
        if due < base:
            due = dt.date(base.year + 1, due.month, due.day)
        return due.isoformat()
    m = re.search(r"(下?周|本周|这周)([一二三四五六日天])", t)
    if m:
        idx = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4,
               "六": 5, "日": 6, "天": 6}[m.group(2)]
        if m.group(1).startswith("下"):
            # 下周 = 下周一 + 星期偏移
            due = base + dt.timedelta(days=(7 - base.weekday()) + idx)
        else:
            due = base + dt.timedelta(days=(idx - base.weekday()) % 7)
        return due.isoformat()
    return ""


_COLLOQUIAL_TITLE = re.compile(r"^(搞|弄|做|整)(个|一个|一下)?\s*(.+)$")


def _suggest_title(project, title):
    """口语化标题给一条更可执行的建议；只建议，不擅自改写。"""
    m = _COLLOQUIAL_TITLE.match(title or "")
    if not m:
        return ""
    noun = m.group(3).strip(" 。，,.")
    if not noun or len(noun) > 12:
        return ""
    deliverable = noun if noun.endswith(
        ("报告", "单", "表", "文档", "方案", "清单")) else noun + "报告"
    return "完成 %s %s并交付%s" % (project, noun, deliverable)


def _has_action(text):
    """本轮消息是否给出了新动作；单纯补对象/期限时返回 False。"""
    t = (text or "").strip()
    if not t or t == fc.resolve_project(t):
        return False
    return bool(_ACTION_VERBS.search(t))


def _route_intent(intent, open_id, chat_id, text=""):
    """把意图识别结果转成实际执行。跑在后台线程里。"""
    try:
        it = intent.get("intent", "unknown")
        pending = conversation.get_pending(open_id)
        ev = _CUR_EVENT.get()

        # 角色名 + 动作 = 派活（优先于角色介绍，避免把活当成提问）
        role, act = _dispatch_target(text)
        if role and act and not pending:
            hint, suffix = _bulk_hint(act)
            ack = "收到，已派给%s：%s。" % (boss.label(role), act)
            if hint:
                ack += "\n" + hint + "约 1–2 分钟出结果。"
            else:
                ack += "约 1–2 分钟出结果。"
            send(ack, chat_id, kind="ack_intent", sender="chief-of-staff")
            note = act + ("。" + suffix if suffix else "")
            try:
                feishu_commands.handle("/run %s %s" % (boss.label(role), note), open_id,
                                       reply_chat=chat_id, reply_id=ev)
            except Exception as e:  # noqa: BLE001
                send(boss.from_error(str(e)), chat_id, kind="fallback", sender="系统通知")
            return ack

        # 问答类：问能力/角色/有没有某个 agent，直接给答案，不发受理回执
        if not pending and (is_capability_question(text) or _is_role_question(text)):
            reply = _qa_answer(text)
            send(reply, chat_id, kind="qa", sender="chief-of-staff")
            return reply

        # 咨询/战略：走 chief-of-staff / planner 的只读问答，不建单、不挂 pending
        if it == "advice" and not pending:
            role = intent.get("role") if intent.get("role") in dbm.ROLE else ""
            if not role:
                role = "planner" if re.search(r"(计划|排期|拆解|里程碑|WBS)", text or "") \
                    else "chief-of-staff"
            send("请%s出建议，约 1–2 分钟出结果。" % boss.label(role),
                 chat_id, kind="ack_start", sender="系统通知")
            return feishu_commands.handle(f"/run {role} {text}", open_id,
                                          reply_chat=chat_id,
                                          reply_id=_CUR_EVENT.get(""))

        # plan 是拆解目标，不是建单，必须留给下面的 /plan 分支
        wants_task = it == "task" or bool(pending)

        # 建任务/派活四要素门槛：动作、对象、责任人、期限，缺一项都不建单
        if wants_task and it not in ("brief", "help", "sync"):
            action = _clean_action((pending or {}).get("action") or text)
            # 本轮又给了明确动作才覆盖；只补对象/期限不覆盖，避免补语混进标题
            if pending and _has_action(text):
                action = _clean_action(text)
            project = fc.resolve_project(text) or (pending or {}).get("project") or ""
            # 对象只落「对象」字段，不留在标题里
            if project and action and project in action:
                action = re.sub(r"在\s*下", "", action.replace(project, " "))
                action = re.sub(r"\s+", " ", action).strip(" 。，,：:")
            action = _strip_meta(action)
            owner = (pending or {}).get("owner") or ""
            for card in fc.agent_cards():
                label = card["name"].split(" Agent")[0].strip()
                if card["role"] in (text or "").lower() or (label and label in (text or "")):
                    owner = card["role"]
            owner_given = bool(owner)
            owner = owner or "pm"
            base = dt.date.today()
            due = (pending or {}).get("due") or ""
            due_given = bool(due)
            parsed_due = _due_from_text(text, base)
            if parsed_due:
                due, due_given = parsed_due, True
            due = due or (base + dt.timedelta(days=3)).isoformat()

            missing = []
            if not action or action in _VAGUE_ACTIONS or not _ACTION_VERBS.search(action):
                missing.append("做什么（要具体到产出，例如「出一份诊断报告」）")
            if not project:
                missing.append("对象（可选：%s）" % fc.project_options())
            if not owner_given:
                missing.append("责任人（可选：%s）"
                               % "、".join(fc.role_label(r) for r in dbm.ROLE))
            if not due_given:
                missing.append("期限（说「明天」「3 天内」或具体日期）")

            if missing:
                rounds = int((pending or {}).get("round") or 0) + 1
                if rounds > 2:
                    conversation.clear_pending(open_id)
                    first = fc.project_options().split("、")[0]
                    reply = ("两轮都没对齐，我不能建单。还缺：%s。\n"
                             "可选项目：%s。\n"
                             "建议这样说：「在 %s 下建一条<具体动作>，指定负责人，3 天内完成」。"
                             % ("；".join(missing), fc.project_options(), first))
                else:
                    conversation.set_pending(open_id, {
                        "action": action, "owner": owner if owner_given else "",
                        "due": due if due_given else "", "project": project,
                        "round": rounds})
                    reply = "还差 %d 项才能建单：\n" % len(missing)
                    reply += "\n".join("· " + item for item in missing)
                send(reply, chat_id, kind="qa", sender="秘书", cap=False)
                return reply

            conversation.clear_pending(open_id)
            reply = feishu_commands.handle(
                '/task add "%s" --owner %s --project %s --start %s --due %s'
                % (action, owner, project, base.isoformat(), due), open_id)
            merged = _strip_cli_header(reply or "任务已建好")
            merged = merged.replace("已建任务", "已在 %s 下建任务" % project, 1)
            suggestion = _suggest_title(project, action)
            if suggestion:
                merged += ("\n标题偏口语，建议改成「%s」；确认要改我再来一条，不擅自改。"
                           % suggestion)
            send(merged, chat_id, sender="系统通知")
            return merged

        if it == "brief":
            reply = _brief_text(feishu_commands.handle("/brief", open_id) or "")
            send(reply, chat_id, kind="qa", sender="秘书")
            return reply
        elif it == "help":
            reply = _strip_cli_header(feishu_commands.handle("/help", open_id) or "")
            reply = re.sub(r"^【我们公司】\s*\n+", "", reply)
            send(reply, chat_id, kind="qa", sender="秘书")
            return reply
        elif it == "sync":
            send("开始推进今天的待办事件。", chat_id, kind="ack_start", sender="系统通知")
            reply = _strip_cli_header(feishu_commands.handle("/sync", open_id) or "")
        elif it == "run":
            # 自然语言派活：真的跑角色，终态由子进程回结论 + 产出路径
            role = intent.get("role", "chief-of-staff")
            note = (intent.get("note") or text or "").strip()
            send("已派给%s，预计 60–120 秒；完成后单独回结论。"
                 % boss.label(role), chat_id)
            reply = feishu_commands.handle(f"/run {role} {note}", open_id,
                                           reply_chat=chat_id)
        elif it == "task":
            role = intent.get("role", "chief-of-staff")
            title = intent.get("title", intent.get("goal", "新任务"))
            send("正在建任务。", chat_id, kind="ack_start", sender="系统通知")
            fake = f'/task add "{title}" --owner {role}'
            reply = feishu_commands.handle(fake, open_id)
        elif it == "plan":
            goal = intent.get("goal", "")
            project = fc.resolve_project(text) or ""
            send("开始拆解目标，约 1 分钟出结果。", chat_id, kind="ack_start", sender="系统通知")
            fake = ("/plan --project %s %s" % (project, goal)) if project else f"/plan {goal}"
            reply = feishu_commands.handle(fake, open_id, reply_chat=chat_id)
        elif it in ("chat", "unknown"):
            # 未命中业务动作：先真回答，再一次追问（最多两轮）
            # 既定选择（创始人 2026-11-13 拍板）：不复用意图识别返回的 reply，
            # 再调一次模型拿正式回答——用 token 换回答质量，不要改成复用。
            reply = free_answer(text, open_id)
            if it == "unknown" and not pending:
                conversation.set_pending(open_id, {
                    "goal": text, "role": intent.get("role") or "pm", "round": 1})
                reply += (
                    "\n\n想让我接着办的话，告诉我一句就行：\n"
                    "· 建任务（给谁做、什么时候要）\n"
                    "· 派活（让哪个角色做）\n"
                    "· 只是了解进度，也直接说。")
            elif it == "unknown" and pending:
                conversation.clear_pending(open_id)
            send(_cap_lines(reply), chat_id, kind="qa", sender="秘书")
            return reply
        else:
            reply = intent.get("reply") or free_answer(text, open_id)
            if reply:
                send(_cap_lines(reply), chat_id, kind="qa", sender="秘书")
            return reply
        # 走到这里只剩建单/推进这类系统状态回执
        if reply:
            reply = boss.from_prose(str(reply))
            reply = boss.format_system([ln for ln in str(reply).splitlines()
                                        if not ln.startswith("老板，")][:2])
            send(reply, chat_id, sender="系统通知")
        return reply
    except Exception as e:  # noqa: BLE001
        print(f"[意图路由异常] {e}", flush=True)
        send(boss.from_error(str(e)), chat_id, sender="系统通知")
        return ""


def on_message(data: P2ImMessageReceiveV1):
    event = data.event
    msg = event.message
    sender = event.sender
    text = text_of(msg)

    # 机器人自己发的消息不回，避免自激
    if sender and sender.sender_type == "app":
        return

    message_id = msg.message_id or ""

    # 幂等去重：飞书长连接 ACK 超时会重投同一 message_id
    if message_id and not _dedup_check(message_id):
        print(f"[去重跳过] {message_id}", flush=True)
        return

    open_id = (sender.sender_id.open_id if sender and sender.sender_id else "") or ""
    _CUR_EVENT.set(message_id)
    record("message", {
        "open_id": open_id,
        "chat_id": msg.chat_id,
        "msg_id": message_id,
        "text": text,
    })
    print(f"[收到] {open_id} @ {msg.chat_id}：{text}", flush=True)

    # 0) 发送人白名单：在解析指令、回消息、调 LLM 之前就拦掉。
    if not sender_allowed(open_id):
        record("rejected", {"open_id": open_id, "chat_id": msg.chat_id,
                            "msg_id": message_id, "text": text})
        print(f"[拒绝] {open_id} 不在白名单", flush=True)
        return

    # 1) 斜杠指令直接走指令层
    cmd = feishu_commands.parse(text)
    if cmd is not None:
        slow_cmds = {"/run", "/plan", "/dispatch", "/roundtable"}
        if cmd.get("cmd") in slow_cmds:
            send(SLOW_ACK.get(cmd.get("cmd"), "收到，正在办，约 1–2 分钟出结果。"),
                 msg.chat_id, kind="ack_start", sender="系统通知")
        threading.Thread(target=run_command,
                         args=(text, open_id, msg.chat_id, message_id),
                         daemon=True).start()
        return

    # 2) 非指令文本 → 能力提问直接数据驱动回答，其余走意图识别
    if is_capability_question(text):
        conversation.append(open_id, "user", text)
        conversation.clear_pending(open_id)
        reply = _qa_answer(text)
        conversation.append(open_id, "assistant", reply[:300])
        send(reply, msg.chat_id, kind="qa", sender="chief-of-staff")
        return

    # 2.1) "下一步"类问题给具体动作，不复读态势
    if any(k in (text or "") for k in ("下一步", "接下来", "然后呢", "接下来怎么办")):
        conversation.append(open_id, "user", text)
        conversation.clear_pending(open_id)
        reply = "按当前台账，接下来做这几件：\n" + fc.next_actions()
        conversation.append(open_id, "assistant", reply[:300])
        send(reply, msg.chat_id, kind="qa", sender="秘书")
        return

    def _async_intent():
        _CUR_EVENT.set(message_id)
        conversation.append(open_id, "user", text)
        ctx = conversation.context_text(open_id)
        pending = conversation.get_pending(open_id)
        # 换话题、问能力、问态势时丢掉旧的待确认建单，不强行续建单
        if pending and (is_capability_question(text) or _SWITCH_RE.search(text)
                        or _advice_hit(text)):
            conversation.clear_pending(open_id)
            pending = None
        role = _dispatch_role(text)
        advice_key = _advice_hit(text)
        # 明确的动作句：有动词+对象、无问询词，且不是"让某人做"的派活
        looks_task = bool(re.search(r"(搞|做|写|建|安排|准备|出个|出份)", text)) \
            and not re.search(r"[?？]|怎么|什么|哪|吗", text) and not role
        if pending:
            parsed = {"intent": "task", "role": "pm"}
        elif role:
            parsed = {"intent": "run", "role": role, "note": text}
        elif advice_key:
            parsed = {"intent": "advice", "role": "chief-of-staff",
                      "note": "规则命中：" + advice_key}
        elif looks_task or _explicit_task(text):
            parsed = {"intent": "task", "role": "pm"}
        else:
            parsed = intent_classify(text, ctx)
        print(f"[意图] {parsed.get('intent')} <- {text}", flush=True)
        reply = _route_intent(parsed, open_id, msg.chat_id, text)
        if reply:
            conversation.append(open_id, "assistant", reply[:300])

    threading.Thread(target=_async_intent, daemon=True).start()


def validate_identity():
    """应用身份 ↔ 数据库必须配对；配错拒绝启动，避免 dev 写正式库。"""
    dbm.require_env()
    cfg_path = ROOT / "agency" / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cfg = {}
    expected = (((cfg.get("feishu") or {}).get("apps") or {})
                .get(APP_ENV) or {}).get("app_id", "")
    db_path = Path(os.environ.get("AGENCY_DB")
                   or ROOT / "agency" / "state" / "agency.db").resolve()
    prod_db = (ROOT / "agency" / "state" / "agency.db").resolve()
    dev_db = (ROOT / "agency" / "dev-state" / "agency.db").resolve()
    if APP_ENV == "prod":
        if db_path != prod_db:
            sys.exit("拒绝启动：prod 应用只能连正式库，当前库 %s" % db_path)
        if not expected:
            sys.exit("拒绝启动：正式飞书应用未配置（config feishu.apps.prod.app_id 为空）")
        if not APP_ID or APP_ID != expected:
            sys.exit("拒绝启动：应用身份与数据库不匹配（prod 应为 %s，实际 %s）"
                     % (expected, APP_ID or "<空>"))
    else:
        if db_path != dev_db:
            sys.exit("拒绝启动：dev 应用不得连正式库，只能连副本库 %s，当前 %s"
                     % (dev_db, db_path))
        if not expected:
            sys.exit("拒绝启动：dev 应用未配置（config feishu.apps.dev.app_id 为空）")
        if not APP_ID or not APP_SECRET:
            sys.exit("拒绝启动：dev 飞书应用未配置（.env.dev 缺 FEISHU_APP_ID/SECRET）")
        if APP_ID != expected:
            sys.exit("拒绝启动：dev 应用身份不匹配（配置 %s，实际 %s）"
                     % (expected, APP_ID))
        prod_expected = (((cfg.get("feishu") or {}).get("apps") or {})
                         .get("prod") or {}).get("app_id", "")
        if prod_expected and APP_ID == prod_expected:
            sys.exit("拒绝启动：dev 不能复用 prod 飞书应用")
    print("[身份校验] env=%s app_id=%s db=%s" % (APP_ENV, APP_ID, db_path),
          flush=True)


def main():
    validate_identity()
    if not APP_ID or not APP_SECRET:
        sys.exit("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，检查 .env")

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    cli = lark.ws.Client(APP_ID, APP_SECRET, event_handler=handler,
                         log_level=lark.LogLevel.INFO)
    print("长连接启动，等待消息…… Ctrl+C 退出", flush=True)
    cli.start()


if __name__ == "__main__":
    main()
