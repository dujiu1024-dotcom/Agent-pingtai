#!/usr/bin/env python3
"""tools/erd.py — 从 SQLite 反推 ER 图（Mermaid）

只读 sqlite_master 与 PRAGMA，不改库。表结构变化后重跑即可。

用法：
  python3 tools/erd.py                        # agency/state/agency.db → docs/erd.md
  python3 tools/erd.py --db path/to.db --out docs/erd.md
"""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db as dbm  # noqa: E402

DEFAULT_OUT = ROOT / "docs" / "erd.md"
KEY_TYPES = ("TEXT", "INTEGER", "REAL", "BLOB", "NUMERIC")


def table_names(con):
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [r[0] if not isinstance(r, sqlite3.Row) else r["name"] for r in rows]


def mermaid_type(sql_type):
    base = (sql_type or "").split("(")[0].strip().upper()
    return base if base in KEY_TYPES else "TEXT"


def describe(con, table):
    """返回 (列清单, 外键清单)。列：name/type/pk；外键：from/table/to。"""
    columns = []
    for row in con.execute('PRAGMA table_info("%s")' % table):
        columns.append({
            "name": row["name"],
            "type": mermaid_type(row["type"]),
            "pk": bool(row["pk"]),
        })
    fks = []
    for row in con.execute('PRAGMA foreign_key_list("%s")' % table):
        fks.append({"from": row["from"], "table": row["table"], "to": row["to"]})
    return columns, fks


def render(con):
    """生成 Mermaid erDiagram + 表清单，返回 markdown 文本。"""
    tables = table_names(con)
    lines = ["# ERD｜agency.db 表结构", "",
             "> 由 `python3 tools/erd.py` 从 `sqlite_master` 与 `PRAGMA foreign_key_list` 自动生成，",
             "> 表结构变更后重跑即可，不手工维护。", "",
             "```mermaid", "erDiagram"]
    relations = []
    counts = []
    for table in tables:
        columns, fks = describe(con, table)
        fk_cols = {fk["from"] for fk in fks}
        lines.append("    %s {" % table)
        for col in columns:
            marks = []
            if col["pk"]:
                marks.append("PK")
            elif col["name"] in fk_cols:
                marks.append("FK")
            suffix = (" " + " ".join(marks)) if marks else ""
            lines.append("        %s %s%s" % (col["type"], col["name"], suffix))
        lines.append("    }")
        for fk in fks:
            relations.append('    %s ||--o{ %s : "%s"' % (fk["table"], table, fk["from"]))
        try:
            n = con.execute('SELECT COUNT(*) AS n FROM "%s"' % table).fetchone()["n"]
        except sqlite3.Error:
            n = 0
        counts.append((table, len(columns), len(fks), n))
    lines.extend(sorted(set(relations)))
    lines.extend(["```", "", "## 表清单", "",
                  "| 表 | 列数 | 外键数 | 行数 |", "|---|---:|---:|---:|"])
    for table, ncol, nfk, nrow in counts:
        lines.append("| `%s` | %d | %d | %d |" % (table, ncol, nfk, nrow))
    lines.append("")
    return "\n".join(lines)


def generate(db_path=None, out_path=None):
    path = Path(db_path or dbm.DB_PATH)
    if not path.exists():
        raise FileNotFoundError("数据库不存在：%s" % path)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        text = render(con)
    finally:
        con.close()
    out = Path(out_path or DEFAULT_OUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out, text


def main():
    parser = argparse.ArgumentParser(description="生成 ERD（Mermaid）")
    parser.add_argument("--db", default=str(dbm.DB_PATH))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    try:
        out, text = generate(args.db, args.out)
    except FileNotFoundError as exc:
        sys.exit(str(exc))
    print("已生成 %s（%d 行）" % (out, text.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
