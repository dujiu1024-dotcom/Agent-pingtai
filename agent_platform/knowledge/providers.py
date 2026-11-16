"""Production HTTP adapters for embedding, reranking, and OCR providers.

The adapters use host-owned HTTP clients and runtime-resolved credentials. They do
not provide mock fallbacks: provider/network/configuration errors fail closed.
"""
from __future__ import annotations

import math
import time
from dataclasses import replace
from typing import Any, Sequence
from urllib.parse import urlsplit

from agent_platform.knowledge.contracts import RetrievalHit


class KnowledgeProviderError(RuntimeError):
    """A configured external knowledge provider returned an unusable response."""


def _https_endpoint(value: str, *, suffix: str = "") -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("provider endpoint must be an absolute HTTPS URL")
    return value.rstrip("/") + suffix


def _vector(value: Any, *, dimensions: int) -> list[float]:
    if not isinstance(value, list) or len(value) != dimensions:
        raise KnowledgeProviderError(
            f"embedding dimension mismatch: expected {dimensions}"
        )
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise KnowledgeProviderError("embedding contains a non-numeric value")
        number = float(item)
        if not math.isfinite(number):
            raise KnowledgeProviderError("embedding contains a non-finite value")
        result.append(number)
    return result


class OpenAICompatibleEmbedder:
    """Real OpenAI-compatible /embeddings adapter (including compatible gateways).

    The configured database schema uses vector(1536) by default; deployments
    using another dimension must migrate that schema before changing dimensions.
    """

    def __init__(
        self,
        *,
        client: Any,
        base_url: str,
        api_key: str,
        model_id: str,
        dimensions: int = 1536,
    ) -> None:
        if not api_key.strip() or not model_id.strip():
            raise ValueError("embedding API key and model_id are required")
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions < 1:
            raise ValueError("embedding dimensions must be a positive integer")
        self.client = client
        self.endpoint = _https_endpoint(base_url, suffix="/embeddings")
        self.api_key = api_key
        self.model_id = model_id
        self.dimensions = dimensions

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("embedding inputs must be non-empty strings")
        response = await self.client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self.model_id, "input": list(texts)},
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise KnowledgeProviderError("embedding provider returned an invalid item count")
        indexed: dict[int, list[float]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise KnowledgeProviderError("embedding provider returned an invalid item")
            index = row.get("index")
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(texts):
                raise KnowledgeProviderError("embedding provider returned an invalid index")
            if index in indexed:
                raise KnowledgeProviderError("embedding provider returned a duplicate index")
            indexed[index] = _vector(row.get("embedding"), dimensions=self.dimensions)
        if set(indexed) != set(range(len(texts))):
            raise KnowledgeProviderError("embedding provider response is incomplete")
        return [indexed[index] for index in range(len(texts))]

    async def embed_query(self, text: str) -> Sequence[float]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("embedding query must not be empty")
        return (await self.embed_documents([text]))[0]


class CohereReranker:
    """Cohere v2 rerank adapter; preserves source/citation metadata on each hit."""

    def __init__(
        self,
        *,
        client: Any,
        endpoint: str = "https://api.cohere.com/v2/rerank",
        api_key: str,
        model_id: str,
    ) -> None:
        if not api_key.strip() or not model_id.strip():
            raise ValueError("rerank API key and model_id are required")
        self.client = client
        self.endpoint = _https_endpoint(endpoint)
        self.api_key = api_key
        self.model_id = model_id

    async def rerank(
        self, query: str, hits: Sequence[RetrievalHit]
    ) -> Sequence[RetrievalHit]:
        if not query.strip():
            raise ValueError("rerank query must not be empty")
        if not hits:
            return []
        response = await self.client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_id,
                "query": query,
                "documents": [hit.text for hit in hits],
                "top_n": len(hits),
                "return_documents": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) > len(hits):
            raise KnowledgeProviderError("rerank provider returned an invalid result list")
        ranked: list[tuple[float, int]] = []
        seen: set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise KnowledgeProviderError("rerank provider returned an invalid result")
            index = row.get("index")
            score = row.get("relevance_score")
            if (
                isinstance(index, bool) or not isinstance(index, int)
                or not 0 <= index < len(hits) or index in seen
                or isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                raise KnowledgeProviderError("rerank provider returned invalid ranking data")
            seen.add(index)
            ranked.append((float(score), index))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [replace(hits[index], score=score) for score, index in ranked]


class AzureDocumentIntelligenceOCRProvider:
    """Synchronous Azure Document Intelligence Read/Layout OCR adapter.

    The parser contract is synchronous, so this adapter deliberately accepts a
    host-owned synchronous HTTP client. The caller must resolve the API key from
    the secret manager at composition time and close the client at shutdown.
    """

    def __init__(
        self,
        *,
        client: Any,
        endpoint: str,
        api_key: str,
        model_id: str = "prebuilt-read",
        api_version: str = "2024-11-30",
        timeout_seconds: float = 30.0,
        max_file_bytes: int = 25 * 1024 * 1024,
        max_polls: int = 60,
    ) -> None:
        if not api_key.strip() or not model_id.strip():
            raise ValueError("OCR API key and model_id are required")
        if timeout_seconds <= 0 or max_file_bytes < 1 or max_polls < 1:
            raise ValueError("invalid OCR timeout, size, or polling limits")
        self.client = client
        self.endpoint = _https_endpoint(endpoint)
        self.api_key = api_key
        self.model_id = model_id
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds
        self.max_file_bytes = max_file_bytes
        self.max_polls = max_polls

    @staticmethod
    def _page_text(analysis: dict[str, Any], content: str) -> str:
        pages = analysis.get("pages")
        if not isinstance(pages, list):
            return content
        parts: list[str] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_number = page.get("pageNumber")
            spans = page.get("spans")
            if (
                isinstance(page_number, bool) or not isinstance(page_number, int)
                or page_number < 1 or not isinstance(spans, list)
            ):
                continue
            segments: list[str] = []
            for span in spans:
                if not isinstance(span, dict):
                    continue
                offset, length = span.get("offset"), span.get("length")
                if (
                    isinstance(offset, bool) or not isinstance(offset, int)
                    or isinstance(length, bool) or not isinstance(length, int)
                    or offset < 0 or length < 0 or offset + length > len(content)
                ):
                    continue
                segments.append(content[offset:offset + length])
            page_content = "".join(segments)
            if page_content.strip():
                parts.append(f"\n[page:{page_number}]\n{page_content}")
        return "\n".join(parts) if parts else content

    def extract_text(self, content: bytes, media_type: str) -> str:
        if not content or len(content) > self.max_file_bytes:
            raise ValueError("OCR content is empty or exceeds configured size limit")
        if media_type not in {
            "application/pdf", "image/jpeg", "image/png", "image/tiff",
        }:
            raise ValueError("unsupported OCR media type")
        url = (
            f"{self.endpoint}/documentintelligence/documentModels/"
            f"{self.model_id}:analyze?api-version={self.api_version}"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Type": "application/octet-stream",
        }
        response = self.client.post(
            url, headers=headers, content=bytes(content), timeout=self.timeout_seconds,
        )
        if response.status_code != 202:
            response.raise_for_status()
            raise KnowledgeProviderError("OCR provider did not accept the analysis request")
        operation_url = response.headers.get("operation-location")
        parsed = urlsplit(operation_url or "")
        base = urlsplit(self.endpoint)
        if (
            parsed.scheme != "https" or parsed.hostname != base.hostname
            or parsed.port != base.port or not parsed.path
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment
        ):
            raise KnowledgeProviderError("OCR provider returned an unsafe operation URL")
        for _ in range(self.max_polls):
            result = self.client.get(
                operation_url, headers={"Ocp-Apim-Subscription-Key": self.api_key},
                timeout=self.timeout_seconds,
            )
            result.raise_for_status()
            payload = result.json()
            status = payload.get("status") if isinstance(payload, dict) else None
            if status == "succeeded":
                analysis = payload.get("analyzeResult", {})
                text = analysis.get("content") if isinstance(analysis, dict) else None
                if not isinstance(text, str) or not text.strip():
                    raise KnowledgeProviderError("OCR provider returned no extracted text")
                return self._page_text(analysis, text)
            if status in {"failed", "canceled"}:
                raise KnowledgeProviderError("OCR document analysis failed")
            if status not in {"notStarted", "running"}:
                raise KnowledgeProviderError("OCR provider returned an unknown operation status")
            retry_after = result.headers.get("retry-after", "1")
            try:
                delay = min(5.0, max(0.0, float(retry_after)))
            except (TypeError, ValueError):
                delay = 1.0
            time.sleep(delay)
        raise KnowledgeProviderError("OCR document analysis exceeded polling limit")
