"""Tenant-scoped, immutable evaluation datasets with CSV/JSONL interchange."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

MAX_IMPORT_BYTES = 2_000_000
MAX_CASES = 2_000
MAX_FIELD_CHARS = 20_000
_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class EvaluationDatasetCase:
    case_id: str
    input: str
    expected_output: str
    rubric: dict[str, Any]
    source_ref: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.case_id, str) or not self.case_id.strip()
            or len(self.case_id) > 128
        ):
            raise ValueError("case_id must be a non-empty string of at most 128 characters")
        if not isinstance(self.input, str) or not self.input.strip():
            raise ValueError("input must be a non-empty string")
        if not isinstance(self.expected_output, str) or not self.expected_output.strip():
            raise ValueError("expected_output must be a non-empty string")
        if len(self.input) > MAX_FIELD_CHARS or len(self.expected_output) > MAX_FIELD_CHARS:
            raise ValueError("input and expected_output must be at most 20000 characters")
        if not isinstance(self.rubric, dict) or not self.rubric:
            raise ValueError("rubric must be a non-empty JSON object")
        if len(json.dumps(self.rubric, ensure_ascii=False)) > 10_000:
            raise ValueError("rubric must be at most 10000 serialized characters")
        if self.source_ref is not None and (
            not isinstance(self.source_ref, str) or not self.source_ref.strip()
            or len(self.source_ref) > 2048
        ):
            raise ValueError("source_ref must be a non-empty string of at most 2048 characters")


@dataclass(frozen=True)
class EvaluationDatasetVersion:
    tenant_id: str
    dataset_id: str
    version: int
    cases: tuple[EvaluationDatasetCase, ...]
    created_by: str
    created_at: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tenant_id, str) or not self.tenant_id.strip()
            or not isinstance(self.created_by, str) or not self.created_by.strip()
        ):
            raise ValueError("tenant_id and created_by are required")
        if not isinstance(self.dataset_id, str) or not _DATASET_ID.fullmatch(self.dataset_id):
            raise ValueError("dataset_id has an invalid format")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be a positive integer")
        if not self.cases or len(self.cases) > MAX_CASES:
            raise ValueError("dataset must contain between 1 and 2000 cases")
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id values must be unique within a dataset version")


class EvaluationDatasetRepository(Protocol):
    async def create_version(
        self, *, tenant_id: str, dataset_id: str, cases: tuple[EvaluationDatasetCase, ...],
        created_by: str,
    ) -> EvaluationDatasetVersion: ...

    async def get_version(
        self, *, tenant_id: str, dataset_id: str, version: int,
    ) -> EvaluationDatasetVersion: ...

    async def list_versions(
        self, *, tenant_id: str, dataset_id: str,
    ) -> list[EvaluationDatasetVersion]: ...


class InMemoryEvaluationDatasetRepository:
    """Reference adapter; production deployments should use PostgresEvaluationDatasetRepository."""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, str, int], EvaluationDatasetVersion] = {}

    async def create_version(
        self, *, tenant_id: str, dataset_id: str, cases: tuple[EvaluationDatasetCase, ...],
        created_by: str,
    ) -> EvaluationDatasetVersion:
        if (
            not isinstance(tenant_id, str) or not tenant_id.strip()
            or not isinstance(created_by, str) or not created_by.strip()
        ):
            raise ValueError("tenant_id and created_by are required")
        if not isinstance(dataset_id, str) or not _DATASET_ID.fullmatch(dataset_id):
            raise ValueError("dataset_id has an invalid format")
        if not cases or len(cases) > MAX_CASES:
            raise ValueError("dataset must contain between 1 and 2000 cases")
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("case_id values must be unique within a dataset version")
        current = [v for (tenant, name, _), v in self._versions.items()
                   if tenant == tenant_id and name == dataset_id]
        version = max((item.version for item in current), default=0) + 1
        record = EvaluationDatasetVersion(
            tenant_id, dataset_id, version, tuple(cases), created_by,
        )
        self._versions[(tenant_id, dataset_id, version)] = record
        return record

    async def get_version(
        self, *, tenant_id: str, dataset_id: str, version: int,
    ) -> EvaluationDatasetVersion:
        record = self._versions.get((tenant_id, dataset_id, version))
        if record is None:
            raise KeyError("evaluation dataset version not found")
        return record

    async def list_versions(
        self, *, tenant_id: str, dataset_id: str,
    ) -> list[EvaluationDatasetVersion]:
        return sorted(
            (record for (tenant, name, _), record in self._versions.items()
             if tenant == tenant_id and name == dataset_id),
            key=lambda item: item.version, reverse=True,
        )


class PostgresEvaluationDatasetRepository:
    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def create_version(
        self, *, tenant_id: str, dataset_id: str, cases: tuple[EvaluationDatasetCase, ...],
        created_by: str,
    ) -> EvaluationDatasetVersion:
        # Validate before opening a transaction; SQL advisory lock serializes versions per tenant/name.
        probe = EvaluationDatasetVersion(tenant_id, dataset_id, 1, cases, created_by)
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"eval-dataset:{tenant_id}:{dataset_id}",),
                )
                cursor = await connection.execute(
                    """SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                       FROM platform_eval_dataset_versions
                       WHERE tenant_id=%s AND dataset_id=%s""",
                    (tenant_id, dataset_id),
                )
                row = await cursor.fetchone()
                version = int(row["next_version"])
                await connection.execute(
                    """INSERT INTO platform_eval_dataset_versions
                       (tenant_id,dataset_id,version,created_by,case_count)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (tenant_id, dataset_id, version, created_by, len(probe.cases)),
                )
                for case in probe.cases:
                    await connection.execute(
                        """INSERT INTO platform_eval_dataset_cases
                           (tenant_id,dataset_id,version,case_id,input_text,expected_output,rubric,source_ref)
                           VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                        (tenant_id, dataset_id, version, case.case_id, case.input,
                         case.expected_output, json.dumps(case.rubric, ensure_ascii=False),
                         case.source_ref),
                    )
        return EvaluationDatasetVersion(tenant_id, dataset_id, version, probe.cases, created_by)

    async def get_version(
        self, *, tenant_id: str, dataset_id: str, version: int,
    ) -> EvaluationDatasetVersion:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT dataset_id,version,created_by,created_at
                   FROM platform_eval_dataset_versions
                   WHERE tenant_id=%s AND dataset_id=%s AND version=%s""",
                (tenant_id, dataset_id, version),
            )
            header = await cursor.fetchone()
            if header is None:
                raise KeyError("evaluation dataset version not found")
            cursor = await connection.execute(
                """SELECT case_id,input_text,expected_output,rubric,source_ref
                   FROM platform_eval_dataset_cases
                   WHERE tenant_id=%s AND dataset_id=%s AND version=%s
                   ORDER BY case_id""",
                (tenant_id, dataset_id, version),
            )
            rows = await cursor.fetchall()
        cases = tuple(EvaluationDatasetCase(
            row["case_id"], row["input_text"], row["expected_output"],
            row["rubric"] if isinstance(row["rubric"], dict) else json.loads(row["rubric"]),
            row["source_ref"],
        ) for row in rows)
        return EvaluationDatasetVersion(
            tenant_id, header["dataset_id"], int(header["version"]), cases,
            header["created_by"], str(header["created_at"]),
        )

    async def list_versions(
        self, *, tenant_id: str, dataset_id: str,
    ) -> list[EvaluationDatasetVersion]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT version FROM platform_eval_dataset_versions
                   WHERE tenant_id=%s AND dataset_id=%s ORDER BY version DESC LIMIT 100""",
                (tenant_id, dataset_id),
            )
            rows = await cursor.fetchall()
        return [await self.get_version(
            tenant_id=tenant_id, dataset_id=dataset_id, version=int(row["version"]),
        ) for row in rows]


def parse_dataset_import(content: str, *, format: str) -> tuple[EvaluationDatasetCase, ...]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("dataset import content is required")
    if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ValueError("dataset import exceeds 2 MB")
    rows: list[dict[str, Any]] = []
    if format == "jsonl":
        for line_number, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL line {line_number} must be an object")
            rows.append(value)
    elif format == "csv":
        try:
            rows = list(csv.DictReader(io.StringIO(content)))
        except csv.Error as exc:
            raise ValueError("invalid CSV dataset") from exc
    else:
        raise ValueError("format must be csv or jsonl")
    if not rows or len(rows) > MAX_CASES:
        raise ValueError("dataset must contain between 1 and 2000 cases")
    cases: list[EvaluationDatasetCase] = []
    for index, row in enumerate(rows, start=1):
        allowed = {"case_id", "input", "expected_output", "rubric", "source_ref"}
        if set(row) - allowed or not {"input", "expected_output", "rubric"} <= set(row):
            raise ValueError(f"dataset row {index} has missing or unknown fields")
        rubric = row["rubric"]
        if isinstance(rubric, str):
            try:
                rubric = json.loads(rubric)
            except json.JSONDecodeError as exc:
                raise ValueError(f"dataset row {index} rubric must be a JSON object") from exc
        if not isinstance(rubric, dict):
            raise ValueError(f"dataset row {index} rubric must be a JSON object")
        case_id = row.get("case_id") or f"case-{index}"
        cases.append(EvaluationDatasetCase(
            case_id=case_id, input=row["input"], expected_output=row["expected_output"],
            rubric=rubric, source_ref=row.get("source_ref") or None,
        ))
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("case_id values must be unique within an import")
    return tuple(cases)


def export_dataset(dataset: EvaluationDatasetVersion, *, format: str) -> str:
    rows = [{
        "case_id": case.case_id, "input": case.input,
        "expected_output": case.expected_output, "rubric": case.rubric,
        "source_ref": case.source_ref,
    } for case in dataset.cases]
    if format == "jsonl":
        return "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)
    if format == "csv":
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output, fieldnames=["case_id", "input", "expected_output", "rubric", "source_ref"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "rubric": json.dumps(row["rubric"], ensure_ascii=False)})
        return output.getvalue()
    raise ValueError("format must be csv or jsonl")


def new_dataset_id() -> str:
    return f"eval-{uuid4().hex}"
