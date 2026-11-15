#!/usr/bin/env python3
"""飞书指令路由：解析、白名单、执行（分项 21.x）"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db as dbm  # noqa: E402
import feishu_commands as fc  # noqa: E402

OWNER = "ou_owner"
OTHER = "ou_stranger"
_ORIG_DB = dbm.DB_PATH


def setUpModule():
    """白名单读 users 表：测试用临时库，表空时回退 env。"""
    tmp = tempfile.mkdtemp()
    dbm.DB_PATH = Path(tmp) / "test.db"
    con = dbm.connect()
    dbm.migrate(con)
    con.close()


def tearDownModule():
    dbm.DB_PATH = _ORIG_DB


class UsersAllowTest(unittest.TestCase):
    def test_users_table_is_authoritative(self):
        con = dbm.connect()
        try:
            with con:
                con.execute(
                    "INSERT INTO users(open_id, name, role, projects, console_token,"
                    " created_at) VALUES ('ou_db', '库内用户', 'assistant', '', 't', ?)",
                    (dbm.now(),))
        finally:
            con.close()
        os.environ["FEISHU_USER_OPEN_ID"] = OWNER
        try:
            self.assertTrue(fc.is_allowed("ou_db"))
            self.assertFalse(fc.is_allowed(OWNER))
        finally:
            con = dbm.connect()
            con.execute("DELETE FROM users")
            con.commit()
            con.close()


class ParseTest(unittest.TestCase):
    def test_plain_text_is_not_command(self):
        self.assertIsNone(fc.parse("帮我看看任务"))
        self.assertIsNone(fc.parse(""))

    def test_help_and_brief(self):
        self.assertEqual(fc.parse("/help")["cmd"], "/help")
        self.assertEqual(fc.parse("/帮助")["cmd"], "/help")
        self.assertEqual(fc.parse("/brief")["cmd"], "/brief")

    def test_sync_force(self):
        self.assertEqual(fc.parse("/sync"), {"cmd": "/sync", "force": False})
        self.assertTrue(fc.parse("/sync --force")["force"])

    def test_run_with_task_and_note(self):
        cmd = fc.parse("/run planner T-0019 只做 19.3")
        self.assertEqual(cmd, {"cmd": "/run", "role": "planner",
                               "task": "T-0019", "note": "只做 19.3"})
        cmd = fc.parse("/run planner 先看现状")
        self.assertEqual(cmd["task"], "")
        self.assertEqual(cmd["note"], "先看现状")

    def test_run_without_role_and_unknown(self):
        self.assertIn("用法", fc.parse("/run")["error"])
        self.assertIn("未知指令", fc.parse("/deploy")["error"])

    def test_task_add_parsing(self):
        cmd = fc.parse("/task add 写诊断报告 --owner planner --days 2")
        self.assertEqual(cmd, {"cmd": "/task", "sub": "add",
                               "title": "写诊断报告", "owner": "planner",
                               "days": 2, "project": "",
                               "check": "", "done_when": "",
                               "start": "", "due": ""})

    def test_task_add_with_project(self):
        cmd = fc.parse('/task add "改首页文案" --owner content --project OPC-002')
        self.assertEqual(cmd["sub"], "add")
        self.assertEqual(cmd["title"], "改首页文案")
        self.assertEqual(cmd["owner"], "content")
        self.assertEqual(cmd["project"], "OPC-002")
        self.assertEqual(cmd["days"], 3)  # 默认值

    def test_task_add_strips_flags_from_title(self):
        cmd = fc.parse('/task add "搞个诊断" --owner pm --project OPC-002'
                       ' --start 2026-11-13 --due 2026-11-16 --unknown x')
        self.assertEqual(cmd["title"], "搞个诊断")

    def test_task_add_missing_owner(self):
        self.assertIn("--owner", fc.parse("/task add 标题")["error"])

    def test_task_add_missing_title(self):
        self.assertIn("标题不能为空", fc.parse("/task add --owner planner")["error"])

    def test_plan_parsing(self):
        cmd = fc.parse("/plan 完成 MVP --project OPC-002 --days 7")
        self.assertEqual(cmd, {"cmd": "/plan", "goal": "完成 MVP",
                               "project": "OPC-002", "days": 7})

    def test_plan_defaults(self):
        cmd = fc.parse("/plan 拆一下")
        self.assertEqual(cmd["goal"], "拆一下")
        self.assertEqual(cmd["days"], 5)
        self.assertEqual(cmd["project"], "")

    def test_plan_missing_goal(self):
        self.assertIn("用法", fc.parse("/plan")["error"])

    def test_dispatch_parsing(self):
        cmd = fc.parse("/dispatch --limit 3 --project OPC-002")
        self.assertEqual(cmd, {"cmd": "/dispatch", "limit": 3,
                               "project": "OPC-002"})

    def test_dispatch_defaults(self):
        cmd = fc.parse("/dispatch")
        self.assertEqual(cmd, {"cmd": "/dispatch", "limit": 5, "project": ""})


class AllowTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in
                      ("FEISHU_ALLOWED_OPEN_IDS", "FEISHU_USER_OPEN_ID")}

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_default_owner_allowed(self):
        os.environ.pop("FEISHU_ALLOWED_OPEN_IDS", None)
        os.environ["FEISHU_USER_OPEN_ID"] = OWNER
        self.assertTrue(fc.is_allowed(OWNER))
        self.assertFalse(fc.is_allowed(OTHER))

    def test_extra_ids(self):
        os.environ["FEISHU_USER_OPEN_ID"] = OWNER
        os.environ["FEISHU_ALLOWED_OPEN_IDS"] = "ou_a, ou_b"
        self.assertTrue(fc.is_allowed("ou_b"))
        self.assertTrue(fc.is_allowed(OWNER))
        self.assertFalse(fc.is_allowed(OTHER))

    def test_no_allowlist_denies_and_shows_id(self):
        os.environ.pop("FEISHU_ALLOWED_OPEN_IDS", None)
        os.environ.pop("FEISHU_USER_OPEN_ID", None)
        reply = fc.handle("/brief", OTHER)
        self.assertIn("未配置飞书指令白名单", reply)
        self.assertIn(OTHER, reply)

    def test_stranger_denied(self):
        os.environ["FEISHU_USER_OPEN_ID"] = OWNER
        self.assertIn("无权执行指令", fc.handle("/brief", OTHER))


class HandleTest(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get("FEISHU_USER_OPEN_ID")
        os.environ["FEISHU_USER_OPEN_ID"] = OWNER
        self.calls = []

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("FEISHU_USER_OPEN_ID", None)
        else:
            os.environ["FEISHU_USER_OPEN_ID"] = self.saved

    def runner(self, cmd):
        self.calls.append(cmd)
        return 0, "执行完成", ""

    def test_plain_text_returns_none(self):
        self.assertIsNone(fc.handle("你好", OWNER, self.runner))

    def test_help_does_not_spawn(self):
        reply = fc.handle("/help", OWNER, self.runner)
        self.assertIn("/run", reply)
        self.assertEqual(self.calls, [])

    def test_brief_and_sync(self):
        fc.handle("/brief", OWNER, self.runner)
        fc.handle("/sync --force", OWNER, self.runner)
        self.assertTrue(self.calls[0][-1].endswith("brief"))
        self.assertIn("sync", self.calls[1])
        self.assertIn("--force", self.calls[1])

    def test_run_builds_arguments(self):
        reply = fc.handle("/run planner T-0019 只做 19.3", OWNER, self.runner)
        self.assertIn("✅", reply)
        cmd = self.calls[0]
        self.assertEqual(cmd[-6:], ["run", "planner", "--task", "T-0019",
                                    "--note", "只做 19.3"])

    def test_unknown_role_rejected(self):
        reply = fc.handle("/run nobody T-0001", OWNER, self.runner)
        self.assertIn("角色不存在", reply)
        self.assertEqual(self.calls, [])

    def test_failure_reports_exit_code(self):
        reply = fc.handle("/sync", OWNER, lambda cmd: (2, "", "数据库锁住了"))
        self.assertIn("退出码 2", reply)
        self.assertIn("数据库锁住了", reply)

    def test_long_output_truncated(self):
        reply = fc.handle("/brief", OWNER, lambda cmd: (0, "x" * 5000, ""))
        self.assertLessEqual(len(reply), fc.MAX_REPLY + 100)

    def test_task_add_builds_arguments(self):
        reply = fc.handle("/task add 写诊断报告 --owner planner --days 2",
                          OWNER, self.runner)
        self.assertIn("✅", reply)
        cmd = self.calls[0]
        self.assertIn("task", cmd)
        self.assertIn("add", cmd)
        self.assertIn("--owner", cmd)
        self.assertIn("planner", cmd)
        self.assertIn("--title", cmd)
        self.assertIn("--start", cmd)
        self.assertIn("--due", cmd)
        # 没有验收标准时用占位，不拿标题兜底
        self.assertEqual(cmd[cmd.index("--done-when") + 1], "待补：验收标准")
        self.assertEqual(cmd.count("写诊断报告"), 1)
        self.assertIn("--check", reply)

    def test_task_add_with_check(self):
        reply = fc.handle('/task add 写诊断报告 --owner planner --check "pytest -q"',
                          OWNER, self.runner)
        self.assertIn("✅", reply)
        cmd = self.calls[0]
        self.assertEqual(cmd[cmd.index("--done-when") + 1], "cmd: pytest -q")
        self.assertNotIn("待补", reply)

    def test_task_add_unknown_role(self):
        reply = fc.handle("/task add 标题 --owner ghost", OWNER, self.runner)
        self.assertIn("角色不存在", reply)
        self.assertEqual(self.calls, [])

    def test_plan_builds_arguments(self):
        fc.handle("/plan 完成 MVP --project OPC-002", OWNER, self.runner)
        cmd = self.calls[0]
        self.assertIn("plan", cmd)
        self.assertIn("完成 MVP", cmd)
        self.assertIn("--apply", cmd)
        self.assertIn("--project", cmd)
        self.assertIn("OPC-002", cmd)

    def test_dispatch_builds_arguments(self):
        fc.handle("/dispatch --limit 3", OWNER, self.runner)
        cmd = self.calls[0]
        self.assertIn("dispatch", cmd)
        self.assertIn("--limit", cmd)
        self.assertIn("3", cmd)


class CapabilityTest(unittest.TestCase):
    def test_capabilities_cover_all_agent_cards(self):
        import feishu_commands as fc
        text = fc.capabilities_text()
        for card in fc.agent_cards():
            self.assertIn(fc.display_name(card), text)
        self.assertIn("【我能做什么】", text)

    def test_command_specs_are_parseable(self):
        import feishu_commands as fc
        self.assertIn("/dispatch", fc.HELP_VERBOSE)
        for usage, _ in fc.COMMAND_SPECS:
            head = usage.split()[0]
            self.assertIsNotNone(fc.parse(head), head)


if __name__ == "__main__":
    unittest.main()
