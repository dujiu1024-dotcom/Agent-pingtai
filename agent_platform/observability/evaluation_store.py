"""Durable evaluation datasets and release-gate reports."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Callable
from uuid import uuid4

from agent_platform.observability.evaluation import EvalCase, EvalDataset, EvalReport


class PostgresEvaluationRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def save_dataset(self, dataset: EvalDataset, *, created_by: str) -> None:
        if not created_by.strip():
            raise ValueError("created_by is required")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"{dataset.dataset_id}:{dataset.version}",),
                )
                await connection.execute(
                    """INSERT INTO platform_eval_datasets
                       (dataset_id,version,created_by,case_count)
                       VALUES (%s,%s,%s,%s)""",
                    (dataset.dataset_id,dataset.version,created_by,len(dataset.cases)),
                )
                for case in dataset.cases:
                    await connection.execute(
                        """INSERT INTO platform_eval_cases
                           (dataset_id,dataset_version,case_id,query,expected_terms,
                            forbidden_terms,expected_document_ids)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (dataset.dataset_id,dataset.version,case.case_id,case.query,
                         list(case.expected_terms),list(case.forbidden_terms),
                         list(case.expected_document_ids)),
                    )

    async def load_dataset(self, dataset_id: str, version: int) -> EvalDataset:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT dataset_id,version FROM platform_eval_datasets
                   WHERE dataset_id=%s AND version=%s""",
                (dataset_id,version),
            )
            header = await cursor.fetchone()
            if header is None:
                raise KeyError("evaluation dataset version not found")
            cursor = await connection.execute(
                """SELECT case_id,query,expected_terms,forbidden_terms,expected_document_ids
                   FROM platform_eval_cases
                   WHERE dataset_id=%s AND dataset_version=%s ORDER BY case_id""",
                (dataset_id,version),
            )
            rows = await cursor.fetchall()
        return EvalDataset(
            dataset_id=header["dataset_id"], version=int(header["version"]),
            cases=tuple(EvalCase(
                case_id=row["case_id"], query=row["query"],
                expected_terms=tuple(row["expected_terms"]),
                forbidden_terms=tuple(row["forbidden_terms"]),
                expected_document_ids=tuple(row["expected_document_ids"]),
            ) for row in rows),
        )

    async def save_report(self, report: EvalReport, *, model_key: str,
                         gate_passed: bool, gate_failures: tuple[str, ...]) -> str:
        if not model_key.strip():
            raise ValueError("model_key is required")
        report_id = str(uuid4())
        payload = {
            "dataset_id": report.dataset_id,
            "dataset_version": report.dataset_version,
            "pass_rate": report.pass_rate,
            "average_score": report.average_score,
            "average_recall_at_k": report.average_recall_at_k,
            "results": [asdict(item) for item in report.results],
            "gate_failures": list(gate_failures),
        }
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_eval_runs
                   (eval_run_id,dataset_id,dataset_version,model_key,report,gate_passed)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
                (report_id,report.dataset_id,report.dataset_version,model_key,
                 json.dumps(payload,ensure_ascii=False,default=str),gate_passed),
            )
        return report_id

    async def latest_reports(self, dataset_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT eval_run_id,dataset_id,dataset_version,model_key,report,
                          gate_passed,created_at
                   FROM platform_eval_runs WHERE dataset_id=%s
                   ORDER BY created_at DESC LIMIT %s""",
                (dataset_id,limit),
            )
            rows = await cursor.fetchall()
        return [{
            "eval_run_id": str(row["eval_run_id"]), "dataset_id": row["dataset_id"],
            "dataset_version": row["dataset_version"], "model_key": row["model_key"],
            "report": row["report"], "gate_passed": row["gate_passed"],
            "created_at": row["created_at"],
        } for row in rows]
