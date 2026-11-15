"""Bounded structured-output validation and repair at the Harness boundary."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Mapping

from agent_platform.runtime.protocols import HarnessAdapter, HarnessRequest, HarnessResult


class StructuredOutputValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("structured output validation failed: " + "; ".join(errors))


def _validate(value: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    type_checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if expected in type_checks and not type_checks[expected](value):
        return [f"{path}: expected {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: required property is missing")
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{path}.{key}: additional property is not allowed")
        for key, child in properties.items():
            if key in value and isinstance(child, Mapping):
                errors.extend(_validate(value[key], child, f"{path}.{key}"))
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                errors.extend(_validate(item, item_schema, f"{path}[{index}]"))
    elif isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    return errors


def validate_structured_output(raw: str, schema: Mapping[str, Any]) -> tuple[Any | None, list[str]]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        return None, [f"$: invalid JSON ({type(exc).__name__})"]
    return value, _validate(value, schema)


class SchemaValidatedHarnessAdapter:
    """Validate JSON output and perform bounded repair attempts through the same adapter."""

    def __init__(self, adapter: HarnessAdapter, *, max_retries: int = 1) -> None:
        if max_retries < 0 or max_retries > 3:
            raise ValueError("max_retries must be between 0 and 3")
        self.adapter = adapter
        self.max_retries = max_retries

    async def run(self, request: HarnessRequest) -> HarnessResult:
        if request.output_schema is None:
            return await self.adapter.run(request)
        current = request
        last_errors: list[str] = []
        for attempt in range(self.max_retries + 1):
            result = await self.adapter.run(current)
            value, errors = validate_structured_output(
                result.output_text, request.output_schema,
            )
            if not errors:
                return replace(
                    result,
                    metadata={
                        **result.metadata,
                        "structured_output_valid": True,
                        "structured_output_attempts": attempt + 1,
                        "structured_output": value,
                    },
                )
            last_errors = errors
            if attempt < self.max_retries:
                current = replace(
                    request,
                    instructions=(
                        request.instructions
                        + "\n\nReturn only valid JSON matching the required schema. "
                        + "Correct the previous output; do not add prose. "
                        + "Validation errors: " + "; ".join(errors)
                        + "\nPrevious output: " + result.output_text
                    ),
                )
        raise StructuredOutputValidationError(last_errors)
