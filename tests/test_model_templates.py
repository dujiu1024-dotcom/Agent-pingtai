import json
import tempfile
import unittest
from pathlib import Path

from agent_platform.contracts import ModelTemplateRef
from agent_platform.models.templates import (
    ModelTemplateError,
    ModelTemplateRegistry,
)


class ModelTemplateRegistryTests(unittest.TestCase):
    def write_config(self, template):
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump({"version": 1, "templates": [template]}, handle)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return handle.name

    def template(self, **overrides):
        value = {
            "id": "general",
            "version": 1,
            "provider": "openai-compatible",
            "model": "model-a",
            "fallback_model": None,
            "parameters": {"temperature": 0.2, "max_tokens": 1000},
            "capabilities": ["text"],
            "allowed_data_levels": ["public"],
            "status": "active",
        }
        value.update(overrides)
        return value

    def test_resolves_active_template(self):
        registry = ModelTemplateRegistry.from_file(
            self.write_config(self.template())
        )
        template = registry.get(ModelTemplateRef("general", 1))
        self.assertEqual(template.model, "model-a")

    def test_draft_template_is_not_runtime_selectable(self):
        registry = ModelTemplateRegistry.from_file(
            self.write_config(self.template(status="draft"))
        )
        with self.assertRaises(ModelTemplateError):
            registry.get(ModelTemplateRef("general", 1))

    def test_embedded_credentials_are_rejected(self):
        with self.assertRaises(ModelTemplateError):
            ModelTemplateRegistry.from_file(
                self.write_config(self.template(api_key="do-not-store"))
            )

    def test_active_placeholder_model_is_rejected(self):
        registry = ModelTemplateRegistry.from_file(
            self.write_config(self.template(model="replace-with-model"))
        )
        with self.assertRaises(ModelTemplateError):
            registry.get(ModelTemplateRef("general", 1))

    def test_duplicate_versions_are_rejected(self):
        path = self.write_config(self.template())
        raw = json.loads(Path(path).read_text())
        raw["templates"].append(self.template())
        Path(path).write_text(json.dumps(raw))
        with self.assertRaises(ModelTemplateError):
            ModelTemplateRegistry.from_file(path)


if __name__ == "__main__":
    unittest.main()
