import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.web.app import create_platform_web_router


class FirstScreenPerformanceBudgetTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(create_platform_web_router())
        self.client = TestClient(app)

    def _get_size(self, path: str) -> int:
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        return len(response.content)

    def test_workbench_critical_path_stays_within_transfer_budgets(self):
        page = self.client.get("/app/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(
            'rel="preload" href="/app/assets/style.css" as="style" fetchpriority="high"',
            page.text,
        )
        self.assertIn(
            'rel="preload" href="/app/assets/app.js" as="script" fetchpriority="high"',
            page.text,
        )
        self.assertIn('<script src="/app/assets/app.js" defer></script>', page.text)

        html_bytes = len(page.content)
        css_bytes = self._get_size("/app/assets/style.css")
        app_js_bytes = self._get_size("/app/assets/app.js")

        self.assertLessEqual(html_bytes, 4 * 1024)
        self.assertLessEqual(css_bytes, 12 * 1024)
        self.assertLessEqual(app_js_bytes, 48 * 1024)
        self.assertLessEqual(html_bytes + css_bytes + app_js_bytes, 64 * 1024)

    def test_legacy_fallback_keeps_document_and_module_transfer_budgets(self):
        page = self.client.get("/app/?legacy=1")
        self.assertEqual(page.status_code, 200)
        self.assertIn(
            '<script type="module" src="/app/assets/console.js"></script>',
            page.text,
        )
        self.assertNotIn("<script>", page.text)

        html_bytes = len(page.content)
        console_js_bytes = self._get_size("/app/assets/console.js")
        config_js_bytes = self._get_size("/app/assets/console-config.js")

        self.assertLessEqual(html_bytes, 32 * 1024)
        self.assertLessEqual(console_js_bytes, 120 * 1024)
        self.assertLessEqual(config_js_bytes, 8 * 1024)
        self.assertLessEqual(
            html_bytes + console_js_bytes + config_js_bytes,
            160 * 1024,
        )


if __name__ == "__main__":
    unittest.main()
