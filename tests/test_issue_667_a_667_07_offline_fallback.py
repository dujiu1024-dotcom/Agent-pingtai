from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.web.app import create_platform_web_router


ROOT = Path(__file__).resolve().parents[1]
SERVICE_WORKER = ROOT / "agent_platform" / "web" / "static" / "sw.js"


class OfflineFallbackAcceptanceTests(unittest.TestCase):
    def test_service_worker_is_served_with_app_scope(self) -> None:
        app = FastAPI()
        app.include_router(create_platform_web_router())
        response = TestClient(app).get("/app/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
        self.assertEqual(response.headers["service-worker-allowed"], "/app/")
        self.assertIn('CACHE_NAME = "platform-ui-shell-v1"', response.text)

    def test_workbench_registers_scoped_service_worker(self) -> None:
        app = FastAPI()
        app.include_router(create_platform_web_router())
        client = TestClient(app)
        page = client.get("/app/")
        script = client.get("/app/assets/app.js")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/app/assets/app.js", page.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn('navigator.serviceWorker.register("/app/sw.js"', script.text)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for service-worker runtime test")
    def test_node_service_worker_runtime_caches_shell_and_serves_offline_navigation(self) -> None:
        harness = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
const handlers = {};
const cachesByName = new Map();
const cacheNames = ["platform-ui-shell-old"];
const oldCache = new Map([["/app/", {marker: "old-shell"}]]);
cachesByName.set("platform-ui-shell-old", oldCache);
const caches = {
  open: async (name) => {
    if (!cachesByName.has(name)) {
      cachesByName.set(name, new Map());
      cacheNames.push(name);
    }
    const entries = cachesByName.get(name);
    return {
      addAll: async (keys) => keys.forEach((key) => entries.set(key, {marker: key})),
      match: async (key) => entries.get(typeof key === "string" ? key : key.url),
      put: async (key, value) => entries.set(typeof key === "string" ? key : key.url, value)
    };
  },
  keys: async () => Array.from(cachesByName.keys()),
  delete: async (name) => cachesByName.delete(name),
  match: async (key) => {
    for (const entries of cachesByName.values()) {
      const value = entries.get(typeof key === "string" ? key : key.url);
      if (value) return value;
    }
    return undefined;
  }
};
const self = {
  location: {origin: "https://platform.test"},
  clients: {claim: async () => { self.claimed = true; }},
  skipWaiting: async () => { self.waitingSkipped = true; },
  addEventListener: (name, callback) => { handlers[name] = callback; }
};
const context = {
  self, caches, URL,
  fetch: async () => { throw new Error("offline"); },
  Response: {error: () => ({marker: "network-error"})}
};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), context);
(async () => {
  await handlers.install({waitUntil: (promise) => promise});
  const activeCache = cachesByName.get("platform-ui-shell-v1");
  assert(activeCache.has("/app/"));
  assert(activeCache.has("/app/assets/app.js"));
  assert(activeCache.has("/app/assets/style.css"));
  assert.strictEqual(self.waitingSkipped, true);

  await handlers.activate({waitUntil: (promise) => promise});
  assert.strictEqual(cachesByName.has("platform-ui-shell-old"), false);
  assert.strictEqual(self.claimed, true);

  let navigation;
  handlers.fetch({
    request: {url: "https://platform.test/app/", method: "GET", mode: "navigate"},
    respondWith: (promise) => { navigation = promise; }
  });
  const fallback = await navigation;
  assert.strictEqual(fallback.marker, "/app/");

  let asset;
  handlers.fetch({
    request: {url: "https://platform.test/app/assets/app.js", method: "GET", mode: "same-origin"},
    respondWith: (promise) => { asset = promise; }
  });
  const cachedAsset = await asset;
  assert.strictEqual(cachedAsset.marker, "/app/assets/app.js");
  console.log("service-worker install/activate/offline-navigation/cache-hit: PASS");
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(
            ["node", "-e", harness, str(SERVICE_WORKER)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("offline-navigation", result.stdout)


if __name__ == "__main__":
    unittest.main()
