#!/usr/bin/env python3
"""控制台意图路由（console.diagnose_intent）

覆盖实测中暴露的错误派单：
- "报价单改一版" 曾派给 content + finance（pm 词表缺业务词）；
- "和客户开会" 曾被 scout 的"客户"误带；
- "方案里的验收标准" 曾被 launch bundle 判成上线。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import console  # noqa: E402


def top(text):
    data = console.diagnose_intent({"instruction": text})["data"]
    roles = data["suggested_roles"]
    return (roles[0]["role"] if roles else "", data["intent"])


class RoutingTest(unittest.TestCase):
    def test_quote_edit_goes_to_pm(self):
        self.assertEqual(top("报价单改一版")[0], "pm")

    def test_diagnosis_pack_goes_to_pm(self):
        self.assertEqual(top("帮我把诊断包里的报价单按2小时出方案改一版")[0], "pm")

    def test_acceptance_criteria_goes_to_pm(self):
        self.assertEqual(top("这个方案里的验收标准写清楚")[0], "pm")

    def test_client_meeting_goes_to_secretary(self):
        self.assertEqual(top("明天下午和客户开会，帮我排个日程并出纪要")[0], "secretary")

    def test_copywriting_goes_to_content(self):
        self.assertEqual(top("给制造业老板写一条朋友圈文案")[0], "content")

    def test_lead_search_goes_to_scout(self):
        self.assertEqual(top("找几个苏州制造业的客户线索")[0], "scout")

    def test_launch_goes_to_producer(self):
        self.assertEqual(top("这个功能明天要上线，帮我排一下")[0], "producer")

    def test_invoice_query_is_view_not_agent(self):
        self.assertEqual(top("查一下这个月的回款和逾期发票")[1], "view")

    def test_product_copy_goes_to_content(self):
        self.assertEqual(top("写个产品文案")[0], "content")

    def test_lead_search_short_goes_to_scout(self):
        self.assertEqual(top("找客户线索")[0], "scout")

    def test_meeting_minutes_go_to_secretary(self):
        self.assertEqual(top("整理会议纪要")[0], "secretary")

    def test_launch_plan_goes_to_producer(self):
        self.assertEqual(top("帮我排个上线计划")[0], "producer")


if __name__ == "__main__":
    unittest.main()
