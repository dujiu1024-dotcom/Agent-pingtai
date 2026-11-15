#!/usr/bin/env python3
"""存量任务字段规范化迁移（分项 13.1 / 13.2 / 13.5）

做什么：
- 给 v1.1 之前的历史任务补 dep、deliverable、milestone；
- 只填空值，已有值一律不动，因此可重复执行（幂等）；
- 迁移前自动备份数据库，失败回滚并给出还原命令；
- 输出迁移清单 agency/state/migrations/2026-11-11-fields.json，供结构校验读取「显式空」登记。

不做什么：
- 不改 hours。T-0001~T-0007 属 v1.1 之前的历史任务，当时无工时口径，
  hours 保持 NULL 并在清单里登记为显式空，不臆造数字。

用法：
    python3 tools/migrate_fields.py --dry-run   # 只看会改什么
    python3 tools/migrate_fields.py             # 备份后执行
    python3 tools/migrate_fields.py --restore agency/state/backups/xxx.db
"""

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402

VERSION = "2026-11-11-fields"
MANIFEST = ROOT / "agency" / "state" / "migrations" / f"{VERSION}.json"

# 显式空标记：字段值写 "—"，表示已确认该字段不适用，而不是漏填
EMPTY = "—"

# 历史任务字段来源：逐条对应 02/03/04 文档与作废记录
FILL = {
    "T-0001": {"dep": EMPTY, "deliverable": "03 方案确认单（已签）", "milestone": EMPTY},
    "T-0002": {"dep": "T-0001", "deliverable": "04 项目计划 v1.1、09 分项计划", "milestone": EMPTY},
    "T-0003": {"dep": EMPTY, "deliverable": "—（作废：范围变更）", "milestone": EMPTY},
    "T-0004": {"dep": EMPTY, "deliverable": "—（作废：范围变更，诊断包并入 T-0011）", "milestone": EMPTY},
    "T-0005": {"dep": EMPTY, "deliverable": "—（作废：范围变更，定位卡与话术并入 T-0012）", "milestone": EMPTY},
    "T-0006": {"dep": EMPTY, "deliverable": "—（作废：范围变更，彩排改由 T-0014 执行）", "milestone": EMPTY},
    "T-0007": {"dep": EMPTY, "deliverable": "—（作废：范围变更，上线改由 T-0015 执行）", "milestone": EMPTY},
    # T-0008 是 P0 第一项，无前置依赖，显式写 "—"
    "T-0008": {"dep": EMPTY},
}

LEGACY = ("T-0001", "T-0002", "T-0003", "T-0004", "T-0005", "T-0006", "T-0007")
EXPLICIT_EMPTY = {tid: ["hours"] for tid in LEGACY}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def backup_db(con):
    dest_dir = ROOT / "agency" / "state" / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"agency_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    dst = sqlite3.connect(dest)
    con.backup(dst)
    dst.close()
    return dest


def restore(src):
    src = Path(src)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print(f"[错误] 备份不存在: {src}")
        return 1
    db_path = Path(dbm.DB_PATH)
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink()
    shutil.copy2(src, db_path)
    print(f"已还原: {src} -> {db_path}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="存量任务字段规范化迁移")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写库、不备份")
    ap.add_argument("--restore", metavar="DB", help="用指定备份还原后退出")
    args = ap.parse_args()

    if args.restore:
        return restore(args.restore)

    con = dbm.connect()
    rows = list(con.execute("SELECT id, dep, deliverable, milestone FROM tasks ORDER BY id"))
    if not rows:
        print("[错误] tasks 表为空，请先执行 python3 agency.py init")
        return 1

    plan = []
    for row in rows:
        target = FILL.get(row["id"])
        if not target:
            continue
        patch = {}
        for key, value in target.items():
            current = row[key]
            if current is None or current == "":
                patch[key] = value
        if patch:
            plan.append((row["id"], patch))

    if args.dry_run:
        print(f"[dry-run] 将更新 {len(plan)} 条，跳过 {len(rows) - len(plan)} 条")
        for tid, patch in plan:
            print(f"  {tid}: " + "，".join(f"{k}={v}" for k, v in patch.items()))
        return 0

    backup_path = None
    if plan:
        backup_path = backup_db(con)
        print(f"已备份: {backup_path.relative_to(ROOT)}")

    updated = 0
    try:
        for tid, patch in plan:
            cols = ", ".join(f"{k}=?" for k in patch)
            con.execute(
                f"UPDATE tasks SET {cols}, updated_at=? WHERE id=?",
                (*patch.values(), now_iso(), tid),
            )
            updated += 1
        con.commit()
    except Exception as exc:  # noqa: BLE001
        con.rollback()
        print(f"[错误] 迁移失败，已回滚: {exc}")
        if backup_path:
            print(f"如需手工还原: python3 tools/migrate_fields.py --restore {backup_path.relative_to(ROOT)}")
        return 1

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": VERSION,
        "generated_at": now_iso(),
        "backup": str(backup_path.relative_to(ROOT)) if backup_path else "",
        "total": len(rows),
        "updated": updated,
        "skipped": len(rows) - updated,
        "explicit_empty": EXPLICIT_EMPTY,
        "changes": [{"id": tid, **patch} for tid, patch in plan],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"更新 {updated} 条，跳过 {len(rows) - updated} 条")
    print(f"清单: {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
