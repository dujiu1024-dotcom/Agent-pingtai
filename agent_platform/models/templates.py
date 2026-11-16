"""Versioned model templates with strict separation from credentials."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agent_platform.contracts import ModelTemplateRef


class ModelTemplateError(ValueError):
    """Raised when a model template is invalid or unavailable."""


_SECRET_KEYS = {
    "api_key",
    "api_secret",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "client_secret",
    "private_key",
    "authorization",
}


def _reject_embedded_secrets(value: Any, path: str = "template") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _SECRET_KEYS:
                raise ModelTemplateError(
                    f"credential field is forbidden in model template: {path}.{key}"
                )
            _reject_embedded_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_embedded_secrets(child, f"{path}[{index}]")


@dataclass(frozen=True)
class ModelTemplate:
    template_id: str
    version: int
    provider: str
    model: str
    fallback_model: str | None
    parameters: Mapping[str, Any]
    capabilities: tuple[str, ...]
    allowed_data_levels: tuple[str, ...]
    status: str
    definition: Mapping[str, Any]


class ModelTemplateRegistry:
    def __init__(self, templates: Mapping[tuple[str, int], ModelTemplate]) -> None:
        self._templates = dict(templates)

    @classmethod
    def from_file(cls, path: str | Path) -> "ModelTemplateRegistry":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelTemplateError(f"cannot load model templates: {exc}") from exc

        if not isinstance(raw, dict) or not isinstance(raw.get("templates"), list):
            raise ModelTemplateError("model template file must contain a templates array")

        templates: dict[tuple[str, int], ModelTemplate] = {}
        for index, item in enumerate(raw["templates"]):
            if not isinstance(item, dict):
                raise ModelTemplateError(f"templates[{index}] must be an object")
            _reject_embedded_secrets(item, f"templates[{index}]")

            try:
                template_id = str(item["id"]).strip()
                version = int(item["version"])
                provider = str(item["provider"]).strip()
                model = str(item["model"]).strip()
                parameters = item["parameters"]
                status = str(item.get("status", "draft"))
            except (KeyError, TypeError, ValueError) as exc:
                raise ModelTemplateError(
                    f"templates[{index}] is missing required fields"
                ) from exc

            if not template_id or version < 1 or not provider or not model:
                raise ModelTemplateError(f"templates[{index}] has invalid identity/provider/model")
            if not isinstance(parameters, dict):
                raise ModelTemplateError(f"templates[{index}].parameters must be an object")
            if status not in {"draft", "active", "deprecated"}:
                raise ModelTemplateError(f"templates[{index}] has unsupported status")

            key = (template_id, version)
            if key in templates:
                raise ModelTemplateError(f"duplicate model template: {template_id}@{version}")

            templates[key] = ModelTemplate(
                template_id=template_id,
                version=version,
                provider=provider,
                model=model,
                fallback_model=item.get("fallback_model"),
                parameters=dict(parameters),
                capabilities=tuple(item.get("capabilities", ())),
                allowed_data_levels=tuple(item.get("allowed_data_levels", ())),
                status=status,
                definition=dict(item),
            )
        return cls(templates)

    def get(
        self, ref: ModelTemplateRef, *, require_active: bool = True
    ) -> ModelTemplate:
        template = self._templates.get((ref.template_id, ref.version))
        if template is None:
            raise ModelTemplateError(
                f"model template not found: {ref.template_id}@{ref.version}"
            )
        if require_active and template.status != "active":
            raise ModelTemplateError(
                f"model template is not active: {ref.template_id}@{ref.version}"
            )
        if template.status == "active" and template.model.startswith("replace-with-"):
            raise ModelTemplateError("active model template cannot use a placeholder model")
        return template

    def list_templates(self) -> tuple[ModelTemplate, ...]:
        return tuple(
            sorted(
                self._templates.values(),
                key=lambda item: (item.template_id, item.version),
            )
        )
