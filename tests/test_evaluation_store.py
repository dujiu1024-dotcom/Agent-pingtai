from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from agent_platform.observability.evaluation import (
    EvalCase,
    EvalDataset,
    EvalGatePolicy,
    build_report,
    evaluate_case,
    gate_report,
)
from agent_platform.observability.evaluation_store import PostgresEvaluationRepository


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows or []
    async def fetchone(self):
        return self.row
    async def fetchall(self):
        return self.rows


class FakeEvalConnection:
    def __init__(self, db):
        self.db = db
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    def transaction(self):
        return self
    async def execute(self, query, params=()):
        sql = " ".join(query.split())
        if "pg_advisory_xact_lock" in sql:
            return FakeCursor()
        if sql.startswith("INSERT INTO platform_eval_datasets"):
            dataset_id, version, created_by, case_count = params
            self.db.datasets[(dataset_id,version)] = {
                "dataset_id": dataset_id, "version": version,
                "created_by": created_by, "case_count": case_count,
            }
            return FakeCursor()
        if sql.startswith("INSERT INTO platform_eval_cases"):
            dataset_id, version, case_id, query, expected, forbidden, docs = params
            self.db.cases[(dataset_id,version,case_id)] = {
                "case_id": case_id, "query": query, "expected_terms": expected,
                "forbidden_terms": forbidden, "expected_document_ids": docs,
            }
            return FakeCursor()
        if sql.startswith("SELECT dataset_id,version FROM platform_eval_datasets"):
            return FakeCursor(self.db.datasets.get(tuple(params)))
        if sql.startswith("SELECT case_id,query,expected_terms"):
            dataset_id, version = params
            rows = [v for (d,vn,_),v in self.db.cases.items() if d == dataset_id and vn == version]
            return FakeCursor(rows=sorted(rows,key=lambda row: row["case_id"]))
        if sql.startswith("INSERT INTO platform_eval_runs"):
            run_id, dataset_id, version, model_key, report, passed = params
            self.db.runs.append({
                "eval_run_id": run_id, "dataset_id": dataset_id,
                "dataset_version": version, "model_key": model_key,
                "report": json.loads(report), "gate_passed": passed,
                "created_at": datetime(2026,11,16,tzinfo=timezone.utc),
            })
            return FakeCursor()
        if sql.startswith("SELECT eval_run_id,dataset_id"):
            dataset_id, limit = params
            rows = [row for row in self.db.runs if row["dataset_id"] == dataset_id]
            return FakeCursor(rows=list(reversed(rows))[:limit])
        raise AssertionError(f"unhandled SQL: {sql}")


class FakeEvalDb:
    def __init__(self):
        self.datasets, self.cases, self.runs = {}, {}, []
    async def connect(self):
        return FakeEvalConnection(self)


class PersistentEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dataset_round_trip_and_gate_report_history(self):
        db = FakeEvalDb()
        repository = PostgresEvaluationRepository(db.connect)
        dataset = EvalDataset(
            "rag-smoke", 1, (
                EvalCase("case-1", query="deployment", expected_terms=("approval",),
                         expected_document_ids=("doc-1",)),
                EvalCase("case-2", query="rollback", forbidden_terms=("secret",)),
            ),
        )
        await repository.save_dataset(dataset, created_by="release-bot")
        loaded = await repository.load_dataset("rag-smoke", 1)
        self.assertEqual(loaded, dataset)
        results = (
            evaluate_case(loaded.cases[0], output_text="approval required",
                          retrieved_document_ids=("doc-1",)),
            evaluate_case(loaded.cases[1], output_text="rollback documented"),
        )
        report = build_report(loaded, results)
        passed, failures = gate_report(report, EvalGatePolicy(
            min_pass_rate=1, min_average_score=1, min_average_recall_at_k=1,
        ))
        self.assertTrue(passed)
        self.assertEqual(failures, ())
        await repository.save_report(
            report, model_key="general@1", gate_passed=passed, gate_failures=failures,
        )
        history = await repository.latest_reports("rag-smoke")
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["gate_passed"])
        self.assertEqual(history[0]["report"]["dataset_version"], 1)

    async def test_unknown_dataset_version_fails_closed(self):
        repository = PostgresEvaluationRepository(FakeEvalDb().connect)
        with self.assertRaises(KeyError):
            await repository.load_dataset("missing", 1)


if __name__ == "__main__":
    unittest.main()
