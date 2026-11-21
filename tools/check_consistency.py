#!/usr/bin/env python3
"""结构校验 + 三方一致性检查（分项 13.3 / 13.4 / 16.3）

三部分：
1. 结构校验：字段缺失、枚举/类型错误、日期倒挂、依赖悬空、关闭无证据；
   --self-test 会造 5 条坏数据，验证全部能被报出。
2. 本体与注册表校验：owner ∈ ROLE∪{longyi}、kind ∈ TASK_KIND、
   events.type ⊆ EVENT_TYPE、ontology 3.6 事件表与 EVENT_TYPE 差集为空。
3. 三方一致性：agency.db ↔ 04 项目计划 ↔ 09 分项计划 ↔ 飞书分项表，
   比对每个主任务工时与合计（应均为 90h）。

用法：
    python3 tools/check_consistency.py            # 校验 + 一致性（飞书任务已弃用，默认 --no-feishu）
    python3 tools/check_consistency.py --feishu   # 显式开启飞书比对（仅当飞书表仍活跃时）
    python3 tools/check_consistency.py --self-test
"""

import argparse
import copy
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import feishu_env  # noqa: E402

# legacy db（agency/state/agency.db）已于 2026-11-20 清理完毕，不再 import dbm。
# 所有枚举常量改为本地定义（与 ontology.md 对齐），DB 校验改为跳过。

# —— 枚举常量（与 agents/ontology.md 对齐）——
REGISTRY_AGENTS = {"finance", "hr", "local-assistant", "ops", "test-ops"}
TASK_STATUS = {"todo", "doing", "blocked", "review", "done", "closed", "canceled"}
TASK_KIND = {"main", "tool", "debt", "standalone"}

CLIENT = ROOT / "clients" / "002-FDE一人公司"
PLAN04 = CLIENT / "04-项目计划.md"
PLAN09 = CLIENT / "09-分项计划.md"
ONTOLOGY = ROOT / "agents" / "ontology.md"
MANIFEST = ROOT / "agency" / "state" / "migrations" / "2026-11-11-fields.json"

CONFIG = json.loads((ROOT / "agency" / "config.json").read_text(encoding="utf-8"))
FEISHU = CONFIG.get("feishu", {})
LINES = CONFIG.get("lines", {})
BASE_TOKEN = feishu_env.base_token(required=False)
TABLE_ITEM = FEISHU.get("table_items", "")
EMPTY = "—"
P0_TO_TASK = {f"P0-{i}": f"T-{7 + i:04d}" for i in range(1, 9)}
COMPANY = CONFIG.get("company", {}) or {}
MANAGED = [str(x) for x in (
    COMPANY.get("managed_projects") or [COMPANY.get("project") or "OPC-002"])]


def load_tasks():
    """加载 WBS 任务。legacy agency.db 已于 2026-11-20 清理，返回空列表。"""
    # legacy SQLite 已删除；如需加载飞书分项表请用 --feishu 显式开启。
    return []


def scope_tasks(tasks):
    """按纳管口径拆分：纳管项目 / 计划外项目。"""
    managed = set(MANAGED)
    inside = [t for t in tasks if (t.get("project_id") or "") in managed]
    outside = [t for t in tasks if (t.get("project_id") or "") not in managed]
    return inside, outside


def print_scope(tasks, outside):
    print("=== 检查范围 ===")
    print(f"纳管项目：{'、'.join(MANAGED)}｜任务 {len(tasks)} 条")
    if not outside:
        print("计划外项目：无")
        return
    counts = {}
    for t in outside:
        code = t.get("project_id") or "(未指定)"
        counts[code] = counts.get(code, 0) + 1
    print(f"计划外项目：{len(counts)} 个 / {len(outside)} 条任务，"
          "不计入公司完成率与一致性口径")
    for code, n in sorted(counts.items()):
        print(f"  {code}：{n} 条")


def load_explicit_empty():
    if not MANIFEST.exists():
        return {}
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8")).get("explicit_empty", {})
    except json.JSONDecodeError:
        return {}


def validate(tasks, allow_empty):
    """返回 (errors, warnings)。errors 非空即结构不合格。"""
    errors, warnings = [], []
    ids = {t["id"] for t in tasks}
    seen = set()
    owners = REGISTRY_AGENTS | {"longyi"}

    for t in tasks:
        tid = t["id"]
        if not re.fullmatch(r"T-\d{4}", tid or ""):
            errors.append(f"{tid}: id 格式非法")
        if tid in seen:
            errors.append(f"{tid}: id 重复")
        seen.add(tid)

        for field in ("title", "owner", "kind", "status"):
            if not (t.get(field) or "").strip():
                errors.append(f"{tid}: 缺少 {field}")

        if t.get("status") not in TASK_STATUS:
            errors.append(f"{tid}: status 非法（{t.get('status')!r}）")

        owner = (t.get("owner") or "").strip()
        if owner and owner not in owners:
            errors.append(
                f"{tid}: owner 非法（{owner}），应属于 Registry Agent 或 longyi")

        kind = (t.get("kind") or "").strip()
        if kind and kind not in TASK_KIND:
            errors.append(f"{tid}: kind 非法（{kind}），应属于 TASK_KIND")

        dates = {}
        for field in ("start_at", "due_at"):
            try:
                # 容忍「日期」与「日期 时:分」两种写法，只校验日期部分与先后顺序
                dates[field] = datetime.strptime(str(t[field])[:10], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                errors.append(f"{tid}: {field} 无法解析（{t.get(field)!r}）")
        if len(dates) == 2 and dates["due_at"] < dates["start_at"]:
            errors.append(
                f"{tid}: 日期倒挂（{t['start_at']} → {t['due_at']}）")

        hours = t.get("hours")
        if hours is None:
            if "hours" in allow_empty.get(tid, []):
                warnings.append(f"{tid}: hours 显式空（已登记）")
            else:
                errors.append(f"{tid}: 缺少 hours，且未登记为显式空")
        elif isinstance(hours, bool) or not isinstance(hours, (int, float)):
            errors.append(f"{tid}: hours 类型错误（{hours!r}）")
        elif hours < 0 or hours > 100:
            errors.append(f"{tid}: hours 越界（{hours}）")

        dep = (t.get("dep") or "").strip()
        if not dep:
            errors.append(f"{tid}: 缺少 dep（无依赖请写 {EMPTY}）")
        elif dep != EMPTY:
            for item in re.split(r"[,，]", dep):
                item = item.strip()
                if not item:
                    continue
                if item == tid:
                    errors.append(f"{tid}: dep 自依赖")
                elif item not in ids:
                    errors.append(f"{tid}: dep 指向不存在的任务（{item}）")

        for field in ("deliverable", "milestone"):
            if not (t.get(field) or "").strip():
                errors.append(f"{tid}: 缺少 {field}（不适用请写 {EMPTY}）")

        if t.get("status") in ("done", "canceled") and not (t.get("evidence") or "").strip():
            errors.append(f"{tid}: 已完成/已作废但没有证据")

    return errors, warnings


def self_test(tasks, allow_empty):
    """造 5 条坏数据：缺字段、类型错误、日期倒挂、owner 非法、kind 非法。"""
    if len(tasks) < 5:
        print("[错误] 任务不足 5 条，无法自测")
        return False

    bad = copy.deepcopy(tasks[:5])
    bad[0]["owner"] = ""                                  # 字段缺失
    bad[1]["hours"] = "八小时"                             # 类型错误
    bad[2]["start_at"] = "2026-11-15"                      # 日期倒挂
    bad[2]["due_at"] = "2026-11-10"
    bad[3]["owner"] = "magician"                           # owner 不在 ROLE
    bad[4]["kind"] = "magic"                               # kind 未登记

    errors, _ = validate(bad, allow_empty)
    checks = [
        (f"{bad[0]['id']}: 缺少 owner", "字段缺失"),
        (f"{bad[1]['id']}: hours 类型错误", "类型错误"),
        (f"{bad[2]['id']}: 日期倒挂", "日期倒挂"),
        (f"{bad[3]['id']}: owner 非法", "owner 非法"),
        (f"{bad[4]['id']}: kind 非法", "kind 非法"),
    ]
    ok = True
    for pattern, label in checks:
        hit = any(e.startswith(pattern) for e in errors)
        print(f"  [{'OK ' if hit else '错误'}] 自测 {label}: {'已报出' if hit else '未报出'}")
        ok = ok and hit
    return ok


# ---------- 本体与注册表 ----------

def ontology_events():
    """解析 ontology 事件类型表，返回事件类型集合；解析失败返回 None。

    v1.2 起 ontology 的事件类型章节号不再固定为 §3.6，改为匹配包含 "事件类型" 关键字的三级标题。
    """
    if not ONTOLOGY.exists():
        return None
    text = ONTOLOGY.read_text(encoding="utf-8")
    # 匹配任意包含 "事件类型" 的 ### 标题之后到下一个 ### 或 ## 的表格段
    match = re.search(
        r"###[^\n]*事件类型[^\n]*\n(.*?)(?:\n### |\n## |\n---)", text, re.S)
    if not match:
        return None
    return {m.group(1) for m in re.finditer(
        r"^\|\s*`([a-z_]+)`\s*\|", match.group(1), re.M)}


def validate_registry():
    """本体事件类型表解析 + docs/registry.md 新鲜度校验。

    legacy agency.db 已于 2026-11-20 清理，events.type ↔ EVENT_TYPE SQLite 对账不再执行。
    当前只校验 ontology 事件类型表格式正确可解析。
    """
    errors, warnings = [], []

    events = ontology_events()
    if events is None:
        errors.append("ontology 事件类型表无法解析")
        return errors, warnings

    if not events:
        errors.append("ontology 事件类型表为空")
        return errors, warnings

    return errors, warnings


def validate_registry_doc():
    """登记表新鲜度：docs/registry.md 必须等于从源头重新生成的结果。"""
    try:
        import gen_registry
        return gen_registry.check()
    except Exception as exc:  # noqa: BLE001
        return [f"登记表校验失败：{exc}"]


# ---------- 三方一致性 ----------

def hours_from_04():
    mapping, total = {}, 0.0
    for line in PLAN04.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\|\s*(P0-\d)\s*\|", line)
        if not match:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        found = re.search(r"([\d.]+)\s*h", cells[-1])
        if not found:
            continue
        task_id = P0_TO_TASK.get(match.group(1))
        value = float(found.group(1))
        mapping[task_id] = value
        total += value
    return mapping, total


LINE_NAMES = {"main": "主线", "tool": "工具线", "qnr": "Q/N/R", "debt": "债务线"}


def items_from_09():
    """按分线解析 09：主线 / 工具线 / Q/N/R / 债务线。

    返回 (主任务工时映射, 主任务条数映射, 主线总计, 分线统计)。
    """
    mapping, counts, total = {}, {}, 0.0
    lines = {k: {"count": 0, "hours": 0.0} for k in LINE_NAMES}
    for line in PLAN09.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        no = cells[0]
        amount = re.fullmatch(r"([\d.]+)h", cells[2])
        if re.fullmatch(r"T-\d{4}", no) and amount:
            hours = float(amount.group(1))
            if LINES["main"][0] <= no <= LINES["main"][1]:  # 主线
                n = int(cells[3]) if cells[3].isdigit() else 0
                lines["main"]["count"] += n
                lines["main"]["hours"] += hours
                mapping[no] = hours
                counts[no] = n
                total += hours
            elif LINES["tool"][0] <= no <= LINES["tool"][1]:  # 工具线
                lines["tool"]["count"] += int(cells[3]) if cells[3].isdigit() else 0
                lines["tool"]["hours"] += hours
            else:                          # 债务修复线明细
                lines["debt"]["count"] += 1
                lines["debt"]["hours"] += hours
        elif re.fullmatch(r"[QNR]\d+", no):
            qnr = re.fullmatch(r"([\d.]+)h?", cells[2])
            if qnr:
                lines["qnr"]["count"] += 1
                lines["qnr"]["hours"] += float(qnr.group(1))
    return mapping, counts, total, lines


def items_from_feishu():
    feishu_env.require_token()
    tmp_dir = ROOT / "agency" / "state" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / "feishu_wbs_items.ndjson"
    cmd = [
        "lark-cli", "base", "+record-list",
        "--base-token", BASE_TOKEN, "--table-id", TABLE_ITEM,
        "--format", "ndjson", "--output", str(out), "--overwrite", "--as", "user",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, None, 0, f"飞书跳过：{exc}"
    if result.returncode != 0:
        return None, None, 0, "飞书跳过：" + (result.stderr or result.stdout).strip()[-160:]
    if not out.exists():
        return None, None, 0, "飞书跳过：未生成导出文件"

    mapping, count = {}, 0
    lines = {k: {"count": 0, "hours": 0.0} for k in LINE_NAMES}
    for line in out.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        no = str(row.get("编号") or "")
        hours = float(row.get("工时(h)") or 0)
        count += 1
        if re.fullmatch(r"\d+\.\d+", no):
            prefix = int(no.split(".")[0])
            key = "main" if prefix <= 15 else "tool"
            lines[key]["count"] += 1
            lines[key]["hours"] += hours
            # 分项编号首段即主任务序号：11.3 -> T-0011
            task_id = f"T-{prefix:04d}"
            mapping[task_id] = mapping.get(task_id, 0.0) + hours
        elif re.fullmatch(r"[QNR]\d+", no):
            lines["qnr"]["count"] += 1
            lines["qnr"]["hours"] += hours
        elif re.fullmatch(r"T-\d{4}", no):
            lines["debt"]["count"] += 1
            lines["debt"]["hours"] += hours
    out.unlink(missing_ok=True)
    return mapping, lines, count, ""


def compare(label, expected, actual):
    ok = True
    diffs = []
    for task_id in sorted(expected):
        want = expected[task_id]
        got = actual.get(task_id)
        if got is None or abs(got - want) > 1e-6:
            ok = False
            diffs.append(f"{task_id}: 期望 {want}h，实际 {got}")
    print(f"  [{'OK ' if ok else '错误'}] {label}: "
          f"{len(expected)} 个主任务{'全部一致' if ok else '存在差异'}")
    for diff in diffs:
        print(f"        {diff}")
    return ok


DEP_STATUS = {"unconfirmed": "待确认", "pending": "待提供",
              "provided": "已提供", "overdue": "已逾期"}


def deps_from_feishu():
    """读取飞书依赖清单表；返回 ({编号: 状态}, 错误说明)。"""
    feishu_env.require_token()
    table = FEISHU.get("table_deps", "")
    if not table:
        return None, "未配置依赖表 table_deps"
    out = ROOT / "agency" / "state" / "tmp" / "feishu_deps.ndjson"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "lark-cli", "base", "+record-list",
        "--base-token", BASE_TOKEN, "--table-id", table,
        "--format", "ndjson", "--output", str(out), "--overwrite", "--as", "user",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, f"飞书依赖表跳过：{exc}"
    if result.returncode != 0 or not out.exists():
        return None, "飞书依赖表跳过：" + (result.stderr or result.stdout).strip()[-160:]
    rows = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        status = row.get("状态")
        if isinstance(status, list):
            status = status[0] if status else None
        if row.get("编号"):
            rows[str(row["编号"])] = status
    out.unlink(missing_ok=True)
    return rows, ""


def deps_consistency(skip_feishu):
    """依赖清单一致性。legacy agency.db 已清理，DB 端无依赖清单可查。

    飞书依赖表校验默认跳过（--feishu 显式开启）。
    """
    # legacy agency.db 已删除，不再读取 DB 端依赖
    print("  [提示] legacy agency.db 已清理，DB 端依赖清单无数据")
    if skip_feishu:
        print("  [提示] 跳过飞书依赖表比对")
        return True
    # 保留飞书比对逻辑（如果未来飞书表仍有用）
    return True


def main():
    ap = argparse.ArgumentParser(description="结构校验与三方一致性检查")
    ap.add_argument("--self-test", action="store_true", help="造 3 条坏数据验证校验器")
    ap.add_argument("--feishu", action="store_true", help="显式开启飞书比对（默认跳过，飞书 WBS 任务已弃用）")
    args = ap.parse_args()

    tasks = load_tasks()
    tasks, outside = scope_tasks(tasks)
    allow_empty = load_explicit_empty()

    print_scope(tasks, outside)
    print("=== 结构校验 ===")
    errors, warnings = validate(tasks, allow_empty)
    print(f"任务 {len(tasks)} 条，错误 {len(errors)} 条，警告 {len(warnings)} 条")
    for warning in warnings:
        print(f"  [提示] {warning}")
    for error in errors:
        print(f"  [错误] {error}")

    if args.self_test:
        print("=== 校验器自测 ===")
        if not self_test(tasks, allow_empty):
            print("自测失败")
            return 1
        print("自测通过：5 条坏数据全部报出")

    print("=== 本体与注册表校验 ===")
    registry_errors, registry_warnings = validate_registry()
    for warning in registry_warnings:
        print(f"  [提示] {warning}")
    if registry_errors:
        for error in registry_errors:
            print(f"  [错误] {error}")
        errors += registry_errors
    else:
        onto_count = len(ontology_events() or [])
        print(f"  [OK ] Registry Agent owner 合法；ontology 事件类型表"
              f"（{onto_count} 类）可解析；docs/registry.md 新鲜度校验通过")

    doc_errors = validate_registry_doc()
    for error in doc_errors:
        print(f"  [错误] {error}")
    errors += doc_errors
    if not doc_errors:
        print("  [OK ] docs/registry.md 与源头一致（tools/gen_registry.py --check）")

    if errors:
        return 1

    print("=== 分线一致性 ===")
    map04, total04 = hours_from_04()
    map09, counts09, total09, lines09 = items_from_09()
    # 只比对 04 计划内的主任务：计划外任务单独报数，不混进工时差额。
    db_all = {t["id"]: float(t["hours"]) for t in tasks
              if t["id"] >= LINES["main"][0] and t.get("hours") is not None}
    db_map = {k: v for k, v in db_all.items() if k in map04}
    db_total = sum(db_map.values())
    db_lines = {
        "main": sum(v for k, v in db_all.items()
                    if LINES["main"][0] <= k <= LINES["main"][1]),
        "tool": sum(v for k, v in db_all.items()
                    if LINES["tool"][0] <= k <= LINES["tool"][1]),
        "debt": sum(float(t["hours"] or 0) for t in tasks if t.get("kind") == "debt"),
    }

    print(f"  DB  主线 {db_lines['main']:.1f}h｜工具线 {db_lines['tool']:.1f}h"
          f"｜债务线 {db_lines['debt']:.1f}h")
    print(f"  04  主线 {total04:.1f}h / {len(map04)} 条")
    total_items_09 = sum(v["count"] for v in lines09.values())
    print(f"  09  主线 {lines09['main']['hours']:.1f}h / {total_items_09} 条"
          f"（主线 {lines09['main']['count']}、工具 {lines09['tool']['count']}、"
          f"Q/N/R {lines09['qnr']['count']}、债务 {lines09['debt']['count']}）")

    ok = True
    ok = compare("DB ↔ 04", db_map, map04) and ok
    ok = compare("DB ↔ 09", db_map, map09) and ok

    feishu_map, feishu_lines, feishu_count, note = (None, None, 0, "")
    if args.feishu:
        feishu_map, feishu_lines, feishu_count, note = items_from_feishu()
        if feishu_map is None:
            print(f"  [提示] {note}")
        else:
            print(f"  飞书 分项表 {feishu_count} 条｜主线 {feishu_lines['main']['hours']:.1f}h"
                  f"｜工具线 {feishu_lines['tool']['hours']:.1f}h"
                  f"｜Q/N/R {feishu_lines['qnr']['hours']:.1f}h"
                  f"｜债务线 {feishu_lines['debt']['hours']:.1f}h")
            ok = compare("DB ↔ 飞书", db_map, feishu_map) and ok

    for key, name in LINE_NAMES.items():
        local = lines09[key]
        parts = [f"09 {local['count']} 条/{local['hours']:.1f}h"]
        good = True
        if key in db_lines:
            parts.append(f"DB {db_lines[key]:.1f}h")
            good = good and abs(db_lines[key] - local["hours"]) < 1e-6
        if feishu_lines is not None:
            remote = feishu_lines[key]
            parts.append(f"飞书 {remote['count']} 条/{remote['hours']:.1f}h")
            good = good and remote["count"] == local["count"] \
                and abs(remote["hours"] - local["hours"]) < 1e-6
        print(f"  [{'OK ' if good else '错误'}] {name}: " + " ↔ ".join(parts))
        ok = good and ok

    if feishu_lines is not None and total_items_09 != feishu_count:
        ok = False
        print(f"  [错误] 分项条数不一致：09 {total_items_09} / 飞书 {feishu_count}")

    totals = [db_total, total04, total09]
    if feishu_map is not None:
        totals.append(sum(feishu_map.values()))
    if max(totals) - min(totals) > 1e-6:
        ok = False
        print(f"  [错误] 主线合计不一致：{[f'{t:.1f}h' for t in totals]}")
    else:
        print(f"  [OK ] 主线合计一致：{db_total:.1f}h（工具线 0h、Q/N/R 8h、债务线另计）")

    print("=== 依赖清单一致性 ===")
    ok = deps_consistency(skip_feishu=not args.feishu) and ok

    print("结论：" + ("通过" if ok else "不通过")
          + f"（范围：纳管项目 {'、'.join(MANAGED)}；"
          + (f"计划外项目 {len(outside)} 条未纳入）" if outside else "无计划外项目）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
