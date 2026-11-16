#!/usr/bin/env python3
"""事实台账 facts add/list

走真实 CLI，只把数据库指向临时目录。
覆盖：分级入库、来源强制、内容去重、按项目检索。
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency  # noqa: E402
import db as dbm  # noqa: E402

PROJECT = "TEST-FACTS"


class FactsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db_path = dbm.DB_PATH
        self.orig_env = {k: os.environ.get(k) for k in
                         ("AGENCY_ENV", "AGENCY_ALLOW_CUSTOM_DB")}
        os.environ["AGENCY_ENV"] = "dev"
        os.environ["AGENCY_ALLOW_CUSTOM_DB"] = "1"
        dbm.DB_PATH = Path(self.tmp.name) / "agency.db"

    def tearDown(self):
        dbm.DB_PATH = self.orig_db_path
        for key, value in self.orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def cli(self, *argv):
        """执行 agency.py 命令，返回 (退出码, 输出)。"""
        old_argv = sys.argv
        sys.argv = ["agency.py", *argv]
        buf, err = io.StringIO(), io.StringIO()
        code = None
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                try:
                    agency.main()
                except SystemExit as e:
                    code = e.code
                    if isinstance(e.code, str):
                        buf.write(e.code)
        finally:
            sys.argv = old_argv
        return code, buf.getvalue() + err.getvalue()

    def test_add_and_list(self):
        self.cli("facts", "add", "客户说质检记录还是纸质", "--project", PROJECT,
                 "--level", "S1", "--source", "创始人 11-12 口述", "--date", "2026-11-12")
        _, text = self.cli("facts", "list", "--project", PROJECT)
        self.assertIn("[S1]", text)
        self.assertIn("客户说质检记录还是纸质", text)
        self.assertIn("2026-11-12", text)

    def test_duplicate_not_written(self):
        args = ("facts", "add", "获客渠道：B 站、抖音", "--project", PROJECT,
                "--source", "创始人 11-11")
        self.cli(*args)
        _, text = self.cli(*args)
        self.assertIn("已存在", text)

    def test_source_required(self):
        code, _ = self.cli("facts", "add", "无来源事实", "--project", PROJECT)
        self.assertNotEqual(code, 0)

    def test_read_by_role_prompt(self):
        self.cli("facts", "add", "首单场景：为他人搭建 FDE OPC Agent 平台",
                 "--project", PROJECT, "--source", "调研记录 11-11")
        facts = agency.recent_facts(PROJECT)
        self.assertEqual(len(facts), 1)
        self.assertIn("[S1]", facts[0])
        self.assertIn("首单场景", facts[0])

    def test_prompt_without_task_keeps_project_facts(self):
        """无任务 run：项目 S1 事实必须进提示词，不能被空 project 丢掉。"""
        self.cli("facts", "add", "获客渠道：B 站、抖音", "--project", PROJECT,
                 "--source", "调研记录 11-11")
        self.cli("facts", "add", "诊断/搭建定价 9,800 元，创始人接受",
                 "--project", PROJECT, "--source", "调研记录 11-11")
        prompt = agency.build_prompt("chief-of-staff", "ROLE", None, [], "")
        self.assertIn("B 站、抖音", prompt)
        self.assertIn("9,800", prompt)

    def test_prompt_open_tasks_include_done_when(self):
        """未关闭任务要带验收标准，避免模型反问库里已有答案的问题。"""
        self.cli("task", "add", "--title", "写诊断报告", "--owner", "pm",
                 "--project", PROJECT, "--start", "2026-11-13",
                 "--due", "2026-11-16", "--done-when", "陌生客户30分钟出报告")
        prompt = agency.build_prompt("chief-of-staff", "ROLE", None,
                                     agency.read_tasks(), "")
        self.assertIn("验收：陌生客户30分钟出报告", prompt)
        self.assertIn("起：2026-11-13 止：2026-11-16", prompt)


if __name__ == "__main__":
    unittest.main()
