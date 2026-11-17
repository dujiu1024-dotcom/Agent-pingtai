"""boss.py — 面向创始人的两种声音（系统通知 / 角色汇报）

A. 系统通知（定时、告警、建单/关闭、自检、预算）
   【系统通知】已建任务 T-0141「版式验收」，pm 负责，11-16 截止。还没填验收标准，关闭前要补。
   —— 不称呼老板、不编号、一到两句话；禁止命令行/参数/字段名/省略号。

B. 角色汇报（秘书、教练、幕僚长、pm 等）
   【秘书】老板，今天三件事办完了：
   1. 人员审计做完，缺三个岗位。
   ...
   —— 保留老板汇报体；每条一行、≤30 字。

通用：不出现「…」截断；环境标记只有（DEV）。
"""

import os
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_LABEL_CACHE = {}
CN_FALLBACK = {"hr": "人事"}
# 口语叫法统一映射到真实角色名；不允许出现「助理」这种不存在的身份
CN_ALIASES = {"助理": "秘书"}
GREETING = "老板，"
MAX_OPENING = 25
MAX_ITEM = 30
MIN_ITEMS = 2
MAX_ITEMS = 5
FALLBACK_TEXT = "这条我没整理好，要不要我重跑"

# 系统通知里一出现就拒发
SYS_FORBIDDEN = re.compile(r"(agency\s|--|…|\.\.\.|^\s*\d+[.、]\s|老板)", re.M)
# 两种声音都不许出现
HARD_FORBIDDEN = re.compile(
    r"(glm-|deepseek|qwen|sensenova|gpt-|claude|token|tokens|退出码|exit code|"
    r"agent_runs|dead_letter|Traceback|File \"|line \d+|\.py\b|\.db\b|\.jsonl|"
    r"/Users/|~/|\bpid\b|S[1-4]\b|【事件】|task_created|task_due|done_when)", re.I)
MARKER = re.compile(r"[\[（(][^\]）)]*S[1-4][^\]）)]*[\]）)]|S[1-4]\b")
# 角色汇报里不许出现路径/文件名（创始人只看结论）
PATHISH = re.compile(
    r"(agency/|dev-outputs|outputs/|/Users/|~/|\.(?:md|json|jsonl|log|csv|txt|db|py)\b)", re.I)
_NOISE = re.compile(
    r"(\[降级\]|\[模型\]|\[排队\]|\[回执\]|\[重试|\[告警\]|\[自检|审计：|"
    r"已保存：|字段确认|标题=|owner=|^[✅❌]\s*agency|模型名)", re.I)
# 无来源的定性说法：带数字才留，没数字整句删掉
VAGUE = re.compile(
    r"(预算可控|成本可控|总体可控|整体可控|风险可控|成本不高|时间充裕|"
    r"预算充足|压力不大|较为可控|基本可控|尚可|良好)")
# 排序必须落到编号上，不留在句子里
ORDINAL = re.compile(r"第([一二三四五])(?:优先|顺位|梯队|优先项|优先级)")
_CN_NUM = "一二三四五"

BOSS_STYLE = """【写作口径：给创始人看】
- 一件事就说一句话，不编号；不要为了凑格式把一句话拆成几条。
- 只有「总结干了什么」「要他拍板」「一次说好几件事」才用 1. 2. 3；编号必须与内容一一对应。
- 要他拍板时，每件事按四段写，每条不超过三行：
  1. <项目/客户 + 当前到哪一步>
  为什么现在：<卡在哪个时间点或依赖上，再拖会怎样>
  选项：<A：时间/钱/客户影响；B：时间/钱/客户影响>。建议：<选哪个 + 一句理由>
- 要他提供材料时，写清四件事：要什么、干什么用、最晚什么时候给、不给会卡住什么。
- 不写模型名、token、退出码、路径、函数名、表名、事件名、S1/S4；不用省略号截断。
- 细节留在产出物里；正文最后最多加一句「详情我可以发给你」，不写目录名或任何路径。
- 文末单独输出一块，格式严格如下：
【给创始人】
<一句话，或编号列表，或上面的拍板四段>
"""


def label(sender):
    sender = (sender or "").strip()
    if sender in ("", "system", "系统", "系统通知", "平台", "platform", "bot"):
        return "系统通知"
    if sender in ("roundtable", "圆桌"):
        return "圆桌"
    if sender in _LABEL_CACHE:
        return _LABEL_CACHE[sender]
    sender = CN_ALIASES.get(sender, sender)
    name = ""
    path = ROOT / "agents" / sender / "ROLE.md"
    try:
        head = path.read_text(encoding="utf-8").splitlines()[0]
        m = re.match(r"#\s*([^（(]+?)(?:\s*Agent)?\s*[（(]", head)
        if m:
            name = m.group(1).strip()
    except OSError:
        pass
    if not re.search(r"[\u4e00-\u9fff]", name):
        # 已经是中文角色名就直接用；归属不到任何角色就按系统通知，不编身份
        name = CN_FALLBACK.get(sender) or (
            sender if re.search(r"[\u4e00-\u9fff]", sender) else "系统通知")
    _LABEL_CACHE[sender] = name
    return name


def env_mark():
    return "（DEV）" if os.environ.get("AGENCY_ENV", "prod").lower() != "prod" else ""


def wrap(text, sender="系统通知"):
    """统一加发送者前缀与（DEV）；清掉尾部多余的点串，保留中文句号。"""
    body = str(text).strip()
    body = re.sub(r"[·.．…\s]+$", "", body)
    body = re.sub(r"\n?（(?:DEV|PROD|dev|prod)）[\s.…]*$", "", body).rstrip()
    tag = "【%s】" % label(sender)
    if not body.startswith("【"):
        body = tag + body
    mark = env_mark()
    if mark:
        body += "\n" + mark
    return body


def inner(text):
    body = str(text).strip()
    body = re.sub(r"^【[^】]{1,10}】", "", body)
    body = re.sub(r"\n?（(?:DEV|PROD|dev|prod)）[\s.…]*$", "", body).strip()
    return body


def _clean(text):
    t = re.sub(r"^\[dev\]\s*", "", str(text).strip())
    t = re.sub(r"^[·\-*•]\s*", "", t)
    t = re.sub(r"^\d+[.、)]\s*", "", t)
    t = re.sub(r"^[✅❌]\s*", "", t)
    t = t.strip("，,、；;。 ")
    return MARKER.sub("", t).strip()


def _drop_vague(text):
    """没有数字的「可控/不高/充裕」类从句直接删掉，不做保留。"""
    parts = [p.strip() for p in re.split(r"[，,]", str(text or "")) if p.strip()]
    kept = []
    for p in parts:
        if VAGUE.search(p) and not re.search(r"\d", p):
            continue
        kept.append(p)
    return "，".join(kept)


def _fit(text, limit):
    """规范成一条：不截断；超长按逗号删修饰从句，只留主干。"""
    t = _drop_vague(_clean(text))
    t = re.sub(r"[。；;、，,\s]+$", "", t)
    if not t or "…" in t:
        return None
    if len(t) <= limit:
        return t
    parts = [p.strip() for p in re.split(r"[，,]", t) if p.strip()]
    keep = []
    for p in parts:
        cand = "，".join(keep + [p])
        if len(cand) <= limit:
            keep.append(p)
        elif not keep:
            return None  # 主干本身超过上限，宁可不发这一条也不截断
        else:
            break
    return "，".join(keep) if keep else None


def _split_items(text):
    """把一段流水句拆成条目：按 ；。 和「，且」断句，每条再清理超长与虚词。"""
    t = str(text or "")
    t = re.sub(r"[，,]\s*且", "；", t)
    out = []
    for part in re.split(r"[；;。\n]", t):
        part = re.sub(r"^(另外|还有|此外|再者)[，,、]?\s*", "", part.strip())
        item = _fit(part, MAX_ITEM)
        if item and len(item) >= 4:
            out.append(item)
    return out


def _reorder(items):
    """「第二优先」这类序数落到编号顺序上，句子里的序数删掉。"""
    rest, slots = [], {}
    for item in items:
        m = ORDINAL.search(item)
        if m:
            idx = _CN_NUM.index(m.group(1))
            item = _clean(ORDINAL.sub("", item))
            if item:
                slots.setdefault(idx, item)
        else:
            rest.append(item)
    out = rest[:]
    for idx in sorted(slots):
        out.insert(min(idx, len(out)), slots[idx])
    return out


# ---------- A. 系统通知 ----------

def format_system(parts, detail=""):
    """一到两句自然语言，不编号、不称呼。"""
    sents = []
    for p in parts:
        p = _clean(HARD_FORBIDDEN.sub("", str(p or "")))
        p = re.sub(r"\s*[|｜]\s*", "，", p)
        if p:
            sents.append(p.rstrip("。"))
    text = "。".join(sents[:2])
    if text:
        text += "。"
    return text


def validate_system(text):
    problems = []
    body = inner(text)
    if not body:
        return ["空消息"]
    bad = SYS_FORBIDDEN.search(body)
    if bad:
        problems.append("系统通知出现禁用内容：" + bad.group(0)[:12])
    bad = HARD_FORBIDDEN.search(body)
    if bad:
        problems.append("系统通知出现内部细节：" + bad.group(0))
    if len(re.split(r"[。！？]", body)) > 4:
        problems.append("系统通知超过两句")
    return problems


def repair_system(text):
    body = inner(text)
    body = re.sub(r"^老板，", "", body)
    body = re.sub(r"^\s*\d+[.、]\s*", "", body, flags=re.M)
    body = re.sub(r"✅\s*agency[^\n]*", "", body)
    body = body.replace("agency ", "").replace("--", "")
    body = re.sub(r"…|\.\.\.", "", body)
    parts = [p for p in re.split(r"[。\n]", body) if p.strip()]
    return format_system(parts[:2])


# ---------- B. 角色汇报 ----------

def _has_field_label(text):
    return bool(re.search(r"(需要你|为什么现在|选项|建议|事件|目的)[：:]", str(text or "")))


def has_label_chain(text):
    """「需要你：为什么现在：」这类连续字段标签，属于模板拼接事故。"""
    return bool(re.search(r"(需要你|为什么现在|选项|建议|事件|目的)[：:]\s*"
                          r"(需要你|为什么现在|选项|建议|事件|目的)[：:]", str(text or "")))


def dechain(text):
    """拆掉连续字段标签，只留人话。"""
    t = str(text or "")
    for _ in range(3):
        new = re.sub(r"(需要你|为什么现在|选项|建议|事件|目的)[：:]\s*"
                     r"(?=(需要你|为什么现在|选项|建议|事件|目的)[：:])", "", t)
        if new == t:
            break
        t = new
    return t.strip()


def _error_reason(raw):
    for ln in str(raw or "").splitlines():
        s = _clean(ln).strip()
        if not s or s.startswith(("已保存", "✅", "❌")) or PATHISH.search(s):
            continue
        s = re.sub(r"^(错误|error|原因|失败)[：:]\s*", "", s, flags=re.I)
        if re.search(r"429|rate.?limit|限流", s, re.I):
            return "模型接口限流（错误码 429）"
        if re.search(r"insufficient_quota|额度不足|余额不足", s, re.I):
            return "模型额度不足"
        return s[:60]
    return "没有拿到具体原因，输出为空"


def format_role(items, needs_you="", detail="", opening=""):
    """角色汇报：一件事就一句话直发；多件事才用编号列表，不为了凑格式拆句。"""
    raw = [str(x).strip() for x in items if str(x).strip()]
    if not raw:
        return ""
    action = _clean(_drop_vague(needs_you))
    # 动作行只接受真动作；字段标签/原因残片一律丢掉，宁可短
    if action and (_has_field_label(action) or re.match(r"^(已|因为|由于|当前|卡在|验收)", action)):
        action = ""
    if len(raw) == 1:
        text = _clean(_drop_vague(raw[0]))
        if not text or "…" in text:
            return ""
        text = text.rstrip("。！？") + "。"
        if action and action != "不需要你做什么":
            if not action.startswith(("需要你", "请你")):
                action = "需要你：" + action
            text += action if action.endswith(("。", "！", "？")) else action + "。"
        return text
    items = [x for x in (_fit(i, MAX_ITEM) for i in raw) if x]
    items = _reorder(items)[:MAX_ITEMS]
    if len(items) < MIN_ITEMS:
        # 凑不出编号就按一段话发，不拦
        return " ".join(_clean(_drop_vague(x)).rstrip("。") + "。" for x in raw)
    action = action or "不需要你做什么"
    if not action.startswith(("需要你", "不需要你", "请你")):
        action = "需要你：" + action
    head = _fit(opening, MAX_OPENING) if opening else ""
    # 只有真完成才说「办完了」；没给完成口径时用中性说法
    first = GREETING + (head or "几件事的进展") + "："
    lines = [first] + ["%d. %s。" % (i + 1, it) for i, it in enumerate(items)]
    lines.append(action if action.endswith(("。", "！", "？")) else action + "。")
    if detail:
        lines.append("详情我可以发给你。")
    text = "\n".join(lines)
    # 日期在未来却写「办完了」，按已排期处理
    if re.search(r"办完了", text) and re.search(r"20\d\d-\d\d-\d\d", text):
        future = [d for d in re.findall(r"20\d\d-\d\d-\d\d", text) if d > str(date.today())]
        if future:
            text = text.replace("办完了", "已排期")
    return text


def validate_role(text):
    """角色汇报校验：单句直发不拦；有多条才要求编号与条数对得上。"""
    problems = []
    body = inner(text)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines:
        return ["空消息"]
    numbered = [ln for ln in lines if re.match(r"^\d+[.、]\s", ln)]
    is_decision = ("为什么现在：" in body or "选项：" in body
                   or bool(re.search(r"建议 [AB]。|(?:^|\n)建议：", body)))
    if numbered:
        floor = 1 if is_decision else MIN_ITEMS
        if not (floor <= len(numbered) <= MAX_ITEMS):
            problems.append("编号条数 %d 不在 %d–%d"
                            % (len(numbered), floor, MAX_ITEMS))
        for ln in numbered:
            item = re.sub(r"^\d+[.、]\s*", "", ln).rstrip("。")
            if not is_decision and len(item) > MAX_ITEM + 1:
                problems.append("条目超过 %d 字" % MAX_ITEM)
        if lines[0].startswith(GREETING):
            opening = lines[0][len(GREETING):].rstrip("：:")
            if len(opening) > MAX_OPENING:
                problems.append("首行总括超过 %d 字" % MAX_OPENING)
        actions = [ln for ln in lines
                   if not re.match(r"^\d+[.、]", ln)
                   and ln.startswith(("需要你", "不需要你", "请你"))]
        if len(actions) > 1:
            problems.append("动作行超过一条")
    bad = HARD_FORBIDDEN.search(str(text))
    if bad:
        problems.append("出现内部细节：" + bad.group(0))
    bad = PATHISH.search(str(text))
    if bad:
        problems.append("出现路径或文件名：" + bad.group(0))
    if "详情见" in str(text):
        problems.append("引用了文件位置")
    if "…" in str(text):
        problems.append("出现省略号截断")
    if _NOISE.search(str(text)):
        problems.append("出现工程日志")
    if has_label_chain(str(text)):
        problems.append("字段标签连续拼接")
    return problems


def repair_role(text):
    """按编号列表重建；拆不出 2 条就返回空串，交给发送层发回退提示。"""
    lines = [ln for ln in inner(text).splitlines() if ln.strip()]
    action = ""
    items = []
    for ln in lines:
        if _NOISE.search(ln):
            continue
        if ln.startswith(GREETING):
            continue
        if PATHISH.search(ln) or "详情见" in ln:
            continue
        if ln.startswith(("需要你", "不需要你", "请你")):
            action = ln
            continue
        m = re.match(r"^\d+\.\s*(.+)$", ln)
        if m:
            items.append(m.group(1))
        elif not ln.startswith(("详情我可以发给你",)):
            items.extend(_split_items(ln))
    return format_role(items, action)


# ---------- 统一入口 ----------

def ensure(text, sender="系统通知"):
    """发送前校验：合规原样返回；不合规先修，返回 (文本, 剩余问题)。"""
    checker = validate_system if label(sender) == "系统通知" else validate_role
    fixer = repair_system if label(sender) == "系统通知" else repair_role
    body = inner(text)
    problems = checker(body)
    if not problems:
        return body, []
    fixed = fixer(body)
    return fixed, checker(fixed)


def extract_block(text):
    body = str(text or "")
    idx = body.rfind("【给创始人】")
    if idx < 0:
        return None
    lines = [ln.strip() for ln in body[idx + len("【给创始人】"):].splitlines() if ln.strip()]
    if not lines:
        return None
    first = lines[0]
    opening = first[len(GREETING):].rstrip("：:") if first.startswith(GREETING) else ""
    items, needs = [], ""
    # 只有首行确实是称呼时才当总括跳过；否则首行就是第一条，不能丢
    start = 1 if first.startswith(GREETING) else 0
    for ln in lines[start:10]:
        if ln.startswith(("【", "##", "---")):
            break
        m = re.match(r"^\d+[.、)]\s*(.+)$", ln)
        if m:
            items.append(m.group(1))
        elif ln.startswith(("·", "-", "*", "•")):
            items.append(ln.lstrip("·-*• "))
        elif not needs:
            needs = ln
    return opening, items, needs


def _body_lines(raw):
    """产出里真正给创始人看的正文行（去掉元数据、工程日志）。"""
    lines = []
    for ln in str(raw or "").splitlines():
        ln = ln.strip()
        if not ln or _NOISE.search(ln) or ln.startswith(("#", ">", "|", "---")):
            continue
        if ln.startswith(("project:", "task:", "role:", "date:", "tags:")) or "|" in ln:
            continue
        lines.append(_clean(ln))
    return [ln for ln in lines if ln]


def _single_sentence(lines):
    """整段只有一句话时返回这句；多句或带编号返回空串。"""
    if not lines:
        return ""
    if any(re.match(r"^\d+[.、)]\s", ln) for ln in lines):
        return ""
    text = " ".join(lines).strip()
    if len(re.findall(r"[。！？!?]", text)) != 1:
        return ""
    return text if len(text) >= 6 else ""


OPT_REASON_RE = re.compile(r"(会露馅|会显得|不该|不应该|浪费|因为|风险是|否则|导致)")
OPT_ACTION_RE = re.compile(r"(给|安排|出|做|跑|建|改|补|先|直接|用|拿|启动|确认|验收|发|写|约)")


def _option_pairs(lines):
    """识别 A…／B… 选项对照（容忍编号、同行连写）；A 在前 B 在后，理由不算选项。"""
    flat = []
    for ln in lines:
        for part in re.split(r"(?<=[。；])\s*(?=(?:\d+[.、]\s*)?[A-C][：:、.．\s])", ln.strip()):
            if part.strip():
                flat.append(part.strip())
    pairs, seen = [], set()
    for ln in flat:
        m = re.match(r"^(?:\d+[.、]\s*)?([A-C])\s*(?:[：:、.．]\s*|\s+)(.+)$", ln.strip())
        if not m:
            continue
        lab, txt = m.group(1), m.group(2).strip(" 。")
        if lab in seen:
            continue
        if OPT_REASON_RE.search(txt) and not OPT_ACTION_RE.search(txt):
            continue  # 这是理由，不是选项
        seen.add(lab)
        pairs.append((lab, txt))
    pairs.sort(key=lambda p: p[0])
    return pairs if len(pairs) >= 2 else []


def format_options(pairs, extras=None, opening=""):
    """选项对照保持 A/B 结构；独立结论单列，不加「不需要你做什么」。"""
    head = GREETING + (opening or ("%s个选项：" % {1: "一", 2: "两", 3: "三", 4: "四", 5: "五"}.get(len(pairs), len(pairs))))
    lines = [head] + ["%s %s" % (lab, txt) for lab, txt in pairs]
    for ex in extras or []:
        ex = _clean(str(ex)).strip(" 。")
        if not ex or ex.startswith(("不需要你", "需要你")):
            continue
        if ex.startswith(("A ", "B ")):
            continue
        lines.append(ex + "。")
    return "\n".join(lines)


def _read_prose(raw, opening=""):
    """把一段产出拆成（条目, 动作行, 总括）；编号、分号、句号、逗号「且」都认。"""
    lines = _body_lines(raw)
    items, needs, head = [], "", opening
    for ln in lines:
        if ln.startswith(GREETING):
            ln = ln[len(GREETING):].strip()
            if re.search(r"[：:]", ln):
                first, rest = re.split(r"[：:]", ln, maxsplit=1)
                head = head or first.strip()
                ln = rest.strip()
        if not ln:
            continue
        if ln.startswith(("需要你", "不需要你", "请你")):
            needs = ln
            continue
        for it in _split_items(ln):
            if it.startswith(("需要你", "不需要你", "请你")):
                needs = it
            else:
                items.append(it)
    return items, needs, head


def _block_lines(text):
    """【给创始人】块的原始行，保持多行结构（拍板四段要用）。"""
    body = str(text or "")
    idx = body.rfind("【给创始人】")
    if idx < 0:
        return []
    lines = []
    for ln in body[idx + len("【给创始人】"):].splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith(("【", "##", "---")):
            break
        lines.append(ln)
    return lines


def _decision_items(lines):
    """识别「事件 / 为什么现在 / 选项 / 建议」四段块。

    兼容两种写法：显式「事件：xxx」，以及角色卡要求的
    「1. <项目/客户 + 当前到哪一步>」+「为什么现在：…」。
    """
    items, cur = [], None
    for ln in lines:
        t = ln.strip()
        m = re.match(r"^(?:\d+[.、]\s*)?(?:事件|事项)[：:]\s*(.+)$", t)
        if m:
            if cur:
                items.append(cur)
            cur = {"event": m.group(1).strip(), "why": "", "opts": [], "advice": ""}
            continue
        # 带编号的标签行（如「1. 为什么现在：…」）按标签处理，不当作新事件
        m_label = re.match(r"^\d+[.、]\s*(为什么现在|为什么|原因|选项|建议)[：:]", t)
        if m_label:
            t = re.sub(r"^\d+[.、]\s*", "", t)
        elif re.match(r"^\d+[.、]\s", t):
            if cur:
                items.append(cur)
            cur = {"event": re.sub(r"^\d+[.、]\s*", "", t).strip(),
                   "why": "", "opts": [], "advice": ""}
            continue
        if cur is None:
            continue
        m = re.match(r"^(?:为什么现在|为什么|原因)[：:]\s*(.+)$", t)
        if m:
            cur["why"] = m.group(1).strip()
            continue
        m = re.match(r"^(选项|A|B)[：:]\s*(.+)$", t)
        if m:
            label_ = m.group(1) if m.group(1) in ("A", "B") else ""
            txt = m.group(2).strip()
            am = re.search(r"建议[：:]?\s*(?:选)?\s*([AB])", txt)
            if am:
                cur["advice"] = am.group(1)
                txt = re.split(r"建议[：:]", txt)[0].strip(" 。；")
            cur["opts"].append((label_ + txt) if label_ else txt)
            continue
        m = re.match(r"^建议[：:]\s*(.+)$", t)
        if m:
            cur["advice"] = m.group(1).strip()
    if cur:
        items.append(cur)
    # 只有「事件」没有为什么/选项/建议的编号列表是普通汇报，不能当拍板
    return [i for i in items
            if i["event"] and (i["why"] or i["opts"] or i["advice"])]


def looks_like_decision(text):
    """是否是拍板类消息（含精简版：编号行 + 「A …」+「建议 A。」）。

    精简版由 format_decision 产出，不含「为什么现在：」字面标签，
    发送层不能用字面标签判断，否则拍板消息会被长度闸门截掉后半段。
    """
    body = str(text or "")
    if "为什么现在：" in body or "选项：" in body:
        return True
    if re.search(r"(?:^|\n)建议[：:]", body):
        return True
    if re.search(r"(?:^|\n)[AB]\s.*[；;]\s*[AB]\s", body):
        return True
    return bool(re.search(r"建议 [AB]。", body)
                and re.search(r"(?:^|\n)[AB]\s", body))


def _clip(text, n):
    """按标点收句，不硬切词、不加省略号；没有可断标点时宁可保留原文，
    也不把数字、编号、金额截成错误值（例如把 002 截成 00）。"""
    t = re.sub(r"\s+", " ", str(text or "")).strip(" ，。；、")
    if len(t) > n:
        cut = max(t.rfind("，", 0, n), t.rfind("；", 0, n), t.rfind("、", 0, n))
        if cut >= 6:
            t = t[:cut]
        else:
            space = t.rfind(" ", 0, n + 1)
            t = t[:space] if space >= 6 else t[:n]
            t = re.sub(r"20\d\d-?\d{0,2}-?\d{0,2}$", "", t).rstrip(" ，。；、-")
            if not t:
                t = str(text or "")[:n]
    return t.rstrip("，；、。 ")


def _strip_ids(text):
    t = re.sub(r"\b(OPC-\d+)\s*项目[：:，,]?\s*", "", str(text or ""))
    t = re.sub(r"\bOPC-\d+\s*[：:，,]?\s*", "", t)
    t = re.sub(r"\bT-\d{4}\b[：:，,]?\s*", "", t)
    t = re.sub(r"\bS[1-4]\b\s*", "", t)
    return t.strip(" ，。；")


def _compact_opts(opts):
    parts, seen = [], set()
    for o in opts:
        for m in re.finditer(r"([AB])\s*[：:]?\s*([^；;。]*)", str(o)):
            txt = _strip_ids(m.group(2)).strip(" ，。；")
            lab = m.group(1)
            if not txt:
                continue
            key = "%s %s" % (lab, txt)
            if key not in seen:
                seen.add(key)
                parts.append(key)
    return "；".join(parts)


def format_decision(items, opening=""):
    """拍板事项：不截断、不丢选项、不截建议（创始人拍板：行数不作为拦截理由）。"""
    n = len(items)
    cn = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五"}.get(n, str(n))
    has_proj = all("OPC-" in (d["event"] + d["why"]) for d in items)
    head = GREETING + (opening or ("%s%s件事要你定" % ("OPC-002 " if has_proj else "", cn))) + "："
    lines = [head]
    for i, d in enumerate(items, 1):
        lines.append("%d. %s" % (i, _strip_ids(d["event"]).rstrip("。")))
        if d["why"]:
            lines.append("为什么现在：%s" % _strip_ids(d["why"]).rstrip("。"))
        opts = "；".join(o.rstrip("。") for o in d["opts"] if o)
        third = ("选项：%s。" % opts) if opts else ""
        if d["advice"]:
            third += "建议：%s。" % str(d["advice"]).rstrip("。")
        if third:
            lines.append(third)
    return "\n".join(lines)


def format_provide(lines):
    """要创始人给东西：要什么、干什么用、最晚什么时候给、不给会卡住什么。"""
    fields = {}
    for ln in lines:
        m = re.match(r"^(要什么|干什么用|最晚什么时候给|最晚|不给会卡住什么|不给会卡住)[：:]\s*(.+)$",
                     ln.strip())
        if not m:
            continue
        key = {"最晚": "最晚什么时候给", "不给会卡住": "不给会卡住什么"}.get(m.group(1), m.group(1))
        fields[key] = m.group(2).strip()
    if not fields.get("要什么"):
        return ""
    out = [GREETING + "有一样东西要你给：",
           "要什么：%s" % fields["要什么"].rstrip("。")]
    for key, name in (("干什么用", "干什么用"), ("最晚什么时候给", "最晚"),
                      ("不给会卡住什么", "不给会卡住")):
        if fields.get(key):
            val = fields[key].rstrip("。")
            if key == "最晚什么时候给" and not re.search(
                    r"(\d|今天|明天|后天|本周|这周|下周|周[一二三四五六日]|月|点|前|内)", val):
                val = "这周内"
            out.append("%s：%s" % (name, val))
    return "\n".join(out)


def from_role_output(output_text, ok=True, detail="", opening="",
                     retry_queued=False):
    """角色产出 → 老板汇报体；一句话直发，拍板/要材料走固定四段。"""
    raw = str(output_text or "")
    if ok and "输出被截断" in raw:
        ok = False
    block_raw = _block_lines(raw)
    if block_raw:
        dec = _decision_items(block_raw)
        if dec:
            return format_decision(dec)
        prov = format_provide(block_raw)
        if prov:
            return prov
        opts = _option_pairs(block_raw)
        if len(opts) >= 2:
            pat = r"^(?:\d+[.、]\s*)?([A-C])\s*(?:[：:、.．]\s*|\s+)(.+)$"
            extras = []
            for ln in block_raw:
                for part in re.split(r"(?<=[。；])\s*(?=(?:\d+[.、]\s*)?[A-C][：:、.．\s])", ln.strip()):
                    part = part.strip(" 。")
                    if not part or len(part) > 40:
                        continue
                    m = re.match(pat, part)
                    if not m:
                        extras.append(part)
                    elif (m.group(1), m.group(2).strip(" 。")) not in opts:
                        extras.append(m.group(2))
            return format_options(opts, extras)
    block = extract_block(raw)
    if block:
        opening2, items, needs = block
        items = [_clean(x) for x in items]
        items = [x for x in items if x and not x.startswith(("不需要你", "需要你"))]
        return format_role(items, needs, detail, opening2 or opening)
    if not ok:
        if retry_queued:
            return ("老板，这件事没跑起来：模型额度已用尽。\n"
                    "已排进重试队列，额度恢复后自动重跑。")
        if "输出被截断" in raw:
            return ("老板，这次产出被截断了，已经存下前半段。\n"
                    "需要拆小或分段重跑；你说一声我就重跑。")
        reason = _error_reason(raw)
        return ("老板，这件事没办成。\n"
                "卡在：%s。\n"
                "下一步：我先重试一次；还不行，我会说清要你找谁。" % reason)
    dec = _decision_items(_body_lines(raw))
    if dec:
        return format_decision(dec)
    opts = _option_pairs(_body_lines(raw))
    if opts:
        extras = [ln for ln in _body_lines(raw)
                  if not re.match(r"^[A-C]\s*(?:[：:、.．]\s*|\s+)", ln.strip())
                  and not ln.startswith("【") and len(ln.strip()) <= 40]
        return format_options(opts, extras)
    one = _single_sentence(_body_lines(raw))
    if one:
        return one
    items, needs, head = _read_prose(raw, opening)
    return format_role(items, needs, detail, head)


def from_error(reason, action="需要你确认后我再试一次", detail=""):
    """错误 → 系统通知（谁的失败就说清楚，不称呼老板）。"""
    first = next((x.strip() for x in HARD_FORBIDDEN.sub("", str(reason or "")).splitlines()
                  if x.strip()), "这件事没办成")
    first = re.sub(r"^[❌✅]\s*", "", first).rstrip("。")
    return format_system([first, action], detail)


def from_prose(text, needs_you=""):
    """问答文本 → 角色汇报体；凑不出编号列表返回空串。"""
    block = extract_block(text)
    if block:
        opening, items, needs = block
        return format_role(items, needs_you or needs, "", opening)
    items, needs, head = _read_prose(text)
    return format_role(items, needs_you or needs, "", head)
