#!/usr/bin/env python3
"""登记表生成器：从现有源头汇总，不手写第二份真相。

v1.2 起同时覆盖两套 Agent 管理架构，SSOT 为 Platform Registry（PostgreSQL）：
  老蓝图（legacy）：db.ROLE                         — 硬编码 10 个角色，在 legacy/v1-archive/db.py
  新实装（active）：  Platform Registry API          — PostgreSQL platform_registry_active 表
  本体（当前）：      agents/ontology.md              — 按新实装 SSOT 更新
  角色卡目录：        agents/*/ROLE.md               — 人类可读说明，与 Registry instructions 对齐

用法：
  python3 tools/gen_registry.py           生成 docs/registry.md
  python3 tools/gen_registry.py --check   校验登记表是否与源头一致（过期即失败）
"""

import argparse
import ast
import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "legacy" / "v1-archive"))

# legacy db（agency/state/agency.db）已于 2026-11-20 清理完毕，不再 import dbm。
# 老蓝图 ROLE 和 EVENT_TYPE 作为硬编码常量保留，用于对账表展示。
# LEGACY_EXPORT_FILES（根目录 agency.py launcher 已删）硬编码为空。
# feishu_commands.COMMAND_SPECS 从 legacy/v1-archive 归档读取。

import feishu_commands  # noqa: E402 — legacy 飞书指令定义

# —— 老蓝图常量（与 legacy/v1-archive/db.py 2026-11-20 版本一致）——
LEGACY_ROLE = (
    "chief-of-staff", "auditor", "pm", "planner", "secretary",
    "coach", "scout", "content", "finance", "producer",
)
LEGACY_EVENT_TYPE = (
    "project_created", "project_confirmed", "tasks_created", "task_created",
    "task_closed", "task_canceled", "task_reopened", "task_due_soon",
    "task_overdue", "task_blocked", "milestone_hit", "milestone_deviation",
    "gate_confirmed", "gate_revoked", "gate_blocked", "agent_run",
    "audit_flag", "check", "sync_conflict", "doc_ready_review",
    "contract_signed", "invoice_overdue", "dependency_overdue",
    "agent_circuit_break", "agent_low_output", "agent_role_overlap",
    "agent_subscribe_flood", "quarterly_review",
)

# LEGACY_EXPORT_FILES / EXPORT_DIRS（根目录 agency.py launcher 已删，老蓝图交付清单不再维护）
LEGACY_EXPORT_FILES: tuple[str, ...] = ()
LEGACY_EXPORT_DIRS: tuple[str, ...] = ()

REGISTRY = ROOT / "docs" / "registry.md"
ONTOLOGY = ROOT / "agents" / "ontology.md"
LIMITS = ROOT / "agency" / "limits.json"
ERD = ROOT / "docs" / "erd.md"

PLATFORM_BASE_URL = os.environ.get("PLATFORM_BASE_URL", "https://127.0.0.1:8000")


def _platform_agents():
    """从 Platform Registry API 拉当前实装的 agent 清单；连接失败返回 None。"""
    url = f"{PLATFORM_BASE_URL}/api/platform/v1/registry/agent?limit=200"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gen_registry/1.0"})
        ctx = __import__("ssl")._create_unverified_context()  # skip cert for localhost
        with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return [
            {
                "agent_id": item["item_id"],
                "version": item["version"],
                "name": (item.get("definition") or {}).get("name", item["item_id"]),
                "description": (item.get("definition") or {}).get("description", ""),
            }
            for item in payload.get("items", [])
        ]
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(f"[warn] Platform Registry 不可达（{exc}），新实装 agent 列表跳过")
        return None


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
    """解析 ontology.md 的角色表和事件类型。

    v1.2 起事件类型章节编号从固定的 §3.6 变为任意编号（当前 §3.4），
    正则改为匹配 "事件类型" 关键字而非硬编码章节号。
    角色表同理，改为匹配 "Agent 角色" 关键字。
    """
    if not ONTOLOGY.exists():
        return {"version": "?", "updated": "?", "roles": [], "events": []}
    text = ONTOLOGY.read_text(encoding="utf-8")
    version = re.search(r"^- 版本：(.+)$", text, re.M)
    updated = re.search(r"^- 更新：(.+)$", text, re.M)

    # 角色表：匹配包含 "Agent 角色" 的三级标题之后的表格
    sec = re.search(r"###[^\n]*Agent\s*角色[^\n]*\n(.*?)(?=\n### |\n## )", text, re.S)
    roles = []
    if sec:
        for line in sec.group(1).splitlines():
            # 跳过表头、分隔符、代码块
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.split("|")]
            # cells[0] 是空（以 | 开头），cells[1] 是第一列
            if len(cells) < 2:
                continue
            first = cells[1]
            if first in ("agent_id", "角色") or set(first.replace("`", "").replace("-", "").strip()) == 0:
                continue
            # 取出 agent_id 格式：`finance` 或 finance
            m = re.match(r"`([\w-]+)`", first)
            role_id = m.group(1) if m else first
            # 过滤：跳过纯分隔符（---）和表头以外的说明行
            if role_id and set(role_id) <= set("-"):
                continue
            roles.append(role_id)

    # 事件类型：匹配包含 "事件类型" 的三级标题之后的表格
    ev = re.search(r"###[^\n]*事件类型[^\n]*\n(.*?)(?=\n### |\n## )", text, re.S)
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
    platform = _platform_agents()

    modules = sorted(p for p in ROOT.glob("*.py"))
    missing_export = [p.name for p in modules if p.name not in LEGACY_EXPORT_FILES]
    missing_export_bad = [name for name in missing_export
                          if _imported_by(Path(name).stem)]

    lines = [
        "# 登记表｜Agent 平台（自动生成）",
        "",
        "> 本文件由 `python3 tools/gen_registry.py` 从现有源头生成，**不要手改**；",
        "> 校验是否过期：`python3 tools/gen_registry.py --check`。",
        "> 本项目同时存在老蓝图（legacy db.ROLE）和新实装（Platform Registry PostgreSQL）两套 Agent 定义，"
        "SSOT 为新实装。详见 `agents/ontology.md` 架构升级说明。",
        "",
        "## 1. Agent 角色",
        "",
        "### 1.1 新实装（Platform Registry PostgreSQL）— SSOT",
        "",
    ]
    if platform is not None:
        lines += [
            "| agent_id | version | name | 角色卡 `agents/<id>/ROLE.md` |",
            "|---|---|---|---|",
        ]
        for a in platform:
            card = ROOT / "agents" / a["agent_id"] / "ROLE.md"
            lines.append(f"| `{a['agent_id']}` | v{a['version']} | {a['name']} | {_ok(card.exists())} |")
    else:
        lines.append("| — | — | — | Platform Registry API 不可达，跳过 |")

    lines += [
        "",
        "### 1.2 老蓝图（legacy `db.ROLE`）— 理想形态，未全部落地",
        "",
        "| 角色 | 角色卡 `agents/<role>/ROLE.md` |",
        "|---|---|",
    ]
    for role in LEGACY_ROLE:
        card = ROOT / "agents" / role / "ROLE.md"
        lines.append(f"| `{role}` | {_ok(card.exists())} |")

    lines += [
        "",
        "## 2. 本体（`agents/ontology.md`）",
        "",
        f"- 版本：{onto['version']}｜更新：{onto['updated']}",
        f"- §2.1 角色（{len(onto['roles'])}）："
        + ("、".join(f"`{r}`" for r in onto["roles"]) if onto["roles"] else "未解析到"),
        f"- 事件类型（{len(onto['events'])}）："
        + ("、".join(f"`{e}`" for e in onto["events"]) if onto["events"] else "未解析到"),
        "",
        "## 3. 指令注册表（`feishu_commands.COMMAND_SPECS`，legacy）",
        "",
        "| 指令 | 说明 |",
        "|---|---|",
    ]
    for cmd, desc in feishu_commands.COMMAND_SPECS:
        lines.append(f"| `{cmd}` | {desc} |")

    lines += [
        "",
        "## 4. 模型限额（`agency/limits.json`，legacy）",
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
        "## 6. 交付导出清单（`LEGACY_EXPORT_FILES` / `EXPORT_DIRS`，legacy）",
        "",
        f"文件（{len(LEGACY_EXPORT_FILES)}）："
        + "、".join(f"`{x}`" for x in LEGACY_EXPORT_FILES),
        "",
        f"目录（{len(LEGACY_EXPORT_DIRS)}）："
        + "、".join(f"`{x}`" for x in LEGACY_EXPORT_DIRS),
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
            _ok(path.name in LEGACY_EXPORT_FILES),
            "、".join(f"`{r}`" for r in refs) or "—"))

    # ---- 一致性对账 ----
    legacy_set = set(LEGACY_ROLE)
    platform_set = {a["agent_id"] for a in platform} if platform else set()
    onto_set = set(onto["roles"])
    dirs_set = set(dirs)

    platform_onto_gap = sorted(platform_set ^ onto_set)
    platform_dirs_gap = sorted(platform_set - dirs_set)  # Platform 有但 ROLE.md 缺失
    dirs_platform_gap = sorted(dirs_set - platform_set)  # ROLE.md 有但 Platform 没有
    onto_legacy_gap = sorted(legacy_set - onto_set)      # 老蓝图有但 ontology 没写

    lines += [
        "",
        "## 9. 一致性对账（生成时计算）",
        "",
        "### 9.1 Platform Registry ↔ ontology §2.1",
        "",
    ]
    if platform is None:
        lines.append("- — Platform Registry API 不可达，跳过对账")
    else:
        lines += [
            f"- {_ok(not platform_onto_gap)} ontology §2.1 角色（{len(onto_set)}）"
            f" ↔ Platform Registry agent（{len(platform_set)}）"
            + ("" if not platform_onto_gap else "；差异：" + "、".join(platform_onto_gap)),
        ]

    lines += [
        "",
        "### 9.2 Platform Registry ↔ ROLE.md 目录",
        "",
    ]
    if platform is None:
        lines.append("- — Platform Registry API 不可达，跳过对账")
    else:
        lines += [
            f"- {_ok(not platform_dirs_gap)} Platform Registry agent（{len(platform_set)}）"
            f" ↔ ROLE.md 目录（{len(dirs_set)}）"
            + ("" if not platform_dirs_gap else f"；Platform 有但 ROLE.md 缺失：{'、'.join(platform_dirs_gap)}")
            + ("" if not dirs_platform_gap else f"；ROLE.md 有但 Platform 未注册：{'、'.join(dirs_platform_gap)}"),
        ]

    lines += [
        "",
        "### 9.3 老蓝图（legacy db.ROLE）↔ ontology",
        "",
        f"- {_ok(not onto_legacy_gap)} 老蓝图（{len(legacy_set)}）"
        f" ↔ ontology 本体（{len(onto_set)}）"
        + ("" if not onto_legacy_gap else f"；老蓝图有但 ontology 未记录：{'、'.join(onto_legacy_gap)}"
           + "（蓝图与实装不一致不自动标红，蓝图描述理想形态）"),
        f"- 本体事件类型（{len(onto['events'])}）类"
        f" ↔ 老蓝图 EVENT_TYPE（{len(LEGACY_EVENT_TYPE)}）类"
        + ("" if set(onto["events"]) <= set(LEGACY_EVENT_TYPE)
           else "；ontology 有但老蓝图没有："
           + "、".join(sorted(set(onto["events"]) - set(LEGACY_EVENT_TYPE)))),
        "",
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
    # Platform Registry ↔ ROLE.md 目录
    platform = _platform_agents()
    if platform is not None:
        platform_ids = {a["agent_id"] for a in platform}
        dirs_set = set(_role_dirs())
        missing = sorted(platform_ids - dirs_set)
        if missing:
            errors.append("Platform Registry agent 存在但 ROLE.md 缺失：" + "、".join(missing))
    bad = [p.name for p in ROOT.glob("*.py")
           if p.name not in LEGACY_EXPORT_FILES and _imported_by(p.stem)]
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

    # 统计 Platform Registry agent 数
    platform = _platform_agents()
    platform_count = len(platform) if platform else 0
    print(f"已生成 docs/registry.md（{len(text)} 字）："
          f"新实装 agent {platform_count}、老蓝图角色 {len(LEGACY_ROLE)}、"
          f"本体角色 {len(_ontology_meta()['roles'])}、"
          f"指令 {len(feishu_commands.COMMAND_SPECS)}、"
          f"表 {len(_erd_tables())}、工具 {len(list((ROOT / 'tools').glob('*.py')))}、"
          f"模块 {len(list(ROOT.glob('*.py')))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
