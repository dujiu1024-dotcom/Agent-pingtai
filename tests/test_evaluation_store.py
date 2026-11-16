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
            run_id, tenant_id, dataset_id, version, model_key, report, passed = params
            self.db.runs.append({
                "eval_run_id": run_id, "tenant_id": tenant_id, "dataset_id": dataset_id,
                "dataset_version": version, "model_key": model_key,
                "report": json.loads(report), "gate_passed": passed,
                "created_at": datetime(2026,11,16,tzinfo=timezone.utc),
            })
            return FakeCursor()
        if sql.startswith("INSERT INTO platform_eval_human_scores"):
            score_id, tenant_id, case_id, reviewer_id, score, rationale, checked_case, checked_run, checked_tenant = params
            run = next((item for item in self.db.runs if item["eval_run_id"] == checked_run and item["tenant_id"] == checked_tenant), None)
            case = self.db.cases.get((run["dataset_id"], run["dataset_version"], checked_case)) if run else None
            if case is None:
                return FakeCursor()
            key = (checked_run, case_id, reviewer_id)
            self.db.scores[key] = {
                "score_id": score_id, "tenant_id": tenant_id, "eval_run_id": checked_run,
                "case_id": case_id, "reviewer_id": reviewer_id, "score": score,
                "rationale": rationale,
            }
            return FakeCursor({"score_id": score_id})
        if sql.startswith("SELECT score_id,eval_run_id"):
            tenant_id, run_id = params
            rows = [row for row in self.db.scores.values()
                    if row["tenant_id"] == tenant_id and row["eval_run_id"] == run_id]
            return FakeCursor(rows=rows)
        if sql.startswith("SELECT eval_run_id,dataset_id"):
            dataset_id, limit = params
            rows = [row for row in self.db.runs if row["dataset_id"] == dataset_id]
            return FakeCursor(rows=list(reversed(rows))[:limit])
        raise AssertionError(f"unhandled SQL: {sql}")


class FakeEvalDb:
    def __init__(self):
        self.datasets, self.cases, self.runs, self.scores = {}, {}, [], {}
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


    async def test_human_scores_are_tenant_scoped_validated_and_redacted(self):
        db = FakeEvalDb()
        repository = PostgresEvaluationRepository(db.connect)
        dataset = EvalDataset("human-review", 1, (EvalCase("case-1", query="question"),))
        await repository.save_dataset(dataset, created_by="release-bot")
        report = build_report(dataset, (
            evaluate_case(dataset.cases[0], output_text="answer"),
        ))
        run_id = await repository.save_report(
            report, model_key="model@1", gate_passed=True, gate_failures=(),
            tenant_id="tenant-a",
        )
        score_id = await repository.save_human_score(
            tenant_id="tenant-a", eval_run_id=run_id, case_id="case-1",
            reviewer_id="reviewer-1", score=0.75, rationale="api_key=secret-value",
        )
        rows = await repository.list_human_scores(tenant_id="tenant-a", eval_run_id=run_id)
        self.assertEqual(rows[0]["score_id"], score_id)
        self.assertEqual(rows[0]["score"], 0.75)
        self.assertNotIn("secret-value", rows[0]["rationale"])
        self.assertEqual(await repository.list_human_scores(tenant_id="tenant-b", eval_run_id=run_id), [])
        with self.assertRaises(ValueError):
            await repository.save_human_score(
                tenant_id="tenant-a", eval_run_id=run_id, case_id="case-1",
                reviewer_id="reviewer-2", score=float("nan"),
            )
        with self.assertRaises(KeyError):
            await repository.save_human_score(
                tenant_id="tenant-b", eval_run_id=run_id, case_id="case-1",
                reviewer_id="reviewer-3", score=0.5,
            )


if __name__ == "__main__":
    unittest.main()
