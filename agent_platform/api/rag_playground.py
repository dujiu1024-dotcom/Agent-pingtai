"""Query playground API for tenant-authorized, configurable RAG experiments."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Sequence
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.evaluation_dataset import parse_evaluation_dataset
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.rag_metrics import evaluate_rag_dataset
from agent_platform.knowledge.retrieval import RetrievalFilters, RetrievalStrategy

PrincipalResolver = Callable[[Request], Awaitable[KnowledgePrincipal]]
RAGResolver = Callable[[KnowledgePrincipal, str, bool], Awaitable[RAGService] | RAGService]


def create_rag_playground_router(
    *,
    resolve_principal: PrincipalResolver,
    resolve_rag: RAGResolver,
    embedding_models: Sequence[str],
) -> APIRouter:
    models = tuple(dict.fromkeys(
        value.strip() for value in embedding_models
        if isinstance(value, str) and value.strip()
    ))
    if not models:
        raise ValueError("at least one configured embedding model is required")
    router = APIRouter(prefix="/api/platform/v1/rag-playground", tags=["rag-playground"])

    async def principal_for(request: Request) -> KnowledgePrincipal:
        principal = await resolve_principal(request)
        if not isinstance(principal, KnowledgePrincipal):
            raise HTTPException(401, "authenticated knowledge principal required")
        return principal

    @router.get("/options")
    async def options(request: Request) -> dict[str, Any]:
        await principal_for(request)
        return {"embedding_models": list(models), "default_embedding_model": models[0],
                "rerank_options": [False, True], "max_top_k": 20}

    @router.post("/query")
    async def query(request: Request) -> dict[str, Any]:
        principal = await principal_for(request)
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object required") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON object required")
        question = payload.get("query")
        if not isinstance(question, str) or not question.strip() or len(question) > 4096:
            raise HTTPException(400, "query must contain 1-4096 characters")
        top_k = payload.get("top_k", 5)
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
            raise HTTPException(400, "top_k must be an integer between 1 and 20")
        threshold = payload.get("min_score", 0.0)
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            raise HTTPException(400, "min_score must be between 0 and 1")
        model = payload.get("embedding_model", models[0])
        if not isinstance(model, str) or model not in models:
            raise HTTPException(400, "embedding_model is not an enabled model")
        rerank = payload.get("rerank", False)
        if not isinstance(rerank, bool):
            raise HTTPException(400, "rerank must be a boolean")
        try:
            service = resolve_rag(principal, model, rerank)
            if hasattr(service, "__await__"):
                service = await service
            if not isinstance(service, RAGService):
                raise RuntimeError("RAG resolver did not return a RAGService")
            result = await service.answer(
                principal=principal, query=question.strip(), limit=top_k,
                filters=RetrievalFilters(min_score=float(threshold)),
                strategy=RetrievalStrategy.HYBRID,
            )
        except PermissionError as exc:
            raise HTTPException(403, "RAG query is not authorized") from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "RAG provider is unavailable") from exc

        hits = []
        for hit in result.retrieved:
            parsed = urlsplit(hit.source_uri)
            source_url = hit.source_uri if parsed.scheme in {"https", "http"} and parsed.netloc else None
            hits.append({
                "document_id": hit.document_id, "document_version": hit.document_version,
                "chunk_id": hit.chunk_id, "text": hit.text, "score": hit.score,
                "title": hit.title, "source_uri": hit.source_uri,
                "source_url": source_url, "page_number": hit.page_number,
                "start_offset": hit.start_offset, "end_offset": hit.end_offset,
                "source_locator": {
                    "page_number": hit.page_number,
                    "start_offset": hit.start_offset,
                    "end_offset": hit.end_offset,
                },
                "retrieval_trace": dict(hit.retrieval_trace),
                "citation_id": hit.citation_id,
            })
        return {
            "query": question.strip(), "embedding_model": model, "rerank": rerank,
            "top_k": top_k, "min_score": float(threshold),
            "answer": result.answer, "abstained": result.abstained,
            "reason": result.reason, "retrieved": hits,
            "citations": list(result.citations),
        }


    @router.post("/evaluate")
    async def evaluate(request: Request) -> dict[str, Any]:
        principal = await principal_for(request)
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object required") from exc
        if not isinstance(payload, dict) or set(payload) - {
            "dataset", "top_k", "min_score", "embedding_model", "rerank",
        }:
            raise HTTPException(400, "evaluation request has an invalid shape")
        try:
            dataset_payload = payload.get("dataset")
            dataset = parse_evaluation_dataset(dataset_payload)
            if len(dataset.cases) > 100 or len(dataset.documents) > 1000:
                raise ValueError("evaluation dataset exceeds 100 cases or 1000 documents")
            if any(len(document.content) > 200000 for document in dataset.documents):
                raise ValueError("evaluation document content exceeds 200000 characters")
            if any(len(case.query) > 4096 or len(case.answer) > 20000 for case in dataset.cases):
                raise ValueError("evaluation query or reference answer is too long")
            top_k = payload.get("top_k", 5)
            if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
                raise ValueError("top_k must be an integer between 1 and 20")
            threshold = payload.get("min_score", 0.0)
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
                raise ValueError("min_score must be between 0 and 1")
            model = payload.get("embedding_model", models[0])
            if not isinstance(model, str) or model not in models:
                raise ValueError("embedding_model is not an enabled model")
            rerank = payload.get("rerank", False)
            if not isinstance(rerank, bool):
                raise ValueError("rerank must be a boolean")
            service = resolve_rag(principal, model, rerank)
            if hasattr(service, "__await__"):
                service = await service
            if not isinstance(service, RAGService):
                raise RuntimeError("RAG resolver did not return a RAGService")
            report = await evaluate_rag_dataset(
                knowledge=service.knowledge,
                principal=principal,
                dataset=dataset,
                top_k=top_k,
                embedding_model=model,
                rerank=rerank,
                min_score=float(threshold),
            )
        except PermissionError as exc:
            raise HTTPException(403, "RAG evaluation is not authorized") from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "RAG evaluation provider is unavailable") from exc
        return report.to_dict()

    return router
