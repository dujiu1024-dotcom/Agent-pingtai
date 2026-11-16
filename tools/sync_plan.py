#!/usr/bin/env python3
"""四处同步：DB → 04 / 09 / 飞书。默认只检查，--apply-* 才写。

  python3 tools/sync_plan.py                 # 检查四处是否一致
  python3 tools/sync_plan.py --apply-docs    # 用 DB 重写 04/09 的债务进度数字
  python3 tools/sync_plan.py --apply-feishu  # 用 DB 同步飞书分项状态与汇总备注
"""

import argparse
import json
import re
import subprocess
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import check_consistency as cc  # noqa: E402

import db as dbm  # noqa: E402

MILESTONE = "tblDzJgHKwa4uVsU"
STATUS = {"done": "已完成", "canceled": "作废"}
TZ8 = __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))


def _plan_stamp(raw):
    """解析计划侧日期；有日期时间也只取日期。返回 YYYY-MM-DD 或 None。"""
    text = str(raw or "")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return "%s-%s-%s" % m.groups()
    m = re.search(r"(?<!\d)(\d{2})-(\d{2})(?![\d:])", text)
    if m:
        return "2026-%s-%s" % m.groups()
    return None


def _actual_minute(raw):
    """解析实际完成时间到分钟；无时分返回 None。"""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", str(raw or ""))
    return "%s-%s-%sT%s:%s" % m.groups() if m else None


def _closed_minute(raw):
    import datetime as _dt
    try:
        ts = _dt.datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return ts.astimezone(TZ8).strftime("%Y-%m-%dT%H:%M")


def check_dates(tasks, item_rows, ms_rows):
    """计划侧比日期（容忍日期/日期时间）；实际侧比到分钟。"""
    by_id = {t["id"]: t for t in tasks}
    ok = True
    ms = {str(r.get("ID") or ""): r for r in ms_rows}
    bad = []
    for tid in [f"T-{n:04d}" for n in range(8, 28)]:
        task = by_id.get(tid)
        row = ms.get(tid)
        if not task or not row:
            continue
        want = str(task["due_at"] or "")[:10]
        have = _plan_stamp(row.get("截止"))
        if want and have and want != have:
            bad.append(f"{tid} 里程碑截止 {have} ≠ DB {want}")
    if bad:
        ok = False
        print("[错误] 计划侧日期不一致 %d 条：%s" % (len(bad), "；".join(bad[:5])))
    else:
        print("[OK ] 计划侧日期一致（里程碑截止 ↔ DB due_at，仅比日期）")

    with closing(dbm.connect()) as con:
        closed = {r["id"]: r["closed_at"] for r in con.execute(
            "SELECT id, closed_at FROM tasks WHERE closed_at IS NOT NULL")}
    mismatched = []
    checked = 0
    pending = []
    early = []
    qnr = 0
    for row in item_rows:
        rid = str(row.get("编号") or "")
        if (row.get("状态") or [""])[0] != "已完成":
            continue
        # Q/N/R 为计划内分项，不升级台账（09 拆分规则），只核对是否回填实际完成时间
        if re.fullmatch(r"[QNR]\d+", rid):
            if _actual_minute(row.get("实际完成日期")):
                qnr += 1
            else:
                mismatched.append(f"{rid} 已完成但实际完成时间为空")
            continue
        parent = str(row.get("主任务") or "").split()[0]
        deps = [x.strip() for x in re.split(r"[,，、]", str(row.get("依赖") or ""))
                if x.strip() not in ("", "—", "-", "无")]
        keys = [rid] if rid.startswith("T-") else [parent] + deps
        key = next((k for k in keys if k in closed), None)
        have = _actual_minute(row.get("实际完成日期"))
        if key is None:
            if rid.startswith("T-"):
                # 债务线分项直接落库：任务未关闭却标已完成，属于真不一致
                mismatched.append(f"{rid} 任务未关闭却填了实际完成 {have}" if have
                                  else f"{rid} 任务未关闭")
            elif have:
                # 主任务未关，但分项已独立完成并回填时间（2026-11-13 创始人确认允许）
                early.append(rid)
            else:
                pending.append(rid)
            continue
        if not have:
            mismatched.append(f"{rid} 已关闭但实际完成时间为空")
            continue
        checked += 1
        want = _closed_minute(closed[key])
        if want and have != want:
            mismatched.append(f"{rid} 飞书 {have} ≠ DB {want}")
    if pending:
        print("[待确认] %d 条：任务未关闭，实际完成时间留空待回填（不计入一致）：%s"
              % (len(pending), "、".join(pending[:10])))
    if early:
        print("[OK ] 主任务未关、分项已独立完成并回填时间 %d 条：%s"
              % (len(early), "、".join(early[:10])))
    if mismatched:
        ok = False
        print("[错误] 实际完成时间不一致 %d/%d 条：%s"
              % (len(mismatched), checked, "；".join(mismatched[:5])))
    else:
        print("[OK ] 实际侧时间一致（飞书实际完成 ↔ DB closed_at，比到分钟，%d 条）"
              % checked)
    if qnr:
        print("[OK ] Q/N/R 已完成 %d 条均已回填实际完成时间（无台账行，只校验是否填写）"
              % qnr)
    return ok


def debt_stats(tasks):
    debt = [t for t in tasks if t["kind"] == "debt"]
    done = [t for t in debt if t["status"] == "done"]
    canceled = [t for t in debt if t["status"] == "canceled"]
    active = [t for t in debt if t["status"] not in ("done", "canceled")]
    return {
        "total": len(debt),
        "done": len(done),
        "canceled": len(canceled),
        "open": len(active),
        "hours": sum(float(t["hours"] or 0) for t in debt),
        "done_hours": sum(float(t["hours"] or 0) for t in done),
        "canceled_hours": sum(float(t["hours"] or 0) for t in canceled),
        "left_hours": sum(float(t["hours"] or 0) for t in active),
    }


def check(tasks):
    _, total04 = cc.hours_from_04()
    _, _, total09, lines09 = cc.items_from_09()
    fmap, flines, fcount, note = cc.items_from_feishu()
    ok = True
    print("DB   主线 %.1fh｜工具线 %.1fh｜债务线 %.1fh"
          % (sum(float(t["hours"] or 0) for t in tasks
                 if "T-0008" <= t["id"] <= "T-0015"),
             sum(float(t["hours"] or 0) for t in tasks
                 if "T-0016" <= t["id"] <= "T-0027"),
             debt_stats(tasks)["hours"]))
    print("04   主线 %.1fh / %d 个主任务" % (total04, len(cc.hours_from_04()[0])))
    print("09   主线 %.1fh / %d 条分项" % (total09, sum(v["count"] for v in lines09.values())))
    if fmap is None:
        print("[提示] " + note)
        return False
    print("飞书 分项 %d 条｜主线 %.1fh｜债务线 %.1fh"
          % (fcount, flines["main"]["hours"], flines["debt"]["hours"]))

    # 每批关闭后的固定核对：DB done ↔ 飞书已完成 ↔ 09 已勾选
    debt_ids = {t["id"] for t in tasks if t["kind"] == "debt"}
    db_done = len([t for t in tasks
                   if t["kind"] == "debt" and t["status"] == "done"])
    out = ROOT / "agency/state/tmp/sync-audit.ndjson"
    r = _lark("base", "+record-list", "--base-token", cc.BASE_TOKEN,
              "--table-id", cc.TABLE_ITEM, "--format", "ndjson",
              "--output", str(out), "--overwrite")
    fs_done = 0
    if r.returncode == 0:
        for line in out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if (str(row.get("编号") or "") in debt_ids
                    and (row.get("状态") or [""])[0] == "已完成"):
                fs_done += 1
    text09 = cc.PLAN09.read_text(encoding="utf-8")
    m = re.search(r"债务线小计：.*?已完成 (\d+) 条", text09, re.S)
    d09 = int(m.group(1)) if m else -1
    print("关闭行数：DB done %d｜飞书已完成 %d｜09 已勾选 %d"
          % (db_done, fs_done, d09))
    counts_ok = db_done == fs_done == d09
    print("  行数核对：" + ("一致" if counts_ok else "不一致，该批不算完成"))
    ok = ok and counts_ok
    for key, name in cc.LINE_NAMES.items():
        local = lines09[key]
        remote = flines[key]
        good = (remote["count"] == local["count"]
                and abs(remote["hours"] - local["hours"]) < 1e-6)
        print("  [%s] %s：09 %d 条/%.1fh ↔ 飞书 %d 条/%.1fh"
              % ("OK " if good else "错误", name, local["count"],
                 local["hours"], remote["count"], remote["hours"]))
        ok = ok and good
    item_rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()
                 if line.strip()] if r.returncode == 0 else []
    ms_out = ROOT / "agency/state/tmp/sync-ms-check.ndjson"
    _lark("base", "+record-list", "--base-token", cc.BASE_TOKEN,
          "--table-id", MILESTONE, "--format", "ndjson",
          "--output", str(ms_out), "--overwrite")
    ms_rows = [json.loads(line) for line in ms_out.read_text(encoding="utf-8").splitlines()
               if line.strip()] if ms_out.exists() else []
    ok = check_dates(tasks, item_rows, ms_rows) and ok
    return ok


def apply_docs(tasks):
    s = debt_stats(tasks)
    p04 = cc.PLAN04
    text = p04.read_text(encoding="utf-8")
    text, n1 = re.subn(r"当前已完成 \d+ 条、待办 \d+ 条",
                       "当前已完成 %d 条、待办 %d 条" % (s["done"], s["open"]),
                       text, count=1)
    debt = [t for t in tasks if t["kind"] == "debt"]
    total_n = len(debt)
    total_h = sum(float(t.get("hours") or 0) for t in debt)
    text, n2 = re.subn(r"\| 债务修复线 \| \d+ \| [\d.]+h \| \d+ \| \d+ \|",
                       "| 债务修复线 | %d | %gh | %d | %d |"
                       % (total_n, total_h, s["done"], s["open"]), text, count=1)
    p04.write_text(text, encoding="utf-8")

    p09 = cc.PLAN09
    text = p09.read_text(encoding="utf-8")
    text, n3 = re.subn(
        r"已完成 \d+ 条 / [\d.]+h，待办 \d+ 条 / [\d.]+h",
        "已完成 %d 条 / %.1fh，待办 %d 条 / %.1fh"
        % (s["done"], s["done_hours"], s["open"], s["left_hours"]),
        text, count=1)
    p09.write_text(text, encoding="utf-8")
    print("04 改写 %d 处｜09 改写 %d 处" % (n1 + n2, n3))
    return n1 and n2 and n3


def _lark(*args):
    return subprocess.run(["lark-cli", *args, "--as", "user"],
                          capture_output=True, text=True, cwd=ROOT)


def apply_feishu(tasks):
    out = ROOT / "agency/state/tmp/sync-items.ndjson"
    r = _lark("base", "+record-list", "--base-token", cc.BASE_TOKEN,
              "--table-id", cc.TABLE_ITEM, "--format", "ndjson",
              "--output", str(out), "--overwrite")
    if r.returncode != 0:
        print("飞书拉取失败：" + (r.stderr or r.stdout)[-200:])
        return False
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    by_id = {t["id"]: t for t in tasks if t["kind"] == "debt"}
    with closing(dbm.connect()) as con:
        closed = {r["id"]: (r["closed_at"] or "")
                  for r in con.execute("SELECT id, closed_at FROM tasks")}
    updates = {}
    for row in rows:
        task = by_id.get(str(row.get("编号") or ""))
        if not task:
            continue
        want = STATUS.get(task["status"], "待办")
        have = (row.get("状态") or [""])[0]
        date = _closed_minute(closed.get(task["id"], "")) or ""
        patch = {}
        if have != want:
            patch["状态"] = [want]
        if want == "已完成" and date:
            patch["实际完成日期"] = date + ":00+08:00"
        if patch:
            updates[row["record_id"]] = patch
    if updates:
        r = _lark("base", "+record-batch-update", "--base-token", cc.BASE_TOKEN,
                  "--table-id", cc.TABLE_ITEM,
                  "--json", json.dumps({"update_records": updates}, ensure_ascii=False))
        if r.returncode != 0:
            print("飞书写入失败：" + (r.stderr or r.stdout)[-200:])
            return False
    s = debt_stats(tasks)
    ms_out = ROOT / "agency/state/tmp/sync-ms.ndjson"
    _lark("base", "+record-list", "--base-token", cc.BASE_TOKEN,
          "--table-id", MILESTONE, "--format", "ndjson",
          "--output", str(ms_out), "--overwrite")
    ms = [json.loads(line) for line in ms_out.read_text(encoding="utf-8").splitlines()
          if line.strip()]
    debt = next((m for m in ms if m.get("ID") == "DEBT"), None)
    if debt:
        note = ("来源 06/08 技术债审查；T-0032～T-0111；已完成 %d 条/%.1fh，"
                "作废 %d 条/%.1fh，未完成 %d 条/%.1fh；与主线 90h 分开计"
                % (s["done"], s["done_hours"], s["canceled"], s["canceled_hours"],
                   s["open"], s["left_hours"]))
        _lark("base", "+record-batch-update", "--base-token", cc.BASE_TOKEN,
              "--table-id", MILESTONE,
              "--json", json.dumps({"update_records": {
                  debt["record_id"]: {"备注": note}}}, ensure_ascii=False))
    print("飞书分项改写 %d 条，汇总备注已更新" % len(updates))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply-docs", action="store_true")
    ap.add_argument("--apply-feishu", action="store_true")
    args = ap.parse_args()
    all_tasks = cc.load_tasks()
    tasks, outside = cc.scope_tasks(all_tasks)
    cc.print_scope(tasks, outside)
    if outside:
        print("[提示] 本轮只核对纳管项目；计划外项目未纳入一致性结论。")
    ok = check(tasks)
    if args.apply_docs:
        ok = apply_docs(tasks) and ok
    if args.apply_feishu:
        ok = apply_feishu(tasks) and ok
        ok = check(tasks) and ok
    print("结论：" + ("通过" if ok else "不一致，需要同步")
          + f"（仅核对纳管项目 {'、'.join(cc.MANAGED)}；"
          + (f"计划外项目 {len(outside)} 条未纳入）" if outside else "无计划外项目）"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
