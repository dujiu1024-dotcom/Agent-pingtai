#!/usr/bin/env python3
"""WBS 完成时刻排程：按角色容量算出 200 条分项的完成时分，回写 DB / 04 / 09 / 飞书。

规则（创始人 2026-11-12 确认，范围仅到「计划完成日期」）：
- 工作时段 09:00–12:00、13:30–18:30；每角色每天容量 8h。
- 高优先级优先，同优先级按依赖顺序与到期日。
- 单条连续占用，可跨半天（午休），不得跨天。
- 同角色同一时段最多 1 条，重叠即排程错误。
- 只对外展示完成时刻，不展示开始时刻，不新增开始字段。

用法：
    python3 tools/schedule_wbs.py            # 只算不写，打印统计
    python3 tools/schedule_wbs.py --apply    # 回写飞书 / DB / 04 / 09
    python3 tools/schedule_wbs.py --check    # 回读飞书核验
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import feishu_env  # noqa: E402

import db as dbm  # noqa: E402

CONFIG = json.loads((ROOT / "agency" / "config.json").read_text(encoding="utf-8"))
BASE_TOKEN = feishu_env.base_token(required=False)
TABLE_ITEM = CONFIG["feishu"]["table_items"]
TABLE_MS = CONFIG["feishu"]["table_milestones"]
TZ = dt.timezone(dt.timedelta(hours=8))
SEGMENTS = ((9 * 60, 12 * 60), (13 * 60 + 30, 18 * 60 + 30))
DAY_START = 9 * 60
DAY_END = 18 * 60 + 30
PRIORITY = {"高": 0, "中": 1, "低": 2}
DEFAULT_START = dt.date(2026, 11, 11)
SPECIAL_OWNER = {"Q": "auditor", "N": "longyi", "R": "longyi", "债务修复线": "longyi"}


def _lark(*args):
    feishu_env.require_token()
    return subprocess.run(["lark-cli", *args, "--as", "user"],
                          capture_output=True, text=True, cwd=ROOT)


def fetch(table, out_name):
    out = ROOT / "agency" / "state" / "tmp" / out_name
    out.parent.mkdir(parents=True, exist_ok=True)
    r = _lark("base", "+record-list", "--base-token", BASE_TOKEN,
              "--table-id", table, "--format", "ndjson",
              "--output", str(out), "--overwrite")
    if r.returncode != 0:
        sys.exit("飞书拉取失败：" + (r.stderr or r.stdout)[-300:])
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def owners():
    with closing(dbm.connect()) as con:
        return {r["id"]: r["owner"] for r in con.execute("SELECT id, owner FROM tasks")}


def role_of(row, owner_map):
    parent = str(row.get("主任务") or "")
    code = parent.split()[0] if parent else ""
    if code in owner_map:
        return owner_map[code]
    for key, role in SPECIAL_OWNER.items():
        if parent.startswith(key):
            return role
    return "longyi"


def plan_date(row):
    raw = str(row.get("计划完成日期") or "")[:10]
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return DEFAULT_START


def dep_ids(row):
    raw = str(row.get("依赖") or "").strip()
    if raw in ("", "—", "-", "无", "None", "null"):
        return []
    return [x.strip() for x in re.split(r"[,，、]", raw) if x.strip()]


def to_dt(day, minutes):
    return dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=minutes)


def minutes_of(ts):
    return ts.hour * 60 + ts.minute


def next_day_start(day):
    return to_dt(day + dt.timedelta(days=1), DAY_START)


def allocate(start, hours):
    """从 start 起占用 hours 小时，返回结束时刻。

    单条不得跨天：当日剩余容量不足时，整体顺延到次日 09:00 再排。
    0h 任务给 1 分钟名义时长，保证仍有可区分的完成时刻。
    """
    remaining = max(float(hours), 1 / 60) * 60
    cursor = start
    while True:
        day = cursor.date()
        pos = minutes_of(cursor)
        if pos < DAY_START:
            pos = DAY_START
        if pos > DAY_END:
            cursor = next_day_start(day)
            continue
        spare = sum(max(0, end - max(pos, begin)) for begin, end in SEGMENTS)
        if spare < remaining:
            cursor = next_day_start(day)
            continue
        begin_at = to_dt(day, pos)
        for begin, end in SEGMENTS:
            if pos >= end:
                continue
            pos = max(pos, begin)
            if pos >= end:
                continue
            take = min(end - pos, remaining)
            pos += take
            remaining -= take
            if remaining <= 1e-6:
                return begin_at, to_dt(day, int(round(pos)))
        cursor = next_day_start(day)


def build_schedule(rows, owner_map):
    """返回 {编号: (start, end, role)}；只对外用 end，start 仅用于容量累计。"""
    role_cursor = {}
    result = {}
    by_id = {str(r.get("编号")): r for r in rows}
    pending = sorted(rows, key=lambda r: (
        PRIORITY.get((r.get("优先级") or [""])[0], 9),
        plan_date(r), str(r.get("编号"))))
    while pending:
        progressed = False
        for row in list(pending):
            rid = str(row.get("编号"))
            deps = [d for d in dep_ids(row) if d in by_id]
            blocking = [d for d in deps if d not in result]
            if blocking:
                continue
            role = role_of(row, owner_map)
            cursor = role_cursor.get(role) or to_dt(DEFAULT_START, DAY_START)
            for d in deps:
                if d in result and result[d][1] > cursor:
                    cursor = result[d][1]
            end = allocate(cursor, row.get("工时(h)") or 0)
            result[rid] = (end[0], end[1], role)
            role_cursor[role] = end[1]
            pending.remove(row)
            progressed = True
            break
        if not progressed:  # 依赖成环：剩余按优先级直接排，避免死循环
            for row in pending:
                rid = str(row.get("编号"))
                role = role_of(row, owner_map)
                cursor = role_cursor.get(role) or to_dt(DEFAULT_START, DAY_START)
                end = allocate(cursor, row.get("工时(h)") or 0)
                result[rid] = (end[0], end[1], role)
                role_cursor[role] = end[1]
            pending = []
    return result


def conflicts(schedule):
    by_role = {}
    for rid, (start, end, role) in schedule.items():
        by_role.setdefault(role, []).append((start, end, rid))
    bad = []
    for role, items in by_role.items():
        items.sort()
        for prev, cur in zip(items, items[1:]):
            if cur[0] < prev[1]:
                bad.append((role, prev[2], cur[2], prev[1], cur[0]))
    return bad


def iso(ts):
    return ts.replace(tzinfo=TZ).isoformat(timespec="seconds")


def date_iso(ts):
    return ts.strftime("%Y-%m-%dT00:00:00+08:00")


def apply_feishu(rows, schedule, due_map=None):
    due_map = due_map or {}
    updates = {}
    for row in rows:
        rid = str(row.get("编号"))
        day = due_map.get(rid) or schedule[rid][1].strftime("%Y-%m-%d")
        updates[row["record_id"]] = {"计划完成日期": day + "T00:00:00+08:00"}
    ids = list(updates)
    for i in range(0, len(ids), 50):
        chunk = {k: updates[k] for k in ids[i:i + 50]}
        r = _lark("base", "+record-batch-update", "--base-token", BASE_TOKEN,
                  "--table-id", TABLE_ITEM,
                  "--json", json.dumps({"update_records": chunk}, ensure_ascii=False))
        if r.returncode != 0:
            sys.exit("飞书写入失败：" + (r.stderr or r.stdout)[-400:])
    return len(updates)


def closed_map():
    """任务 → 真实 closed_at；父任务未关闭时回退到该任务最近事件时间。"""
    with closing(dbm.connect()) as con:
        tasks = {r["id"]: (r["status"], r["closed_at"])
                 for r in con.execute("SELECT id, status, closed_at FROM tasks")}
        last_event = {r[0]: r[1] for r in con.execute(
            "SELECT task_id, MAX(created_at) FROM events"
            " WHERE task_id IS NOT NULL GROUP BY task_id")}
    result = {}
    for tid, (status, closed) in tasks.items():
        if closed:
            result[tid] = (closed, "closed_at")
        elif tid in last_event:
            result[tid] = (last_event[tid], "last_event")
    return result


def local_minute(raw):
    try:
        ts = dt.datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return ts.astimezone(TZ).strftime("%Y-%m-%dT%H:%M:00+08:00")


def actual_for(row, cmap):
    rid = str(row.get("编号"))
    parent = str(row.get("主任务") or "").split()[0]
    if rid.startswith("T-"):
        return cmap.get(rid)
    if parent in cmap:
        return cmap[parent]
    deps = dep_ids(row)
    hits = [cmap[d] for d in deps if d in cmap]
    return max(hits) if hits else None


def apply_actual(rows, cmap):
    updates = {}
    for row in rows:
        if (row.get("状态") or [""])[0] != "已完成":
            continue
        hit = actual_for(row, cmap)
        if not hit:
            continue
        if hit[1] == "closed_at":
            updates[row["record_id"]] = {"实际完成日期": local_minute(hit[0])}
        else:
            # 任务未关闭：不把最近事件时间冒充实际完成时间，留空并标注
            note = "实际完成待任务关闭后回填（当前时间取自最近事件，非实际完成）"
            old = str(row.get("备注") or "").strip()
            updates[row["record_id"]] = {
                "实际完成日期": None,
                "备注": (old + "｜" + note) if old and note not in old else (old or note),
            }
    ids = list(updates)
    for i in range(0, len(ids), 50):
        chunk = {k: updates[k] for k in ids[i:i + 50]}
        r = _lark("base", "+record-batch-update", "--base-token", BASE_TOKEN,
                  "--table-id", TABLE_ITEM,
                  "--json", json.dumps({"update_records": chunk}, ensure_ascii=False))
        if r.returncode != 0:
            sys.exit("实际完成日期写入失败：" + (r.stderr or r.stdout)[-400:])
    return len(updates)


def apply_milestones(rows, schedule, due_map=None):
    due_map = due_map or {}
    groups = {}
    for row in rows:
        parent = str(row.get("主任务") or "")
        code = parent.split()[0] if parent else parent
        groups.setdefault(code, []).append(schedule[str(row.get("编号"))])
    ms_rows = fetch(TABLE_MS, "schedule-ms.ndjson")
    updates = {}
    for m in ms_rows:
        key = str(m.get("ID") or "")
        items = groups.get(key)
        if not items and key == "DEBT":
            items = [v for k, v in schedule.items() if k.startswith("T-")]
        if not items and key.startswith("Q/N/R"):
            items = [v for k, v in schedule.items() if k[:1] in ("Q", "N", "R")]
        if not items:
            continue
        stamp = due_map.get(key) or max(x[1] for x in items).strftime("%Y-%m-%d")
        updates[m["record_id"]] = {"截止": stamp + "T00:00:00+08:00"}
    if updates:
        r = _lark("base", "+record-batch-update", "--base-token", BASE_TOKEN,
                  "--table-id", TABLE_MS,
                  "--json", json.dumps({"update_records": updates}, ensure_ascii=False))
        if r.returncode != 0:
            sys.exit("里程碑写入失败：" + (r.stderr or r.stdout)[-400:])
    return len(updates)


def apply_db(rows, schedule):
    """只写 due_at（完成日期）；start_at 保持原日期。

    若排程完成日早于任务开始日，按开始日封底，避免出现日期倒挂。
    """
    groups = {}
    for row in rows:
        rid = str(row.get("编号"))
        parent = str(row.get("主任务") or "")
        code = parent.split()[0] if parent else ""
        end = schedule[rid][1]
        if rid.startswith("T-"):
            groups[rid] = end
        elif code:
            groups[code] = max(groups.get(code, end), end)
    due_map = {}
    with closing(dbm.connect()) as con, con:
        for tid, end in groups.items():
            row = con.execute("SELECT id, start_at FROM tasks WHERE id = ?",
                              (tid,)).fetchone()
            if not row:
                continue
            day = end.strftime("%Y-%m-%d")
            start = str(row["start_at"] or "")[:10]
            if start and day < start:
                day = start
            con.execute("UPDATE tasks SET due_at = ?, updated_at = ? WHERE id = ?",
                        (day, dbm.now(), tid))
            due_map[tid] = day
    return due_map


def apply_docs(rows, schedule, due_map=None):
    due_map = due_map or {}
    plan04 = ROOT / "clients" / "002-FDE一人公司" / "04-项目计划.md"
    plan09 = ROOT / "clients" / "002-FDE一人公司" / "09-分项计划.md"
    groups = {}
    for row in rows:
        parent = str(row.get("主任务") or "")
        code = parent.split()[0] if parent else ""
        if not code:
            continue
        end = schedule[str(row.get("编号"))][1]
        groups[code] = max(groups.get(code, end), end)

    def day_of(code):
        if code in due_map:
            return due_map[code][5:]
        return "%02d-%02d" % (groups[code].month, groups[code].day)

    text = plan04.read_text(encoding="utf-8")
    for code, end in groups.items():
        if not code.startswith("T-00") or len(code) != 6:
            continue
        num = int(code[-2:]) - 7
        if not 1 <= num <= 8:
            continue
        p0 = "P0-%d" % num
        # 04 主表：| ID | 任务 | 产出 | 开始 | 计划完成 | 实际完成 | Owner | 工时 |
        text = re.sub(
            r"(\|\s*%s\s*\|[^|]*\|[^|]*\|[^|]*\|)\s*[\d-]+(?:\s+\d{2}:\d{2})?\s*\|"
            % re.escape(p0),
            r"\g<1> %s |" % day_of(code), text, count=1)
    qnr_ends = {}
    for row in rows:
        rid = str(row.get("编号"))
        if rid[:1] in ("Q", "N", "R"):
            qnr_ends[rid] = schedule[rid][1]
    for rid, end in qnr_ends.items():
        text = re.sub(
            r"(\|\s*%s\s*\|[^|]*\|[^|]*\|[^|]*\|)\s*[\d-]+(?:\s+\d{2}:\d{2})?\s*\|"
            % re.escape(rid),
            r"\g<1> %02d-%02d |" % (end.month, end.day), text, count=1)
    plan04.write_text(text, encoding="utf-8")

    text = plan09.read_text(encoding="utf-8")
    for code, end in groups.items():
        if not code.startswith("T-00") or len(code) != 6:
            continue
        stamp = day_of(code)
        # 09 主任务标题：把括号里最后一个日期（含 11-12→11-13 的终点）补时分
        def fix(m):
            # 只改括号内最后一个日期 token
            parts = list(re.finditer(r"\d{2}-\d{2}(?![\d:])", m.group(2)))
            if not parts:
                return m.group(0)
            last = parts[-1]
            inner = m.group(2)[:last.start()] + stamp + m.group(2)[last.end():]
            return m.group(1) + inner + "）"
        text = re.sub(r"(## %s [^\n（]*（)([^）]*)）" % re.escape(code), fix, text, count=1)
    # 09 债务表：| 编号 | 动作 | 工时 | 优先级 | 计划完成 | 验收 |
    for row in rows:
        rid = str(row.get("编号"))
        if not rid.startswith("T-"):
            continue
        end = schedule[rid][1]
        text = re.sub(
            r"(\|\s*%s\s*\|[^|]*\|[^|]*\|[^|]*\|)\s*[\d-]+(?:\s+\d{2}:\d{2})?\s*\|"
            % re.escape(rid),
            r"\g<1> %s |" % due_map.get(rid, end.strftime("%Y-%m-%d")),
            text, count=1)
    plan09.write_text(text, encoding="utf-8")
    return len(groups)


def check(rows, schedule):
    print("排程条数：%d｜角色数：%d" % (
        len(schedule), len({v[2] for v in schedule.values()})))
    bad = conflicts(schedule)
    print("同角色同时段冲突：%d 条" % len(bad))
    for item in bad[:5]:
        print("  ", item)
    ends = [v[1] for v in schedule.values()]
    print("最早完成：%s｜最晚完成：%s" % (iso(min(ends)), iso(max(ends))))
    same_day = all(v[0].date() == v[1].date() for v in schedule.values())
    print("单条不跨天：%s" % ("是" if same_day else "否"))
    return len(bad) == 0


def readback():
    fields = _lark("base", "+field-list", "--base-token", BASE_TOKEN,
                   "--table-id", TABLE_ITEM, "--format", "json", "--jq",
                   ".data.fields[] | select(.name==\"计划完成日期\" or .name==\"实际完成日期\")"
                   " | {name, type, format: .style.format}")
    print("字段类型/格式：")
    print(fields.stdout.strip())
    rows = fetch(TABLE_ITEM, "schedule-readback.ndjson")
    cmap = closed_map()
    done = [r for r in rows if (r.get("状态") or [""])[0] == "已完成"]
    actual_with_time = 0
    pending = 0
    for r in done:
        raw = str(r.get("实际完成日期") or "")
        hit = actual_for(r, cmap)
        if re.search(r"T\d{2}:\d{2}", raw):
            actual_with_time += 1
        elif hit and hit[1] == "last_event":
            pending += 1
    plan_with_time = sum(
        1 for r in rows
        if re.search(r"T(?!00:00)\d{2}:\d{2}", str(r.get("计划完成日期") or "")))
    mismatch = 0
    for r in done:
        hit = actual_for(r, cmap)
        if not hit or hit[1] != "closed_at":
            continue
        want = local_minute(hit[0])[:16]
        have = str(r.get("实际完成日期") or "")[:16]
        if want != have:
            mismatch += 1
    print("实际完成日期带时分：%d 条（取 closed_at）｜待确认（未关闭、留空）：%d 条"
          % (actual_with_time, pending))
    print("计划完成日期出现时分：%d 条（应为 0）" % plan_with_time)
    print("实际完成时间与库内 closed_at 不一致：%d 条（应为 0）" % mismatch)
    times = [str(r.get("实际完成日期")) for r in done if r.get("实际完成日期")]
    if times:
        print("实际完成最早：%s｜最晚：%s" % (min(times), max(times)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        readback()
        return
    rows = fetch(TABLE_ITEM, "schedule-items.ndjson")
    schedule = build_schedule(rows, owners())
    ok = check(rows, schedule)
    if args.apply:
        due_map = apply_db(rows, schedule)
        n = apply_feishu(rows, schedule, due_map)
        m = apply_milestones(rows, schedule, due_map)
        doc = apply_docs(rows, schedule, due_map)
        a = apply_actual(rows, closed_map())
        print("已写入：飞书计划 %d 条｜里程碑 %d 条｜DB %d 条｜文档 %d｜实际完成 %d 条"
              % (n, m, len(due_map), doc, a))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
