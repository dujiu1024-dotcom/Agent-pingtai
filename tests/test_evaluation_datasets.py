import unittest

from agent_platform.observability.evaluation_datasets import (
    EvaluationDatasetCase,
    InMemoryEvaluationDatasetRepository,
    export_dataset,
    parse_dataset_import,
)


class EvaluationDatasetImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_jsonl_import_versions_and_tenant_isolation(self):
        repo = InMemoryEvaluationDatasetRepository()
        cases = parse_dataset_import(
            '{"case_id":"c1","input":"question","expected_output":"answer",'
            '"rubric":{"type":"exact","weight":1},"source_ref":"doc:v1"}',
            format="jsonl",
        )
        first = await repo.create_version(
            tenant_id="tenant-a", dataset_id="support", cases=cases, created_by="user-a",
        )
        second = await repo.create_version(
            tenant_id="tenant-a", dataset_id="support", cases=cases, created_by="user-a",
        )
        self.assertEqual((first.version, second.version), (1, 2))
        loaded = await repo.get_version(
            tenant_id="tenant-a", dataset_id="support", version=1,
        )
        self.assertEqual(loaded.cases[0].source_ref, "doc:v1")
        with self.assertRaises(KeyError):
            await repo.get_version(tenant_id="tenant-b", dataset_id="support", version=1)
        self.assertEqual(len(await repo.list_versions(
            tenant_id="tenant-b", dataset_id="support",
        )), 0)

    async def test_csv_import_and_jsonl_csv_round_trip(self):
        csv_content = (
            "case_id,input,expected_output,rubric,source_ref\n"
            'c1,"hello, world",greeting,"{""type"":""contains"",""weight"":1}",source-1\n'
        )
        cases = parse_dataset_import(csv_content, format="csv")
        self.assertEqual(cases[0].input, "hello, world")
        self.assertEqual(cases[0].rubric["type"], "contains")
        repo = InMemoryEvaluationDatasetRepository()
        record = await repo.create_version(
            tenant_id="tenant-a", dataset_id="csv-set", cases=cases, created_by="user-a",
        )
        jsonl = export_dataset(record, format="jsonl")
        csv_export = export_dataset(record, format="csv")
        self.assertEqual(parse_dataset_import(jsonl, format="jsonl"), cases)
        self.assertEqual(parse_dataset_import(csv_export, format="csv"), cases)

    async def test_rejects_invalid_shapes_and_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "missing or unknown"):
            parse_dataset_import('{"input":"q","expected_output":"a"}', format="jsonl")
        with self.assertRaisesRegex(ValueError, "rubric"):
            parse_dataset_import(
                '{"input":"q","expected_output":"a","rubric":"not-json"}',
                format="jsonl",
            )
        duplicate = (
            '{"case_id":"same","input":"q1","expected_output":"a","rubric":{"x":1}}\n'
            '{"case_id":"same","input":"q2","expected_output":"a","rubric":{"x":1}}'
        )
        with self.assertRaisesRegex(ValueError, "unique"):
            parse_dataset_import(duplicate, format="jsonl")
        with self.assertRaisesRegex(ValueError, "format"):
            parse_dataset_import("input,expected_output,rubric\nq,a,{}", format="yaml")

    async def test_case_validation_and_import_limits(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            EvaluationDatasetCase("c1", "", "answer", {"type": "exact"})
        with self.assertRaisesRegex(ValueError, "rubric"):
            EvaluationDatasetCase("c1", "question", "answer", {})
        with self.assertRaisesRegex(ValueError, "2 MB"):
            parse_dataset_import(" " + ("x" * 2_000_001), format="jsonl")


if __name__ == "__main__":
    unittest.main()
