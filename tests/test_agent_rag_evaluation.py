from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone

from agent_platform.observability.agent_rag_evaluation import (
    AgentRAGEvaluationService,
    CaseOutput,
    Citation,
    EvaluationCase,
    EvaluationDataset,
    EvaluationPlan,
    ModelEndpoint,
    PostgresAgentRAGEvaluationRepository,
    dataset_fingerprint,
    score_case,
)


class FakeEvaluator:
    def __init__(self):
        self.active = 0
        self.maximum = 0

    async def evaluate(self, case, *, tenant_id, endpoint):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            await asyncio.sleep(0)
            return CaseOutput(
                answer="alpha answer",
                retrieved_document_ids=("doc-a", "doc-b"),
                citations=(Citation("doc-a", "https://docs.example/a", "alpha"),),
            )
        finally:
            self.active -= 1


class FakeRepository:
    def __init__(self):
        self.saved = []

    async def save_evaluation_report(self, report):
        self.saved.append(report)


class AgentRAGEvaluationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dataset = EvaluationDataset("rag-release", 7, (
            EvaluationCase(
                "case-a", "question a", ("alpha",), ("forbidden",),
                ("doc-a",), ("doc-a",),
            ),
            EvaluationCase(
                "case-b", "question b", ("alpha",), (), ("doc-a",), ("doc-a",),
            ),
            EvaluationCase(
                "case-c", "question c", ("alpha",), (), ("doc-a",), ("doc-a",),
            ),
        ))
        self.endpoint = ModelEndpoint(
            "prod-eu", "answer-model", "https://model.example/v1", "2026-11-01",
        )

    def test_quality_metrics_cover_retrieval_answer_and_citations(self):
        case = self.dataset.cases[0]
        metrics = score_case(case, CaseOutput(
            "alpha answer", ("doc-a", "doc-x"),
            (Citation("doc-a", "https://docs.example/a"),),
        ), k=2)
        self.assertEqual(metrics.retrieval_recall_at_k, 1.0)
        self.assertEqual(metrics.retrieval_precision_at_k, 0.5)
        self.assertEqual(metrics.answer_term_f1, 1.0)
        self.assertEqual(metrics.citation_precision, 1.0)
        self.assertEqual(metrics.citation_recall, 1.0)
        self.assertEqual(metrics.citation_validity, 1.0)

    async def test_run_pins_dataset_endpoint_and_bounds_concurrency(self):
        evaluator, repository = FakeEvaluator(), FakeRepository()

        async def load(dataset_id, version):
            self.assertEqual((dataset_id, version), ("rag-release", 7))
            return self.dataset

        service = AgentRAGEvaluationService(
            dataset_loader=load, evaluator=evaluator, repository=repository,
        )
        report = await service.run(EvaluationPlan(
            "tenant-a", "rag-release", 7, self.endpoint, concurrency_limit=2,
        ))
        self.assertEqual(report.dataset_version, 7)
        self.assertEqual(len(report.dataset_sha256), 64)
        self.assertEqual(report.dataset_sha256, dataset_fingerprint(self.dataset))
        self.assertEqual(report.endpoint_id, "prod-eu")
        self.assertEqual(report.model_version, "2026-11-01")
        self.assertEqual(report.concurrency_limit, 2)
        self.assertLessEqual(evaluator.maximum, 2)
        self.assertEqual(len(repository.saved), 1)
        self.assertEqual(report.metrics["answer_term_f1"], 1.0)

    def test_dataset_fingerprint_is_stable_and_detects_same_version_mutation(self):
        original = self.dataset
        same_content = EvaluationDataset(
            "rag-release", 7, tuple(original.cases),
        )
        changed_content = EvaluationDataset(
            "rag-release", 7, (
                EvaluationCase(
                    "case-a", "question changed", ("alpha",), ("forbidden",),
                    ("doc-a",), ("doc-a",),
                ),
                *original.cases[1:],
            ),
        )
        self.assertEqual(dataset_fingerprint(original), dataset_fingerprint(same_content))
        self.assertNotEqual(dataset_fingerprint(original), dataset_fingerprint(changed_content))

    async def test_failed_case_does_not_persist_partial_report(self):
        class Broken(FakeEvaluator):
            async def evaluate(self, case, *, tenant_id, endpoint):
                if case.case_id == "case-b":
                    raise RuntimeError("endpoint unavailable")
                return await super().evaluate(case, tenant_id=tenant_id, endpoint=endpoint)

        repository = FakeRepository()
        async def load(dataset_id, version):
            return self.dataset
        service = AgentRAGEvaluationService(
            dataset_loader=load, evaluator=Broken(), repository=repository,
        )
        with self.assertRaisesRegex(RuntimeError, "endpoint unavailable"):
            await service.run(EvaluationPlan("tenant-a", "rag-release", 7, self.endpoint))
        self.assertEqual(repository.saved, [])

    def test_rejects_invalid_concurrency_and_unpinned_endpoint(self):
        with self.assertRaises(ValueError):
            EvaluationPlan("tenant-a", "rag-release", 7, self.endpoint, concurrency_limit=33)
        with self.assertRaises(ValueError):
            ModelEndpoint("prod", "model", "file:///tmp/model", "v1")


if __name__ == "__main__":
    unittest.main()


class FakeReportCursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class FakeReportConnection:
    def __init__(self, database):
        self.database = database

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, query, params=()):
        sql = " ".join(query.split())
        if sql.startswith("INSERT INTO platform_agent_rag_eval_reports"):
            (run_id, tenant_id, dataset_id, version, fingerprint, endpoint_id,
             model_key, model_version, concurrency, report) = params
            row = {
                "run_id": run_id, "tenant_id": tenant_id, "dataset_id": dataset_id,
                "dataset_version": version, "dataset_sha256": fingerprint,
                "endpoint_id": endpoint_id, "model_key": model_key,
                "model_version": model_version, "concurrency_limit": concurrency,
                "report": json.loads(report),
                "created_at": datetime(2026, 11, 22, tzinfo=timezone.utc),
            }
            self.database[(tenant_id, run_id)] = row
            return FakeReportCursor()
        if "WHERE tenant_id = %s AND run_id = %s" in sql:
            tenant_id, run_id = params
            return FakeReportCursor(self.database.get((tenant_id, run_id)))
        if "WHERE tenant_id = %s AND dataset_id = %s" in sql:
            tenant_id, dataset_id, limit = params
            rows = [
                row for (owner, _), row in self.database.items()
                if owner == tenant_id and row["dataset_id"] == dataset_id
            ]
            return FakeReportCursor(rows=rows[:limit])
        if "WHERE tenant_id = %s ORDER BY created_at DESC" in sql:
            tenant_id, limit = params
            rows = [row for (owner, _), row in self.database.items() if owner == tenant_id]
            return FakeReportCursor(rows=rows[:limit])
        raise AssertionError(f"unhandled SQL: {sql}")


class FakeReportDatabase:
    def __init__(self):
        self.rows = {}

    async def connect(self):
        return FakeReportConnection(self.rows)


class AgentRAGEvaluationReportPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_round_trip_is_tenant_scoped_and_lists_dataset_history(self):
        database = FakeReportDatabase()
        repository = PostgresAgentRAGEvaluationRepository(database.connect)
        report = type("Report", (), {
            "run_id": "e9f2e6bd-cb4c-42db-94d8-6c0f991d5f71",
            "tenant_id": "tenant-a",
            "dataset_id": "rag-release",
            "dataset_version": 7,
            "dataset_sha256": "a" * 64,
            "endpoint_id": "endpoint-eu",
            "model_key": "answer-model",
            "model_version": "v7",
            "concurrency_limit": 2,
            "case_metrics": (),
            "metrics": {"answer_term_f1": 0.9},
        })()
        await repository.save_evaluation_report(report)

        loaded = await repository.get_report(
            tenant_id="tenant-a", run_id=report.run_id,
        )
        self.assertEqual(loaded["report"]["metrics"]["answer_term_f1"], 0.9)
        self.assertEqual(loaded["dataset_sha256"], "a" * 64)
        self.assertEqual(
            await repository.list_reports(tenant_id="tenant-a", dataset_id="rag-release"),
            [loaded],
        )
        self.assertEqual(
            await repository.list_reports(tenant_id="tenant-b"), [],
        )
        with self.assertRaises(KeyError):
            await repository.get_report(tenant_id="tenant-b", run_id=report.run_id)

    async def test_report_persistence_rejects_invalid_fingerprint_and_list_limit(self):
        database = FakeReportDatabase()
        repository = PostgresAgentRAGEvaluationRepository(database.connect)
        report = type("Report", (), {
            "run_id": "e9f2e6bd-cb4c-42db-94d8-6c0f991d5f71",
            "tenant_id": "tenant-a",
            "dataset_id": "rag-release",
            "dataset_version": 7,
            "dataset_sha256": "not-a-fingerprint",
            "endpoint_id": "endpoint-eu",
            "model_key": "answer-model",
            "model_version": "v7",
            "concurrency_limit": 2,
            "case_metrics": (),
            "metrics": {},
        })()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            await repository.save_evaluation_report(report)
        with self.assertRaisesRegex(ValueError, "limit"):
            await repository.list_reports(tenant_id="tenant-a", limit=0)
