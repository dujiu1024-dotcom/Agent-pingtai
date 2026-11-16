from __future__ import annotations

import asyncio
import unittest

from agent_platform.observability.evaluation import EvalCase, EvalDataset, EvalGatePolicy
from agent_platform.observability.evaluation_runner import (
    EvalOutput,
    EvaluationRunService,
)


class FakeRepository:
    def __init__(self, dataset):
        self.dataset = dataset
        self.saved = []

    async def load_dataset(self, dataset_id, version):
        if (dataset_id, version) != (self.dataset.dataset_id, self.dataset.version):
            raise KeyError("dataset version not found")
        return self.dataset

    async def save_report(self, report, *, model_key, gate_passed, gate_failures, tenant_id=""):
        self.saved.append((report, model_key, gate_passed, gate_failures, tenant_id))
        return "eval-run-1"


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs
        self.active = 0
        self.max_active = 0

    async def run_case(self, case, *, tenant_id, model_key):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        value = self.outputs[case.case_id]
        if isinstance(value, Exception):
            raise value
        return value


class EvaluationRunServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dataset = EvalDataset(
            "release-smoke", 3,
            (
                EvalCase("case-a", expected_terms=("ready",), expected_document_ids=("doc-a",)),
                EvalCase("case-b", expected_terms=("safe",), expected_document_ids=("doc-b",)),
            ),
        )

    async def test_runs_pinned_dataset_and_persists_passed_gate(self):
        repository = FakeRepository(self.dataset)
        runner = FakeRunner({
            "case-a": EvalOutput("service ready", ("doc-a", "doc-x")),
            "case-b": EvalOutput("safe output", ("doc-b",)),
        })
        service = EvaluationRunService(
            repository=repository,
            runner=runner,
            policy=EvalGatePolicy(1.0, 1.0, 1.0),
            concurrency=1,
            k=3,
        )
        result = await service.run(
            tenant_id="tenant-a", dataset_id="release-smoke",
            dataset_version=3, model_key="model-v2",
        )
        self.assertTrue(result.gate_passed)
        self.assertEqual(result.eval_run_id, "eval-run-1")
        self.assertEqual(result.report.dataset_version, 3)
        self.assertEqual(len(result.report.results), 2)
        self.assertEqual(repository.saved[0][1:], (
            "model-v2", True, (), "tenant-a",
        ))
        self.assertEqual(runner.max_active, 1)

    async def test_persists_failed_gate_evidence_without_marking_passed(self):
        repository = FakeRepository(self.dataset)
        runner = FakeRunner({
            "case-a": EvalOutput("not ready", ("wrong-doc",)),
            "case-b": EvalOutput("safe output", ("doc-b",)),
        })
        service = EvaluationRunService(
            repository=repository,
            runner=runner,
            policy=EvalGatePolicy(1.0, 1.0, 1.0),
        )
        result = await service.run(
            tenant_id="tenant-a", dataset_id="release-smoke",
            dataset_version=3, model_key="model-v2",
        )
        self.assertFalse(result.gate_passed)
        self.assertTrue(result.gate_failures)
        self.assertFalse(repository.saved[0][2])

    async def test_runner_failure_does_not_persist_partial_report(self):
        repository = FakeRepository(self.dataset)
        runner = FakeRunner({
            "case-a": EvalOutput("ready", ("doc-a",)),
            "case-b": RuntimeError("model endpoint failed"),
        })
        service = EvaluationRunService(
            repository=repository, runner=runner,
            policy=EvalGatePolicy(),
        )
        with self.assertRaisesRegex(RuntimeError, "model endpoint failed"):
            await service.run(
                tenant_id="tenant-a", dataset_id="release-smoke",
                dataset_version=3, model_key="model-v2",
            )
        self.assertEqual(repository.saved, [])

    async def test_rejects_invalid_run_identity_and_dataset_version(self):
        repository = FakeRepository(self.dataset)
        service = EvaluationRunService(
            repository=repository, runner=FakeRunner({}),
            policy=EvalGatePolicy(),
        )
        with self.assertRaisesRegex(ValueError, "tenant_id"):
            await service.run(
                tenant_id="", dataset_id="release-smoke",
                dataset_version=3, model_key="model-v2",
            )
        with self.assertRaisesRegex(ValueError, "positive integer"):
            await service.run(
                tenant_id="tenant-a", dataset_id="release-smoke",
                dataset_version=True, model_key="model-v2",
            )
        self.assertEqual(repository.saved, [])


if __name__ == "__main__":
    unittest.main()
