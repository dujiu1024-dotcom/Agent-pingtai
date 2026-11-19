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
                         gate_passed: bool, gate_failures: tuple[str, ...],
                         tenant_id: str = "") -> str:
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
                   (eval_run_id,tenant_id,dataset_id,dataset_version,model_key,report,gate_passed)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (report_id,tenant_id,report.dataset_id,report.dataset_version,model_key,
                 json.dumps(payload,ensure_ascii=False,default=str),gate_passed),
            )
        return report_id


    async def save_human_score(
        self, *, tenant_id: str, eval_run_id: str, case_id: str,
        reviewer_id: str, score: float, rationale: str = "",
    ) -> str:
        import math

        from agent_platform.observability.redaction import redact_text

        if not tenant_id.strip() or not reviewer_id.strip() or not case_id.strip():
            raise ValueError("tenant_id, reviewer_id and case_id are required")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("human score must be a finite number between 0 and 1")
        if len(rationale) > 4000:
            raise ValueError("rationale must be at most 4000 characters")
        score_id = str(uuid4())
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_eval_human_scores
                   (score_id,tenant_id,eval_run_id,case_id,reviewer_id,score,rationale)
                   SELECT %s,%s,run.eval_run_id,%s,%s,%s,%s
                   FROM platform_eval_runs run
                   JOIN platform_eval_cases item
                     ON item.dataset_id=run.dataset_id
                    AND item.dataset_version=run.dataset_version
                    AND item.case_id=%s
                   WHERE run.eval_run_id=%s AND run.tenant_id=%s
                   ON CONFLICT (eval_run_id,case_id,reviewer_id) DO UPDATE
                     SET score=EXCLUDED.score,rationale=EXCLUDED.rationale,updated_at=now()
                   RETURNING score_id""",
                (score_id,tenant_id,case_id,reviewer_id,float(score),
                 redact_text(rationale,max_chars=4000),case_id,eval_run_id,tenant_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("evaluation run or case not found in this tenant")
        return str(row["score_id"])

    async def list_human_scores(
        self, *, tenant_id: str, eval_run_id: str
    ) -> list[dict[str, Any]]:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT score_id,eval_run_id,case_id,reviewer_id,score,
                          rationale,created_at,updated_at
                   FROM platform_eval_human_scores
                   WHERE tenant_id=%s AND eval_run_id=%s
                   ORDER BY created_at,reviewer_id,case_id""",
                (tenant_id,eval_run_id),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def list_datasets(
        self, *, tenant_id: str, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List distinct dataset_ids with latest version + case count."""
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT dataset_id, MAX(version) AS latest_version,
                          MAX(case_count) AS case_count
                   FROM platform_eval_datasets
                   GROUP BY dataset_id
                   ORDER BY dataset_id
                   LIMIT %s""",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def latest_reports(
        self, dataset_id: str | None = None, *, tenant_id: str, limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        connection = await self._connect()
        async with connection:
            if dataset_id and dataset_id.strip():
                cursor = await connection.execute(
                    """SELECT eval_run_id,tenant_id,dataset_id,dataset_version,model_key,report,
                              gate_passed,created_at
                       FROM platform_eval_runs WHERE dataset_id=%s AND tenant_id=%s
                       ORDER BY created_at DESC LIMIT %s""",
                    (dataset_id,tenant_id,limit),
                )
            else:
                cursor = await connection.execute(
                    """SELECT eval_run_id,tenant_id,dataset_id,dataset_version,model_key,report,
                              gate_passed,created_at
                       FROM platform_eval_runs WHERE tenant_id=%s
                       ORDER BY created_at DESC LIMIT %s""",
                    (tenant_id,limit),
                )
            rows = await cursor.fetchall()
        return [{
            "eval_run_id": str(row["eval_run_id"]), "tenant_id": row["tenant_id"],
            "dataset_id": row["dataset_id"], "dataset_version": row["dataset_version"],
            "model_key": row["model_key"], "report": row["report"],
            "gate_passed": row["gate_passed"], "created_at": row["created_at"],
        } for row in rows]


    async def load_report_for_tenant(self, *, tenant_id: str, eval_run_id: str) -> dict[str, Any]:
        if not tenant_id.strip() or not eval_run_id.strip():
            raise ValueError("tenant_id and eval_run_id are required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT eval_run_id,tenant_id,dataset_id,dataset_version,model_key,report,
                          gate_passed,created_at
                   FROM platform_eval_runs WHERE eval_run_id=%s AND tenant_id=%s""",
                (eval_run_id,tenant_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("evaluation run not found in this tenant")
        payload = row["report"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        metrics = {
            key: value for key, value in {
                "pass_rate": payload.get("pass_rate"),
                "average_score": payload.get("average_score"),
                "average_recall_at_k": payload.get("average_recall_at_k"),
            }.items() if value is not None
        }
        metrics.update({key: value for key, value in payload.get("metrics", {}).items() if value is not None})
        return {
            "eval_run_id": str(row["eval_run_id"]), "tenant_id": row["tenant_id"],
            "dataset_id": row["dataset_id"], "dataset_version": row["dataset_version"],
            "model_key": row["model_key"], "metrics": metrics, "report": payload,
            "gate_passed": row["gate_passed"],
        }

    async def save_comparison(self, *, tenant_id: str, comparison) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        payload = {
            "deltas": [asdict(item) for item in comparison.deltas],
            "regressions": list(comparison.regressions),
        }
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_eval_comparisons
                   (comparison_id,tenant_id,baseline_run_id,candidate_run_id,comparison)
                   SELECT %s,%s,base.eval_run_id,candidate.eval_run_id,%s::jsonb
                   FROM platform_eval_runs base
                   JOIN platform_eval_runs candidate
                     ON candidate.eval_run_id=%s AND candidate.tenant_id=%s
                   WHERE base.eval_run_id=%s AND base.tenant_id=%s""",
                (comparison.comparison_id,tenant_id,
                 json.dumps(payload,ensure_ascii=False),comparison.candidate_run_id,
                 tenant_id,comparison.baseline_run_id,tenant_id),
            )

    async def save_gate_evidence(self, evidence) -> None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_eval_gate_evidence
                   (evidence_id,tenant_id,candidate_run_id,baseline_run_id,policy_id,
                    policy_version,policy_snapshot,passed,failures,comparison_id,evidence_refs,
                    evaluated_at_utc,content_sha256)
                   SELECT %s,%s,candidate.eval_run_id,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s
                   FROM platform_eval_runs candidate
                   WHERE candidate.eval_run_id=%s AND candidate.tenant_id=%s
                   RETURNING evidence_id""",
                (evidence.evidence_id,evidence.tenant_id,evidence.baseline_run_id,
                 evidence.policy_id,evidence.policy_version,
                 json.dumps(dict(evidence.policy_snapshot),ensure_ascii=False),evidence.passed,
                 list(evidence.failures),evidence.comparison_id,list(evidence.evidence_refs),
                 evidence.evaluated_at_utc,evidence.content_sha256,
                 evidence.candidate_run_id,evidence.tenant_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("candidate evaluation run not found in this tenant")
