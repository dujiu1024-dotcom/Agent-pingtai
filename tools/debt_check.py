#!/usr/bin/env python3
"""技术债验收命令库（debt sync 生成的 done_when 都调用本文件）

用法：
    python3 tools/debt_check.py 06:1.1      # 跑单条，退出码 0=通过，1=未通过
    python3 tools/debt_check.py --list      # 列出全部验收项
    python3 tools/debt_check.py --all       # 跑全部（可能较慢）

约定：每条验收只回答一个问题——"这个债真的还了吗"。宁可判失败，
不写永远返回 0 的空检查。
"""

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "legacy" / "v1-archive"))

import db as dbm  # noqa: E402


def _read(rel):
    path = ROOT / rel
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _has(rel, needle):
    return needle in _read(rel)


def _not_has(rel, needle):
    return needle not in _read(rel)


def _run(cmd, timeout=300):
    return subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True,
                          text=True, timeout=timeout)


def _test(module):
    path = ROOT / "tests" / f"{module}.py"
    if not path.exists():
        return False, f"缺少 {path.relative_to(ROOT)}"
    proc = _run(f"python3 -m unittest tests.{module} -q", timeout=600)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()[-400:]


def _export_members():
    out = Path(tempfile.mkdtemp()) / "debt-export.tar.gz"
    proc = _run(f'python3 agency.py export "{out}"', timeout=600)
    if proc.returncode != 0 or not out.exists():
        return set(), f"导出失败：{(proc.stdout + proc.stderr).strip()[-300:]}"
    with tarfile.open(out, "r:gz") as tar:
        names = {m.name for m in tar.getmembers()}
    out.unlink(missing_ok=True)
    return names, ""


def _tmp_db_copy():
    tmpdir = Path(tempfile.mkdtemp())
    target = tmpdir / "agency.db"
    if dbm.DB_PATH.exists():
        shutil.copy2(dbm.DB_PATH, target)
    return tmpdir, target


# ---------- 06 第一轮 ----------

def check_06_1_1():
    src = _read("agency.py")
    if "BEGIN IMMEDIATE" not in src:
        return False, "next_id 仍未加锁（缺 BEGIN IMMEDIATE/序列表）"
    return True, "next_id 已在写事务内取号"


def check_06_1_2():
    return _has("memory.py", "ON CONFLICT"), "wm_session 写入需 UPSERT/BEGIN IMMEDIATE 才通过"


def check_06_1_3():
    return _has("agency.py", "value = value +"), "config_kv 计数需 SQL 原子自增"


def check_06_1_4():
    ok = all(s in dbm.TASK_STATUS for s in ("doing", "review", "done", "canceled"))
    ok = ok and '"review"' in _read("agency.py") and '"doing"' in _read("agency.py")
    return ok, "状态机需包含 doing/review/done/canceled 且调度层真实写入"


def check_06_1_5():
    return (ROOT / "tools" / "sync_plan.py").exists(), "缺四处同步脚本 tools/sync_plan.py"


def check_06_1_6():
    return _has("console.py", "ag.cmd_task_add") or _has("console.py", "ag.create_task"), \
        "控制台与 CLI 仍是两套写入"


def check_06_1_7():
    src = _read("agency.py")
    match = re.search(r"def db\(\):\n(.*?)\n\n", src, re.S)
    body = match.group(1) if match else ""
    return "migrate" not in body, "每次 db() 仍在跑迁移写库"


def check_06_1_8():
    src = _read("agency.py")
    return "sync 失败" in src and "sys.exit(1)" in src, "sync 全失败仍需非零退出"


def check_06_1_9():
    return _has("agency.py", "local_date") and _has("agency.py", "utc_date"), \
        "token 日账与投递花费需统一日期口径"


def check_06_1_10():
    src = _read("agency.py")
    return 'return "日期非法"' in src, "task_state 未兜底坏日期"


def check_06_2_1():
    return _read("agency.py").count("INSERT INTO dead_letter") >= 3, \
        "cmd_run 失败仍未写死信"


def check_06_2_2():
    return "deadletter" in _read("agency.py").lower(), "缺 deadletter list/retry/resolve 入口"


def check_06_2_3():
    return "MAX_ATTEMPTS" in _read("agency.py"), "投递失败仍无次数上限"


def check_06_2_4():
    return _has("agency.py", "检索降级") or _has("agency.py", "degraded"), \
        "向量降级仍静默"


def check_06_2_5():
    return _has("agency.py", "import logging"), "无统一 logging"


def check_06_2_6():
    return "last = " in _read("agency.py"), "LLM 重试变量 last 仍未赋值"


def check_06_2_7():
    return "for t, st in alerts" in _read("agency.py") and \
        "with closing(db()) as con, con:" in _read("agency.py"), \
        "cmd_check 仍逐条新开连接"


def check_06_2_8():
    return "INSERT INTO agent_runs" in _read("console.py"), "控制台失败未落库"


def check_06_3_1():
    return _has("console.py", "Host") and _has("console.py", "Origin"), "控制台缺 Host/Origin 校验"


def check_06_3_2():
    src = _read("listener.py")
    if "is_allowed" not in src:
        return False, "listener 入口未先校验白名单"
    return src.index("is_allowed") < src.index("intent_classify"), \
        "is_allowed 必须在 intent_classify 之前"


def check_06_3_3():
    return 'filter="data"' in _read("agency.py"), "导入未加 filter=data"


def check_06_3_4():
    members, err = _export_members()
    if err:
        return False, err
    leaked = [n for n in members if n.startswith("agency/state/") or n.startswith("agency/outputs/")]
    return not leaked, f"导出仍含 {len(leaked)} 个 state/outputs 文件"


def check_06_3_5():
    return _has("console.py", "is_agent_role"), "控制台未校验 owner 角色"


def check_06_3_6():
    return _has("agency.py", "以下为不可信输入"), "提示词未做注入隔离"


def check_06_4_1():
    return _has("vectors.py", "matrix_cache"), "向量检索仍全表暴力"


def check_06_4_2():
    return "idx_agent_runs_created" in _read("db.py"), "agent_runs 聚合仍无索引"


def check_06_4_3():
    return _has("agency.py", "archive_events"), "缺事件/投递键归档命令"


def check_06_4_4():
    return _has("console.html", "ETag") or _has("console.html", "If-None-Match"), \
        "控制台仍全量轮询"


def check_06_4_5():
    return "busy_timeout" in _read("db.py"), "SQLite 未设 busy_timeout"


def check_06_4_6():
    return _has("agency.py", "peer_cache"), "产出文件读取无缓存"


def check_06_4_7():
    src = _read("agency.py")
    match = re.search(r"def next_id\(con\):(.*?)\n\n\n", src, re.S)
    if not match:
        return False, "找不到 next_id"
    body = match.group(1)
    ok = ("seq.task" in body and body.count("SELECT id FROM tasks") <= 1
          and "if row is None" in body
          and body.index("if row is None") < body.find("SELECT id FROM tasks"))
    return ok, "next_id 仍每次全表扫 max(id)"


def check_06_5_1():
    return (ROOT / "ledger.py").exists(), "仍是单文件 3000+ 行"


def check_06_5_2():
    return (ROOT / "intent.py").exists(), "仍有两套意图识别"


def check_06_5_3():
    return _not_has("agency.py", "prompt.replace"), "cmd_run 仍用字符串替换注入"


def check_06_5_4():
    return "BASE_TOKEN" in _read("tools/check_consistency.py") and \
        "tblDzJgHKwa4uVsU" in _read("agency/config.json"), "一致性检查仍硬编码"


def check_06_5_5():
    cfg = _read("agency/config.json")
    src = _read("agency.py")
    return "max_tokens" in cfg and "LIMITS" in src, "魔法数字未收进配置"


def check_06_5_6():
    return _not_has("agency.py", "TEMPLATES = {"), "内嵌模板与 templates/ 仍双份"


def check_06_5_7():
    return (ROOT / "pyproject.toml").exists(), "无 lint/类型检查/CI"


def check_06_5_8():
    return _not_has("agency.py", "最多 5 条"), "注释仍与实现不一致"


def check_06_5_7b():
    """全量 lint（含存量）：T-0113 关闭前错误数必须为 0。"""
    cfg = ROOT / "tools" / "ruff_legacy.toml"
    if not cfg.exists():
        return False, "缺 tools/ruff_legacy.toml"
    exe = shutil.which("ruff")
    runner = f'"{exe}"' if exe else f'"{sys.executable}" -m ruff'
    proc = _run(f'{runner} check --no-cache --config "{cfg}"'
                ' --output-format concise .', timeout=180)
    if proc.returncode == 0:
        return True, ""
    out = (proc.stdout + proc.stderr).strip().splitlines()
    tail = out[-1] if out else "ruff 执行失败"
    if "No module named ruff" in tail:
        return False, "未安装 ruff（brew install ruff 或 pip install ruff）"
    return False, "存量 lint 未清零：" + tail


def check_06_6_1():
    members, err = _export_members()
    if err:
        return False, err
    need = ["ratelimit.py", "llm_local.py", "policy.py", "gui.py", "browser.py",
            "probe.py", "feishu_bot.py", "feishu_commands.py", "listener.py",
            "console.py", "console.html", "memory.py", "vectors.py"]
    missing = [n for n in need if n not in members]
    need_dirs = [d for d in ("templates/", "tools/", "tests/", "agency/policies/",
                             "agency/apps/") if not any(n.startswith(d) for n in members)]
    return not missing and not need_dirs, f"导出包缺：{missing + need_dirs}"


def check_06_6_2():
    return check_06_3_4()


def check_06_6_3():
    return (ROOT / "requirements-listener.txt").exists(), "缺 requirements-listener.txt"


def check_06_6_4():
    return _has("gui.py", "platform.system"), "操作层无平台探测/降级"


def check_06_6_5():
    return not (ROOT / ".venv-listener").exists(), "仍有两套运行环境"


def check_06_6_6():
    return "网络盘" in _read("db.py") or "网络盘" in _read("agency.py"), "缺 WAL 网络盘警告"


def check_06_7_1():
    return _test("test_export")


def check_06_7_2():
    return _test("test_console")


def check_06_7_3():
    return _test("test_listener")


def check_06_7_4():
    return _test("test_vectors")


def check_06_7_5():
    return _test("test_memory")


def check_06_7_6():
    return _test("test_contract")


def check_06_7_7():
    return _test("test_concurrency")


def check_06_7_8():
    if not (ROOT / "tests" / "test_blackboard.py").exists():
        return False, "缺黑板回归测试"
    return "dedup" in _read("tests/test_sync.py"), "到期事件 dedup 无回归用例"


def check_06_8_1():
    return _not_has("agency.py", '("none", "low")'), "仍默认发送 reasoning_effort=none"


def check_06_8_2():
    match = re.search(r"max_prompt_tokens[\"']?\s*[:=]\s*(\d+)", _read("ratelimit.py"))
    return bool(match) and int(match.group(1)) >= 32000, "未知模型默认上限仍过小"


def check_06_8_3():
    return _not_has("agency.py", "gpt-4o-mini"), "仍回退到 gpt-4o-mini"


def check_06_8_4():
    return "FEISHU_BASE_URL" in _read("feishu_bot.py"), "飞书域名/时区仍写死"


def check_06_8_5():
    return check_06_6_3()


def check_06_8_6():
    return (ROOT / "pyproject.toml").exists() or (ROOT / ".python-version").exists(), \
        "Python 版本无声明"


def check_06_8_7():
    src = _read("agency.py")
    return "HTTPS_PROXY" in src or "proxy" in src, "无代理/重试策略配置"


# ---------- 08 第二轮 ----------

def check_08_N01():
    return (ROOT / "coverage.json").exists(), "覆盖率仍未量化（缺 coverage.json）"


def check_08_N02():
    return (ROOT / "tests" / "test_cli.py").exists(), "缺 CLI 入口冒烟测试"


def check_08_N03():
    ok = (ROOT / "tests" / "test_vectors.py").exists()
    return ok and all(_has(f"tests/test_{m}.py", "def test_") for m in ("vectors", "memory")), \
        "降级/错误路径仍零覆盖"


def check_08_N04():
    src = _read("agency.py")
    match = re.search(r"def _dep_list\(value\):.*?\n\n\n", src, re.S)
    if not match:
        return False, "找不到 _dep_list"
    start = src.find("DEP_PLACEHOLDERS")
    if start == -1 or start > match.start():
        return False, "找不到 DEP_PLACEHOLDERS 常量"
    ns = {}
    exec(src[start:match.end()], ns)  # noqa: S102 - 只执行本仓库函数源码
    dep = ns["_dep_list"]
    return all(dep(v) == [] for v in ("—", "-", "无", "空", "none", "null")), \
        "_dep_list 仍把占位符当依赖"


def check_08_N05():
    tmpdir, target = _tmp_db_copy()
    env = {"AGENCY_DB": str(target), "PATH": "/usr/local/bin:/usr/bin:/bin"}
    cmd = ('python3 agency.py task add --title 幂等测试 --owner pm '
           '--start 2026-11-12 --due 2026-11-13 --done-when cmd:true --project OPC-002')
    r1 = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, env={**env})
    r2 = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, env={**env})
    shutil.rmtree(tmpdir, ignore_errors=True)
    if r1.returncode != 0 or r2.returncode != 0:
        return False, "无法建任务，检查失败"
    id1 = re.search(r"(?:已建|已存在) (T-\d+)", r1.stdout)
    id2 = re.search(r"(?:已建|已存在) (T-\d+)", r2.stdout)
    return bool(id1 and id2 and id1.group(1) == id2.group(1)), "重复任务仍生成新 ID"


def check_08_N06():
    tmpdir, target = _tmp_db_copy()
    env = {"AGENCY_DB": str(target), "PATH": "/usr/local/bin:/usr/bin:/bin"}
    proc = subprocess.run(
        "python3 agency.py task update T-0028 --start 2026-13-99",
        shell=True, cwd=ROOT, capture_output=True, text=True, env={**env})
    shutil.rmtree(tmpdir, ignore_errors=True)
    out = proc.stdout + proc.stderr
    return proc.returncode != 0 and "日期格式错误" in out and "Traceback" not in out, \
        "非法日期仍抛原始 traceback"


def check_08_N07():
    tmpdir, target = _tmp_db_copy()
    env = {"AGENCY_DB": str(target), "PATH": "/usr/local/bin:/usr/bin:/bin"}
    title = "长" * 200000
    proc = subprocess.run(
        ["python3", "agency.py", "task", "add", "--title", title,
         "--owner", "pm", "--start", "2026-11-12", "--due", "2026-11-13",
         "--done-when", "cmd:true", "--project", "OPC-002"],
        cwd=ROOT, capture_output=True, text=True, env={**env})
    shutil.rmtree(tmpdir, ignore_errors=True)
    out = proc.stdout + proc.stderr
    return (proc.returncode != 0 and "标题过长" in out and "上限 200" in out
            and "Traceback" not in out), "超长标题仍被接受或报错不明确"


def check_08_N08():
    return _has("agency.py", "OUTPUT_DIR.mkdir") and _has("agency.py", "空目录"), \
        "doctor/导出未处理空 outputs 目录"


def check_08_N09():
    return _has("agency.py", "would_send"), "dry-run 仍把未发送计为成功"


def check_08_N10():
    src = _read("agency.py")
    return "--force" in src and "非空" in src and "backup" in src, \
        "导入非空目录仍无 --force/备份"


def check_08_N11():
    return "staging" in _read("agency.py").lower() or "临时目录" in _read("agency.py"), \
        "导入仍非原子"


def check_08_N12():
    return _has("listener.py", "dedup_key"), "飞书去重仍只在内存"


def check_08_N13():
    tmpdir, target = _tmp_db_copy()
    env = {"AGENCY_DB": str(target), "PATH": "/usr/local/bin:/usr/bin:/bin"}
    cmd = ("python3 agency.py task add --title 负工时测试 --owner pm "
           "--start 2026-11-12 --due 2026-11-13 --hours -5 "
           "--done-when cmd:true --project OPC-002")
    proc = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True,
                          text=True, env={**env})
    shutil.rmtree(tmpdir, ignore_errors=True)
    out = proc.stdout + proc.stderr
    return (proc.returncode != 0 and "工时必须大于 0" in out
            and "Traceback" not in out), "负工时仍被接受"


def check_08_N14():
    return _has("console.py", "project_id"), "产出接口无项目作用域"


def check_08_N15():
    src = _read("agency.py")
    return ("manifest" in src and "包不完整" in src
            and "EXPORT_REQUIRED" in src), "导入仍不校验包完整性"


def check_08_N16():
    return _has("agency.py", "checkpoint 失败") or _has("agency.py", "wal_checkpoint"), \
        "WAL checkpoint 失败仍被吞"


def check_08_N17():
    con = sqlite3.connect(f"file:{dbm.DB_PATH}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT project_id FROM tasks WHERE id='T-0021'").fetchone()
    finally:
        con.close()
    return bool(row and row[0]), "T-0021 仍无项目归属"


def check_08_N18():
    return _not_has("console.html", "slice(5,16)"), "前端仍混用 UTC/本地时间"


def check_08_N19():
    src = _read("agency.py")
    bad = re.findall(
        r"UPDATE events SET delivered_at = \?[\s\S]{0,240}?timespec=\"seconds\"", src)
    return (not bad and 'timespec="microseconds"' in src), "delivered_at 仍截断到秒"


def check_08_N20():
    return (ROOT / "requirements-listener.txt").exists(), "README 与依赖仍矛盾"


def check_08_COV():
    path = ROOT / "coverage.json"
    if not path.exists():
        return False, "缺 coverage.json，先跑 python3 tools/coverage_report.py"
    data = json.loads(path.read_text(encoding="utf-8"))
    total = data.get("total", {}).get("percent", 0)
    files = data.get("files", {})
    need = {"agency.py": 60, "db.py": 60, "console.py": 60}
    bad = ["%s %.1f%%<60%%" % (n, files.get(n, {}).get("percent", 0))
           for n in need if files.get(n, {}).get("percent", 0) < 60]
    ok = total >= 70 and not bad
    msg = "总 %.1f%%（需≥70%%），agency/db/console=%s" % (
        total, "、".join("%.1f%%" % files.get(n, {}).get("percent", 0)
                        for n in need))
    if bad:
        msg += "；未达标：" + "、".join(bad)
    return ok, msg


CHECKS = {}

for _name, _fn in list(globals().items()):
    if _name.startswith("check_06_"):
        CHECKS["06:" + _name[len("check_06_"):].replace("_", ".")] = _fn
    elif _name.startswith("check_08_"):
        CHECKS["08:" + _name[len("check_08_"):]] = _fn


def main():
    ap = argparse.ArgumentParser(description="技术债验收命令")
    ap.add_argument("key", nargs="?", help="如 06:1.1 / 08:N04")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.list:
        for key in sorted(CHECKS):
            print(key)
        return 0
    if args.all:
        failed = []
        for key in sorted(CHECKS):
            try:
                ok, msg = CHECKS[key]()
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, f"检查异常：{exc}"
            print(f"[{'OK ' if ok else 'FAIL'}] {key} {msg}")
            if not ok:
                failed.append(key)
        print(f"合计 {len(CHECKS)} 条，通过 {len(CHECKS) - len(failed)}，未通过 {len(failed)}")
        return 1 if failed else 0

    if not args.key:
        ap.error("需要 key，或使用 --list / --all")
    if args.key not in CHECKS:
        print(json.dumps({"ok": False, "error": f"未登记验收项：{args.key}"},
                         ensure_ascii=False) if args.json else f"未登记验收项：{args.key}")
        return 2
    try:
        ok, msg = CHECKS[args.key]()
    except Exception as exc:  # noqa: BLE001
        ok, msg = False, f"检查异常：{exc}"
    if args.json:
        print(json.dumps({"key": args.key, "ok": ok, "message": msg}, ensure_ascii=False))
    else:
        print(f"[{'OK ' if ok else 'FAIL'}] {args.key} {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
