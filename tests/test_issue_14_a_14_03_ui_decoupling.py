from __future__ import annotations

import ast
import http.client
import threading
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import console
from agent_platform.web.app import create_platform_web_router


ROOT = Path(__file__).resolve().parents[1]


class PlatformUiDecouplingAcceptanceTests(unittest.TestCase):
    def test_new_ui_is_served_from_independent_platform_router(self):
        app = FastAPI()
        app.include_router(create_platform_web_router())
        client = TestClient(app)

        page = client.get("/app/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Agent Platform Workbench", page.text)
        self.assertEqual(client.get("/app").status_code, 200)

        script = client.get("/app/assets/app.js")
        styles = client.get("/app/assets/style.css")
        self.assertEqual(script.status_code, 200)
        self.assertEqual(styles.status_code, 200)
        self.assertIn("workbench/capabilities", script.text)
        self.assertEqual(client.get("/app/assets/unknown.js").status_code, 404)
        # The new UI owns its /app namespace; it does not take over the FDE root.
        self.assertEqual(client.get("/").status_code, 404)

    def test_platform_composition_does_not_import_legacy_console_or_gui(self):
        source = (ROOT / "agent_platform" / "api" / "app.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("console", imported)
        self.assertNotIn("gui", imported)
        self.assertIn("create_platform_web_router", source)

    def test_legacy_fde_console_root_and_api_entry_remain_available(self):
        server = console.ConsoleServer(("127.0.0.1", 0), console.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/")
            response = conn.getresponse()
            page = response.read().decode("utf-8")
            conn.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(page, console.HTML_PATH.read_text(encoding="utf-8"))

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/api/state")
            response = conn.getresponse()
            response.read()
            conn.close()
            self.assertEqual(response.status, 401)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
