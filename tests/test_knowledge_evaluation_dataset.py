from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_platform.knowledge.evaluation_dataset import (
    load_evaluation_dataset,
    parse_evaluation_dataset,
)

FIXTURE = Path(__file__).parent / "fixtures" / "knowledge_rag_eval_v1.json"


class RAGEvaluationDatasetTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_loads_versioned_fixture_and_returns_stable_fingerprint(self):
        dataset = load_evaluation_dataset(FIXTURE)
        self.assertEqual(dataset.dataset_id, "platform-knowledge-smoke-v1")
        self.assertEqual(dataset.version, 1)
        self.assertEqual((len(dataset.documents), len(dataset.cases)), (2, 3))
        self.assertEqual(len(dataset.fingerprint), 64)
        self.assertEqual(dataset.fingerprint, parse_evaluation_dataset(self.payload).fingerprint)

    def test_fingerprint_is_independent_of_object_key_order(self):
        reordered = {key: self.payload[key] for key in reversed(tuple(self.payload))}
        self.assertEqual(
            parse_evaluation_dataset(self.payload).fingerprint,
            parse_evaluation_dataset(reordered).fingerprint,
        )

    def test_fingerprint_changes_when_expected_content_changes(self):
        changed = copy.deepcopy(self.payload)
        changed["cases"][0]["expected_answer_terms"].append("rotation")
        self.assertNotEqual(
            parse_evaluation_dataset(self.payload).fingerprint,
            parse_evaluation_dataset(changed).fingerprint,
        )

    def test_rejects_unknown_or_missing_root_fields(self):
        for mutate in (
            lambda data: data.update({"unexpected": True}),
            lambda data: data.pop("description"),
        ):
            changed = copy.deepcopy(self.payload)
            mutate(changed)
            with self.assertRaises(ValueError):
                parse_evaluation_dataset(changed)

    def test_rejects_invalid_versions_including_bool(self):
        for version in (True, False, 0, -1, 1.5, "1"):
            changed = copy.deepcopy(self.payload)
            changed["version"] = version
            with self.subTest(version=version), self.assertRaises(ValueError):
                parse_evaluation_dataset(changed)

    def test_rejects_duplicate_document_keys_and_case_ids(self):
        duplicate_document = copy.deepcopy(self.payload)
        duplicate_document["documents"].append(duplicate_document["documents"][0])
        duplicate_case = copy.deepcopy(self.payload)
        duplicate_case["cases"].append(duplicate_case["cases"][0])
        for changed in (duplicate_document, duplicate_case):
            with self.assertRaises(ValueError):
                parse_evaluation_dataset(changed)

    def test_rejects_unknown_document_references_and_duplicate_expected_keys(self):
        unknown = copy.deepcopy(self.payload)
        unknown["cases"][0]["expected_document_keys"] = ["missing"]
        duplicate = copy.deepcopy(self.payload)
        duplicate["cases"][0]["expected_document_keys"] *= 2
        for changed in (unknown, duplicate):
            with self.assertRaises(ValueError):
                parse_evaluation_dataset(changed)

    def test_rejects_invalid_case_types_and_empty_queries(self):
        for field, value in (
            ("query", "  "), ("expect_abstain", 1),
            ("expected_answer_terms", "not-a-list"), ("answer", None),
        ):
            changed = copy.deepcopy(self.payload)
            changed["cases"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_evaluation_dataset(changed)

    def test_rejects_empty_case_sets_and_non_object_payloads(self):
        changed = copy.deepcopy(self.payload)
        changed["cases"] = []
        with self.assertRaises(ValueError):
            parse_evaluation_dataset(changed)
        with self.assertRaises(ValueError):
            parse_evaluation_dataset([])

    def test_file_loader_rejects_invalid_json_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text("{", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_evaluation_dataset(invalid)
            with self.assertRaises(ValueError):
                load_evaluation_dataset(Path(directory) / "missing.json")


if __name__ == "__main__":
    unittest.main()
