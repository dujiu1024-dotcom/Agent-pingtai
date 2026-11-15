#!/usr/bin/env python3
"""登记表生成器：从现有源头汇总，不手写第二份真相。

来源（每项都已有唯一出处，本文件只汇总，不复制规则）：
  db.ROLE                         角色注册表
  agents/ontology.md              本体（版本 / 角色 / 事件类型）
  feishu_commands.COMMAND_SPECS   飞书指令表
  agency/limits.json              模型限额实测
  docs/erd.md                     表结构（由 tools/erd.py 生成）
  agency.EXPORT_FILES/DIRS        交付导出清单
  tools/*.py、*.py                工具脚本与模块

用法：
  python3 tools/gen_registry.py           生成 docs/registry.md
  python3 tools/gen_registry.py --check   校验登记表是否与源头一致（过期即失败）
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agency  # noqa: E402
import db as dbm  # noqa: E402
import feishu_commands  # noqa: E402

REGISTRY = ROOT / "docs" / "registry.md"
ONTOLOGY = ROOT / "agents" / "ontology.md"
LIMITS = ROOT / "agency" / "limits.json"
ERD = ROOT / "docs" / "erd.md"


def _doc_line(path):
    """模块 docstring 首行；只解析不执行。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return ""
    doc = ast.get_docstring(tree) or ""
    lines = doc.strip().splitlines()
    return lines[0].strip().replace("|", "｜") if lines else ""


def _ontology_meta():
    if not ONTOLOGY.exists():
        return {"version": "?", "updated": "?", "roles": [], "events": []}
    text = ONTOLOGY.read_text(encoding="utf-8")
    version = re.search(r"^- 版本：(.+)$", text, re.M)
    updated = re.search(r"^- 更新：(.+)$", text, re.M)
    sec = re.search(r"### 2\.1[^\n]*\n(.*?)(?=\n### |\n## )", text, re.S)
    roles = []
    if sec:
        for line in sec.group(1).splitlines():
            cell = re.match(r"\|\s*([^|]+?)\s*\|", line)
            if not cell:
                continue
            name = cell.group(1).strip()
            if name == "角色" or set(name) <= set("-: "):
                continue
            roles.append(name)
    ev = re.search(r"### 3\.6[^\n]*\n(.*?)(?=\n### |\n## )", text, re.S)
    events = sorted({m.group(1) for m in re.finditer(
        r"^\|\s*`([a-z_]+)`\s*\|", ev.group(1), re.M)}) if ev else []
    return {
        "version": version.group(1).strip() if version else "?",
        "updated": updated.group(1).strip() if updated else "?",
        "roles": roles,
        "events": events,
    }


def _limits():
    if not LIMITS.exists():
        return {}
    return json.loads(LIMITS.read_text(encoding="utf-8"))


def _erd_tables():
    """只取表名/列数/外键数；行数随运行变化，不进登记表。"""
    if not ERD.exists():
        return []
    text = ERD.read_text(encoding="utf-8")
    sec = re.search(r"## 表清单\n(.*)$", text, re.S)
    if not sec:
        return []
    out = []
    for line in sec.group(1).splitlines():
        cell = re.match(r"\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", line)
        if cell:
            out.append((cell.group(1), int(cell.group(2)), int(cell.group(3))))
    return out


def _role_dirs():
    return sorted(p.parent.name for p in (ROOT / "agents").glob("*/ROLE.md"))


def _imported_by(mod):
    names = []
    for path in sorted(ROOT.glob("*.py")):
        if path.stem == mod:
            continue
        if re.search(r"^(?:import|from)\s+%s\b" % re.escape(mod),
                     path.read_text(encoding="utf-8"), re.M):
            names.append(path.stem)
    return names


def _ok(flag):
    return "✓" if flag else "✗"


def render():
    onto = _ontology_meta()
    limits = _limits()
    erd = _erd_tables()
    dirs = _role_dirs()
    role_gap = sorted(set(dbm.ROLE) ^ set(dirs))
    onto_events = onto["events"]
    event_gap = sorted(set(onto_events) ^ set(dbm.EVENT_TYPE))
    modules = sorted(p for p in ROOT.glob("*.py"))
    missing_export = [p.name for p in modules if p.name not in agency.EXPORT_FILES]
    # 被引用却没入包才是缺陷；无人引用的模块（alerts/publisher）按决定不入包。
    missing_export_bad = [name for name in missing_export
                          if _imported_by(Path(name).stem)]

    lines = [
        "# 登记表｜Agent 平台（自动生成）",
        "",
        "> 本文件由 `python3 tools/gen_registry.py` 从现有源头生成，**不要手改**；",
        "> 校验是否过期：`python3 tools/gen_registry.py --check`"
        "（已接入 `tools/check_consistency.py`）。",
        "",
        "## 1. 角色注册表（`db.ROLE`）",
        "",
        "| 角色 | 角色卡 `agents/<role>/ROLE.md` |",
        "|---|---|",
    ]
    for role in dbm.ROLE:
        card = ROOT / "agents" / role / "ROLE.md"
        lines.append(f"| `{role}` | {_ok(card.exists())} |")

    lines += [
        "",
        "## 2. 本体（`agents/ontology.md`）",
        "",
        f"- 版本：{onto['version']}｜更新：{onto['updated']}",
        f"- 2.1 角色（{len(onto['roles'])}）："
        + ("、".join(onto["roles"]) if onto["roles"] else "未解析到"),
        f"- 3.6 事件类型（{len(onto_events)}）："
        + ("、".join(f"`{e}`" for e in onto_events) if onto_events else "未解析到"),
        "",
        "## 3. 指令注册表（`feishu_commands.COMMAND_SPECS`）",
        "",
        "| 指令 | 说明 |",
        "|---|---|",
    ]
    for cmd, desc in feishu_commands.COMMAND_SPECS:
        lines.append(f"| `{cmd}` | {desc} |")

    lines += [
        "",
        "## 4. 模型限额（`agency/limits.json`）",
        "",
        f"实测日期：{limits.get('measured_at', '?')}",
        f"口径：{limits.get('note', '')}",
        "",
        "| 角色 | 可用模型 |",
        "|---|---|",
    ]
    for role, models in (limits.get("roles") or {}).items():
        lines.append(f"| `{role}` | {'、'.join(models)} |")
    lines += [
        "",
        "| 模型 | 可调用 | 最大 prompt | TPM | RPM | 并发 | 恢复(s) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, spec in (limits.get("models") or {}).items():
        lines.append(
            "| `%s` | %s | %s | %s | %s | %s | %s |" % (
                name, _ok(spec.get("callable")), spec.get("max_prompt_tokens", "—"),
                spec.get("tpm", "—"), spec.get("rpm", "—"),
                spec.get("concurrency", "—"), spec.get("recovery_s", "—")))

    lines += [
        "",
        "## 5. 数据模型（`docs/erd.md`，由 `tools/erd.py` 生成）",
        "",
        "| 表 | 列数 | 外键数 |",
        "|---|---:|---:|",
    ]
    for name, cols, fks in erd:
        lines.append(f"| `{name}` | {cols} | {fks} |")

    lines += [
        "",
        "## 6. 交付导出清单（`agency.EXPORT_FILES` / `EXPORT_DIRS`）",
        "",
        f"文件（{len(agency.EXPORT_FILES)}）："
        + "、".join(f"`{x}`" for x in agency.EXPORT_FILES),
        "",
        f"目录（{len(agency.EXPORT_DIRS)}）："
        + "、".join(f"`{x}`" for x in agency.EXPORT_DIRS),
        "",
        f"未入包（{len(missing_export)}）："
        + ("、".join(f"`{x}`" for x in missing_export) or "无"),
        "",
        "## 7. 工具脚本（`tools/*.py`）",
        "",
        "| 脚本 | 用途（docstring 首行） |",
        "|---|---|",
    ]
    for path in sorted((ROOT / "tools").glob("*.py")):
        lines.append(f"| `tools/{path.name}` | {_doc_line(path)} |")

    lines += [
        "",
        "## 8. 顶层模块（`*.py`）",
        "",
        "| 模块 | 用途 | 导出包 | 被这些模块引用 |",
        "|---|---|---|---|",
    ]
    for path in modules:
        refs = _imported_by(path.stem)
        lines.append("| `%s` | %s | %s | %s |" % (
            path.name, _doc_line(path),
            _ok(path.name in agency.EXPORT_FILES),
            "、".join(f"`{r}`" for r in refs) or "—"))

    roles_ok = not role_gap and len(dbm.ROLE) == len(dirs)
    onto_roles_ok = len(onto["roles"]) == len(dbm.ROLE)
    events_ok = not event_gap
    lines += [
        "",
        "## 9. 一致性摘要（生成时计算）",
        "",
        f"- {_ok(roles_ok)} 角色：`ROLE` {len(dbm.ROLE)} 个 ↔ "
        f"`agents/*/ROLE.md` {len(dirs)} 个"
        + ("" if roles_ok else "；差异：" + "、".join(role_gap)),
        f"- {_ok(onto_roles_ok)} 本体 2.1 角色 {len(onto['roles'])} 个 ↔ "
        f"`ROLE` {len(dbm.ROLE)} 个",
        f"- {_ok(events_ok)} 本体 3.6 事件 {len(onto_events)} 类 ↔ "
        f"`EVENT_TYPE` {len(dbm.EVENT_TYPE)} 类"
        + ("" if events_ok else "；差异：" + "、".join(event_gap)),
        f"- {_ok(not missing_export_bad)} 被引用的顶层模块全部入导出包"
        + ("" if not missing_export_bad else "；缺口："
           + "、".join(f"{m} ← {'、'.join(_imported_by(Path(m).stem))}"
                      for m in missing_export_bad))
        + (f"；未入包但无引用：{'、'.join(missing_export)}"
           if missing_export and not missing_export_bad else ""),
        "",
    ]
    return "\n".join(lines)


def check():
    """返回错误列表；空列表表示登记表与源头一致。"""
    errors = []
    if not REGISTRY.exists():
        errors.append("缺少 docs/registry.md，先运行 python3 tools/gen_registry.py")
        return errors
    if REGISTRY.read_text(encoding="utf-8") != render():
        errors.append("docs/registry.md 已过期（源头已改），"
                      "运行 python3 tools/gen_registry.py 重新生成")
    dirs = set(_role_dirs())
    gap = sorted(set(dbm.ROLE) ^ dirs)
    if gap:
        errors.append("ROLE 与 agents/*/ROLE.md 不一致：" + "、".join(gap))
    bad = [p.name for p in ROOT.glob("*.py")
           if p.name not in agency.EXPORT_FILES and _imported_by(p.stem)]
    if bad:
        errors.append("模块被引用但未入导出包：" + "、".join(sorted(bad)))
    return errors


def main():
    ap = argparse.ArgumentParser(description="生成/校验登记表")
    ap.add_argument("--check", action="store_true", help="只校验是否与源头一致")
    args = ap.parse_args()
    if args.check:
        errors = check()
        for error in errors:
            print("[错误] " + error)
        if errors:
            return 1
        print(f"[OK ] docs/registry.md 与源头一致（{REGISTRY.stat().st_size} 字节）")
        return 0
    text = render()
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(text, encoding="utf-8")
    print(f"已生成 docs/registry.md（{len(text)} 字）："
          f"角色 {len(dbm.ROLE)}、指令 {len(feishu_commands.COMMAND_SPECS)}、"
          f"表 {len(_erd_tables())}、工具 {len(list((ROOT / 'tools').glob('*.py')))}、"
          f"模块 {len(list(ROOT.glob('*.py')))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
