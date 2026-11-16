"""Bounded structured-output validation and repair at the Harness boundary."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from typing import Any, Mapping

from agent_platform.runtime.protocols import HarnessAdapter, HarnessRequest, HarnessResult


class StructuredOutputSchemaRegistry:
    """In-process immutable-by-version schema registry for Harness output contracts."""

    def __init__(self) -> None:
        self._schemas: dict[tuple[str, int], dict[str, Any]] = {}

    def register(self, schema_id: str, version: int, schema: Mapping[str, Any]) -> None:
        if not isinstance(schema_id, str) or not schema_id.strip() or len(schema_id) > 128:
            raise ValueError("schema_id must be a non-empty string of at most 128 characters")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("schema version must be a positive integer")
        if not isinstance(schema, Mapping) or not schema:
            raise ValueError("structured output schema must be a non-empty object")
        key = (schema_id, version)
        value = copy.deepcopy(dict(schema))
        existing = self._schemas.get(key)
        if existing is not None and existing != value:
            raise ValueError("registered schema versions are immutable")
        self._schemas[key] = value

    def resolve(self, schema_id: str, version: int) -> dict[str, Any]:
        try:
            schema = self._schemas[(schema_id, version)]
        except KeyError as exc:
            raise KeyError(f"unknown structured output schema {schema_id}@{version}") from exc
        return copy.deepcopy(schema)


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

    def __init__(self, adapter: HarnessAdapter, *, max_retries: int = 1, schema_registry: StructuredOutputSchemaRegistry | None = None) -> None:
        if max_retries < 0 or max_retries > 3:
            raise ValueError("max_retries must be between 0 and 3")
        self.adapter = adapter
        self.max_retries = max_retries
        self.schema_registry = schema_registry

    async def run(self, request: HarnessRequest) -> HarnessResult:
        schema = request.output_schema
        schema_id = request.metadata.get("output_schema_id")
        schema_version = request.metadata.get("output_schema_version")
        if schema is None and (schema_id is not None or schema_version is not None):
            if not isinstance(schema_id, str) or not schema_id.strip():
                raise ValueError("output_schema_id is required with a versioned schema")
            if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
                raise ValueError("output_schema_version must be a positive integer")
            if self.schema_registry is None:
                raise RuntimeError("versioned output schema requested without a schema registry")
            schema = self.schema_registry.resolve(schema_id, schema_version)
        if schema is None:
            return await self.adapter.run(request)
        current = replace(request, output_schema=schema)
        last_errors: list[str] = []
        for attempt in range(self.max_retries + 1):
            result = await self.adapter.run(current)
            value, errors = validate_structured_output(
                result.output_text, schema,
            )
            if not errors:
                return replace(
                    result,
                    metadata={
                        **result.metadata,
                        "structured_output_valid": True,
                        "structured_output_attempts": attempt + 1,
                        "structured_output": value,
                        **({"output_schema_id": schema_id, "output_schema_version": schema_version} if schema_id is not None else {}),
                    },
                )
            last_errors = errors
            if attempt < self.max_retries:
                current = replace(
                    current,
                    instructions=(
                        request.instructions
                        + "\n\nReturn only valid JSON matching the required schema. "
                        + "Correct the previous output; do not add prose. "
                        + "Validation errors: " + "; ".join(errors)
                        + "\nPrevious output: " + result.output_text
                    ),
                )
        raise StructuredOutputValidationError(last_errors)
