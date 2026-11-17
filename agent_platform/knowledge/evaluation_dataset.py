"""Validated, fingerprinted RAG evaluation datasets."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_platform.knowledge.evaluation import evaluation_fingerprint


@dataclass(frozen=True)
class RAGEvaluationDocument:
    key: str
    title: str
    source_uri: str
    content: str


@dataclass(frozen=True)
class RAGEvaluationCase:
    case_id: str
    query: str
    expected_document_keys: tuple[str, ...]
    expected_answer_terms: tuple[str, ...]
    answer: str
    expect_abstain: bool


@dataclass(frozen=True)
class RAGEvaluationDataset:
    dataset_id: str
    version: int
    description: str
    documents: tuple[RAGEvaluationDocument, ...]
    cases: tuple[RAGEvaluationCase, ...]
    fingerprint: str


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings")
    items = tuple(_required_string(item, f"{field}[]") for item in value)
    if len(items) != len(set(items)):
        raise ValueError(f"{field} must not contain duplicates")
    return items


def parse_evaluation_dataset(payload: Mapping[str, Any]) -> RAGEvaluationDataset:
    """Validate a JSON-decoded dataset and compute its canonical SHA-256."""
    if not isinstance(payload, Mapping):
        raise ValueError("evaluation dataset must be a JSON object")
    required = {"dataset_id", "version", "description", "documents", "cases"}
    if set(payload) != required:
        missing = sorted(required - set(payload))
        unknown = sorted(set(payload) - required)
        raise ValueError(f"invalid dataset fields (missing={missing}, unknown={unknown})")

    dataset_id = _required_string(payload["dataset_id"], "dataset_id")
    description = _required_string(payload["description"], "description")
    version = payload["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("version must be a positive integer")

    raw_documents, raw_cases = payload["documents"], payload["cases"]
    if not isinstance(raw_documents, list) or not isinstance(raw_cases, list):
        raise ValueError("documents and cases must be lists")
    if not raw_cases:
        raise ValueError("evaluation dataset must contain at least one case")

    documents: list[RAGEvaluationDocument] = []
    document_keys: set[str] = set()
    document_fields = {"key", "title", "source_uri", "content"}
    for index, item in enumerate(raw_documents):
        if not isinstance(item, Mapping) or set(item) != document_fields:
            raise ValueError(f"documents[{index}] has an invalid shape")
        document = RAGEvaluationDocument(
            key=_required_string(item["key"], f"documents[{index}].key"),
            title=_required_string(item["title"], f"documents[{index}].title"),
            source_uri=_required_string(item["source_uri"], f"documents[{index}].source_uri"),
            content=_required_string(item["content"], f"documents[{index}].content"),
        )
        if document.key in document_keys:
            raise ValueError(f"duplicate document key: {document.key}")
        document_keys.add(document.key)
        documents.append(document)

    cases: list[RAGEvaluationCase] = []
    case_ids: set[str] = set()
    case_fields = {
        "case_id", "query", "expected_document_keys", "expected_answer_terms",
        "answer", "expect_abstain",
    }
    for index, item in enumerate(raw_cases):
        if not isinstance(item, Mapping) or set(item) != case_fields:
            raise ValueError(f"cases[{index}] has an invalid shape")
        case_id = _required_string(item["case_id"], f"cases[{index}].case_id")
        if case_id in case_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        case_ids.add(case_id)
        expected_keys = _string_list(
            item["expected_document_keys"], f"cases[{index}].expected_document_keys",
        )
        unknown_keys = sorted(set(expected_keys) - document_keys)
        if unknown_keys:
            raise ValueError(f"cases[{index}] references unknown documents: {unknown_keys}")
        expected_terms = _string_list(
            item["expected_answer_terms"], f"cases[{index}].expected_answer_terms",
        )
        expect_abstain = item["expect_abstain"]
        if not isinstance(expect_abstain, bool):
            raise ValueError(f"cases[{index}].expect_abstain must be a boolean")
        answer = item["answer"]
        if not isinstance(answer, str):
            raise ValueError(f"cases[{index}].answer must be a string")
        cases.append(RAGEvaluationCase(
            case_id=case_id,
            query=_required_string(item["query"], f"cases[{index}].query"),
            expected_document_keys=expected_keys,
            expected_answer_terms=expected_terms,
            answer=answer,
            expect_abstain=expect_abstain,
        ))

    try:
        fingerprint = evaluation_fingerprint(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("evaluation dataset must contain finite JSON data") from exc
    return RAGEvaluationDataset(
        dataset_id=dataset_id, version=version, description=description,
        documents=tuple(documents), cases=tuple(cases), fingerprint=fingerprint,
    )


def load_evaluation_dataset(path: str | Path) -> RAGEvaluationDataset:
    """Load and validate a UTF-8 JSON evaluation dataset from disk."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("unable to read evaluation dataset JSON") from exc
    return parse_evaluation_dataset(payload)
