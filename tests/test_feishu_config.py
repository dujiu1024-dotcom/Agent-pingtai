#!/usr/bin/env python3
"""飞书域名/时区可配置：默认值必须与历史硬编码完全一致。"""

import datetime as dt
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feishu_bot  # noqa: E402


class FeishuConfigTest(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("FEISHU_BASE_URL", "FEISHU_TZ_OFFSET")}

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_defaults_match_legacy(self):
        os.environ.pop("FEISHU_BASE_URL", None)
        os.environ.pop("FEISHU_TZ_OFFSET", None)
        self.assertEqual(feishu_bot.base_url(),
                         "https://open.feishu.cn/open-apis")
        self.assertEqual(feishu_bot.local_tz().utcoffset(None),
                         dt.timedelta(hours=8))
        legacy = str(int(dt.datetime(2026, 11, 20, 23, 59, 59,
                                     tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()))
        self.assertEqual(feishu_bot.to_timestamp("2026-11-20"), legacy)

    def test_override(self):
        os.environ["FEISHU_BASE_URL"] = "https://open.larksuite.com/open-apis/"
        os.environ["FEISHU_TZ_OFFSET"] = "0"
        self.assertEqual(feishu_bot.base_url(),
                         "https://open.larksuite.com/open-apis")
        self.assertEqual(feishu_bot.local_tz().utcoffset(None), dt.timedelta(0))

    def test_bad_offset_falls_back(self):
        os.environ["FEISHU_TZ_OFFSET"] = "abc"
        self.assertEqual(feishu_bot.local_tz().utcoffset(None),
                         dt.timedelta(hours=8))


if __name__ == "__main__":
    unittest.main()
