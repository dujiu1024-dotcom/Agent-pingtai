from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from agent_platform.knowledge.contracts import RetrievalHit
from agent_platform.knowledge.ingestion import OCRParserAdapter, ParserRegistry
from agent_platform.knowledge.providers import (
    AzureDocumentIntelligenceOCRProvider,
    CohereReranker,
    KnowledgeProviderError,
    OpenAICompatibleEmbedder,
)


class AsyncResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class AsyncClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return AsyncResponse(self.payload)


class SyncResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self.payload = payload or {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP request failed")

    def json(self):
        return self.payload


class SyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.pop(0)


class KnowledgeProviderAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_compatible_embedder_orders_and_validates_vectors(self):
        client = AsyncClient({
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ],
        })
        embedder = OpenAICompatibleEmbedder(
            client=client, base_url="https://embedding.example/v1",
            api_key="runtime-secret", model_id="embedding-prod", dimensions=2,
        )
        vectors = await embedder.embed_documents(["first", "second"])
        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(client.calls[0][0], "https://embedding.example/v1/embeddings")
        self.assertEqual(
            client.calls[0][1]["headers"]["Authorization"],
            "Bearer runtime-secret",
        )
        self.assertFalse(client.calls[0][1]["follow_redirects"])

    async def test_embedding_rejects_dimension_mismatch(self):
        client = AsyncClient({
            "data": [{"index": 0, "embedding": [1.0]}],
        })
        embedder = OpenAICompatibleEmbedder(
            client=client, base_url="https://embedding.example/v1",
            api_key="secret", model_id="model", dimensions=2,
        )
        with self.assertRaises(KnowledgeProviderError):
            await embedder.embed_documents(["document"])

    async def test_cohere_reranker_preserves_hit_identity_and_citation_metadata(self):
        hits = [
            RetrievalHit(
                document_id="doc-a", document_version=2, chunk_id="chunk-a",
                text="alpha", source_uri="file://a", title="A", score=0.1,
                page_number=3, start_offset=10, end_offset=15,
                citation_id="citation-a", retrieval_trace={"strategy": "hybrid"},
            ),
            RetrievalHit(
                document_id="doc-b", document_version=1, chunk_id="chunk-b",
                text="beta", source_uri="file://b", title="B", score=0.9,
                citation_id="citation-b",
            ),
        ]
        client = AsyncClient({
            "results": [
                {"index": 1, "relevance_score": 0.99},
                {"index": 0, "relevance_score": 0.12},
            ],
        })
        reranker = CohereReranker(
            client=client, api_key="runtime-secret", model_id="rerank-v3.5",
        )
        ranked = await reranker.rerank("query", hits)
        self.assertEqual([hit.document_id for hit in ranked], ["doc-b", "doc-a"])
        self.assertEqual(ranked[1].citation_id, "citation-a")
        self.assertEqual(ranked[1].page_number, 3)
        self.assertEqual(ranked[1].retrieval_trace, {"strategy": "hybrid"})
        self.assertEqual(ranked[0].score, 0.99)
        self.assertFalse(client.calls[0][1]["follow_redirects"])

    def test_azure_document_intelligence_extracts_text_from_real_api_contract(self):
        operation = "https://resource.example/documentintelligence/operations/abc"
        client = SyncClient([
            SyncResponse(202, headers={"operation-location": operation}),
            SyncResponse(200, {"status": "running"}, {"retry-after": "0"}),
            SyncResponse(200, {
                "status": "succeeded",
                "analyzeResult": {
                    "content": "OCR extracted source text",
                    "pages": [{"pageNumber": 1, "spans": [{"offset": 0, "length": 25}]}],
                },
            }),
        ])
        provider = AzureDocumentIntelligenceOCRProvider(
            client=client, endpoint="https://resource.example",
            api_key="runtime-secret", max_polls=3,
        )
        self.assertEqual(
            provider.extract_text(b"%PDF-content", "application/pdf"),
            "\n[page:1]\nOCR extracted source text",
        )
        self.assertEqual(client.calls[0][0], "POST")
        self.assertFalse(client.calls[0][2]["follow_redirects"])
        self.assertFalse(client.calls[1][2]["follow_redirects"])
        self.assertEqual(
            client.calls[0][2]["headers"]["Ocp-Apim-Subscription-Key"],
            "runtime-secret",
        )
        self.assertEqual([call[0] for call in client.calls], ["POST", "GET", "GET"])

    def test_empty_text_pdf_falls_through_to_registered_ocr_adapter(self):
        class EmptyPage:
            def extract_text(self):
                return ""

        class FakePdfReader:
            def __init__(self, stream):
                self.pages = [EmptyPage()]

        fake_pdf = types.ModuleType("pypdf")
        fake_pdf.PdfReader = FakePdfReader

        class OCRProvider:
            def extract_text(self, content, media_type):
                self.assert_content = content
                self.assert_media_type = media_type
                return "scanned page OCR text"

        ocr = OCRProvider()
        with patch.dict(sys.modules, {"pypdf": fake_pdf}):
            registry = ParserRegistry(parsers=(OCRParserAdapter(ocr),))
            self.assertEqual(
                registry.parse(b"%PDF-scanned", "application/pdf"),
                "scanned page OCR text",
            )
        self.assertEqual(ocr.assert_content, b"%PDF-scanned")
        self.assertEqual(ocr.assert_media_type, "application/pdf")

    def test_ocr_parser_rejects_invalid_limits_and_media_type_contracts(self):
        class OCRProvider:
            def extract_text(self, content, media_type):
                return "text"

        for invalid_limit in (0, -1, True, 1.5):
            with self.subTest(limit=invalid_limit):
                with self.assertRaises(ValueError):
                    OCRParserAdapter(OCRProvider(), max_output_chars=invalid_limit)
        for invalid_types in ((), ("",), ("application/pdf", "application/pdf"),
                              (" application/pdf",), ("application/pdf; charset=utf-8",)):
            with self.subTest(media_types=invalid_types):
                with self.assertRaises(ValueError):
                    OCRParserAdapter(OCRProvider(), media_types=invalid_types)

    def test_ocr_parser_enforces_input_and_output_bounds(self):
        class OCRProvider:
            def __init__(self, result):
                self.result = result
                self.calls = 0

            def extract_text(self, content, media_type):
                self.calls += 1
                return self.result

        provider = OCRProvider("1234")
        parser = OCRParserAdapter(provider, max_output_chars=4)
        self.assertEqual(parser.parse(b"image", "image/png"), "1234")
        with self.assertRaises(ValueError):
            parser.parse(b"", "image/png")
        with self.assertRaises(ValueError):
            parser.parse("not-bytes", "image/png")
        self.assertEqual(provider.calls, 1)

        oversized = OCRParserAdapter(OCRProvider("12345"), max_output_chars=4)
        with self.assertRaisesRegex(ValueError, "character limit"):
            oversized.parse(b"image", "image/png")

    def test_ocr_parser_rejects_non_text_provider_response(self):
        class OCRProvider:
            def extract_text(self, content, media_type):
                return None

        parser = OCRParserAdapter(OCRProvider())
        with self.assertRaises(TypeError):
            parser.parse(b"image", "image/png")

    def test_azure_document_intelligence_rejects_cross_host_poll_url(self):
        client = SyncClient([
            SyncResponse(
                202, headers={
                    "operation-location": "https://attacker.example/steal",
                },
            ),
        ])
        provider = AzureDocumentIntelligenceOCRProvider(
            client=client, endpoint="https://resource.example",
            api_key="runtime-secret",
        )
        with self.assertRaises(KnowledgeProviderError):
            provider.extract_text(b"image", "image/png")


if __name__ == "__main__":
    unittest.main()
