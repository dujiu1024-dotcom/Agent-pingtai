#!/usr/bin/env python3
"""policy.py 测试（T-0027）：策略校验、来源解释、模型选择与降级链。"""

import json
import tempfile
import unittest
from pathlib import Path

import policy as pm


class FakeCon:
    """只实现 config_kv 读取，够 explain/select 用。"""

    def __init__(self, kv=None):
        self.kv = kv or {}

    def execute(self, sql, params=()):
        value = self.kv.get(params[0]) if params else None

        class Row:
            def fetchone(self):
                return {"value": value} if value is not None else None

        return Row()


def health(ok=True, models=("qwen3:1.7b", "bge-m3"), error=""):
    return {"ok": ok, "url": "http://127.0.0.1:11434",
            "models": list(models), "ms": 1, "error": error}


def sample():
    return {
        "version": 1,
        "models": {"judge": {"primary": "qwen3:1.7b", "fallback": ["rules"]}},
        "apps": {"textedit": {"backend": "peekaboo",
                             "verified_at": "2026-11-11"}},
        "risk": {"click": "A1"},
        "night": {"window": "23:00-08:00", "allow": []},
    }


class ValidateTest(unittest.TestCase):
    def test_real_policy_loads(self):
        data = pm.load_policy()
        self.assertEqual(data["version"], 1)
        self.assertIn("judge", data["models"])

    def test_unknown_keys(self):
        for mutate in (
            lambda d: d.update(extra=1),
            lambda d: d["models"].update(singing={"primary": "x",
                                                  "fallback": []}),
            lambda d: d["apps"]["textedit"].update(port=1),
            lambda d: d["night"].update(extra=1),
        ):
            data = sample()
            mutate(data)
            with self.assertRaises(pm.PolicyError):
                pm.validate_policy(data)

    def test_unknown_action_rejected(self):
        data = sample()
        data["risk"]["send"] = "A2"
        with self.assertRaises(pm.PolicyError) as ctx:
            pm.validate_policy(data)
        self.assertIn("未登记动作", str(ctx.exception))

    def test_bad_values(self):
        for mutate in (
            lambda d: d["models"]["judge"].update(primary="Qwen Big"),
            lambda d: d["models"]["judge"].update(fallback="rules"),
            lambda d: d["apps"]["textedit"].update(backend="ffi"),
            lambda d: d["apps"]["textedit"].update(verified_at="2099-01-01"),
            lambda d: d["night"].update(window="25:00-26:00"),
            lambda d: d["night"].update(allow=["textedit"]),
            lambda d: d["night"].update(allow=["textedit:send"]),
            lambda d: d["risk"].update(click="A9"),
        ):
            data = sample()
            mutate(data)
            with self.assertRaises(pm.PolicyError):
                pm.validate_policy(data)

    def test_missing_file_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = pm.load_policy(Path(tmp) / "none.json", must_exist=False)
            self.assertEqual(data["night"]["window"], "23:00-08:00")
            self.assertTrue(data["models"])


class SelectTest(unittest.TestCase):
    def test_local_ready(self):
        out = pm.select("judge", health=health())
        self.assertEqual(out["model"], "qwen3:1.7b")

    def test_missing_model_falls_to_rules(self):
        out = pm.select("judge", health=health(models=("bge-m3",)))
        self.assertEqual(out["model"], "rules:local")
        self.assertIn("未装", out["reason"])

    def test_ollama_down_falls_to_rules(self):
        out = pm.select("judge", health=health(ok=False,
                                               error="Ollama 不可达"))
        self.assertEqual(out["model"], "rules:local")
        self.assertIn("不可达", out["reason"])

    def test_config_override(self):
        con = FakeCon({"llm.route.judge": "qwen3:8b"})
        out = pm.select("judge", con=con, health=health())
        self.assertEqual(out["model"], "qwen3:1.7b")
        self.assertIn("qwen3:8b", out["chain"])
        self.assertIn("config_kv", out["reason"])
        self.assertIn("未装", out["reason"])

    def test_confidential_forces_local(self):
        out = pm.select("generate", sensitivity="confidential",
                        health=health())
        self.assertEqual(out["model"], "qwen3:1.7b")
        self.assertNotIn("remote:doubao", out["chain"])

    def test_remote_primary(self):
        out = pm.select("plan", health=health())
        self.assertEqual(out["model"], "remote:doubao")

    def test_unknown_purpose(self):
        with self.assertRaises(pm.PolicyError):
            pm.select("draw")


class ExplainTest(unittest.TestCase):
    def test_policy_sources(self):
        out = pm.explain("textedit", "click", con=FakeCon())
        self.assertEqual(out["backend"]["value"], "peekaboo")
        self.assertEqual(out["backend"]["source"], "policy.apps")
        self.assertEqual(out["risk"]["value"], "A1")
        self.assertEqual(out["risk"]["source"], "policy.risk")
        self.assertEqual(out["night"]["window"]["source"], "policy.night")

    def test_config_overrides(self):
        con = FakeCon({"policy.gui.apps.textedit.backend": "api",
                       "policy.gui.risk.click": "A3",
                       "policy.gui.night.window": "22:00-07:00",
                       "policy.gui.night.allow": '["textedit:click"]'})
        out = pm.explain("textedit", "click", con=con)
        self.assertEqual(out["backend"]["value"], "api")
        self.assertEqual(out["backend"]["source"], "config_kv")
        self.assertEqual(out["risk"]["value"], "A3")
        self.assertEqual(out["night"]["window"]["value"], "22:00-07:00")
        self.assertEqual(out["night"]["allow"]["value"], ["textedit:click"])

    def test_unregistered_action(self):
        with self.assertRaises(pm.PolicyError):
            pm.explain("textedit", "send", con=FakeCon())

    def test_meta_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gui.json"
            path.write_text(json.dumps(sample()), encoding="utf-8")
            first = pm.meta(path)
            second = pm.meta(path)
            self.assertEqual(first["sha256"], second["sha256"])
            path.write_text(json.dumps(sample()) + "\n", encoding="utf-8")
            self.assertNotEqual(first["sha256"], pm.meta(path)["sha256"])


class ListModelsTest(unittest.TestCase):
    def test_rows(self):
        out = pm.list_models(health=health())
        self.assertEqual(len(out["routes"]), len(pm.PURPOSES))
        judge = [r for r in out["routes"] if r["purpose"] == "judge"][0]
        self.assertIn("qwen3:1.7b=已装", judge["status"])

    def test_down(self):
        out = pm.list_models(health=health(ok=False, models=()))
        judge = [r for r in out["routes"] if r["purpose"] == "judge"][0]
        self.assertIn("qwen3:1.7b=不可达", judge["status"])


if __name__ == "__main__":
    unittest.main()
