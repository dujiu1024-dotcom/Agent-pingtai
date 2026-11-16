#!/usr/bin/env python3
"""用标准库 trace 跑全量测试并产出 coverage.json。

分母递归统计模块 + 所有嵌套函数/方法的语句行（dis.findlinestarts 递归 code
对象的 co_consts），执行行来自 trace 的逐行计数。额外做语句数下限自检：
分母过小说明统计方法坏了，直接失败，而不是给出好看但虚假的百分比。
"""

import dis
import json
import sys
import trace
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 语句数下限：低于此值说明只统计了模块顶层，必须视为方法错误
FLOORS = {
    "agency.py": 800,
    "db.py": 150,       # 实际 185 条可执行行；文件主体是 SQL 字符串，300 不适用
    "console.py": 300,
    "memory.py": 100,
    "vectors.py": 150,
}
# 必须被测试 import 到的文件；executed=0 时明确报“未覆盖”
MUST_IMPORT = ("listener.py", "feishu_bot.py")


def executable_lines(path):
    """模块 + 全部嵌套函数/方法的可执行行号。"""
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    lines = set()

    def walk(obj):
        for _, lineno in dis.findlinestarts(obj):
            if lineno:
                lines.add(lineno)
        for const in obj.co_consts:
            if isinstance(const, types.CodeType):
                walk(const)

    walk(code)
    return lines


def main():
    tracer = trace.Trace(
        count=1, trace=0,
        ignoredirs=[sys.prefix, sys.exec_prefix, str(ROOT / ".venv")],
    )

    def run():
        suite = unittest.TestLoader().discover(
            str(ROOT / "tests"), top_level_dir=str(ROOT))
        result = unittest.TextTestRunner(verbosity=0).run(suite)
        return result.wasSuccessful()

    passed = tracer.runfunc(run)
    counts = tracer.results().counts
    hits, executed = {}, {}
    for (filename, lineno), n in counts.items():
        path = Path(filename).resolve()
        if path.suffix != ".py" or ROOT not in path.parents or not n:
            continue
        hits.setdefault(path, set()).add(lineno)
        executed[path] = executed.get(path, 0) + n

    targets = {p.resolve() for p in ROOT.glob("*.py")}
    targets |= set(hits)
    files, total_exec, total_stmt, errors = {}, 0, 0, []
    for path in sorted(targets):
        if ROOT not in path.parents:
            continue
        try:
            statements = executable_lines(path)
        except (OSError, SyntaxError) as e:
            errors.append("%s 无法统计：%s" % (path.name, e))
            continue
        if not statements:
            continue
        ran = hits.get(path, set()) & statements
        rel = str(path.relative_to(ROOT))
        floor = FLOORS.get(rel)
        if floor and len(statements) < floor:
            errors.append("%s 语句数 %d < 下限 %d，统计口径异常"
                          % (rel, len(statements), floor))
        imported = executed.get(path, 0) > 0
        if rel in MUST_IMPORT and not imported:
            errors.append("%s 未被测试 import（未覆盖）" % rel)
        files[rel] = {
            "executed": len(ran),
            "statements": len(statements),
            "percent": round(100.0 * len(ran) / len(statements), 1),
            "imported": imported,
        }
        total_exec += len(ran)
        total_stmt += len(statements)

    report = {
        "tool": "stdlib trace (recursive code objects)",
        "tests_passed": passed,
        "total": {
            "executed": total_exec,
            "statements": total_stmt,
            "percent": round(100.0 * total_exec / total_stmt, 1) if total_stmt else 0.0,
        },
        "files": dict(sorted(files.items())),
        "errors": errors,
    }
    (ROOT / "coverage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("coverage.json 已生成：%s" % (ROOT / "coverage.json"))
    print("总覆盖率 %.1f%%（%d/%d 行），测试通过=%s"
          % (report["total"]["percent"], total_exec, total_stmt, passed))
    for name in ("agency.py", "db.py", "console.py", "memory.py",
                 "vectors.py", "listener.py", "feishu_bot.py"):
        if name in files:
            f = files[name]
            flag = "" if f["imported"] else "（未覆盖：未被测试 import）"
            print("  %-16s %5.1f%%  (%d/%d)%s"
                  % (name, f["percent"], f["executed"], f["statements"], flag))
    if errors:
        print("统计自检失败：")
        for e in errors:
            print("  - " + e)
        sys.exit(1)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
