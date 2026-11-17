# RAG batch quality metrics

The RAG workbench evaluates a pinned `RAGEvaluationDataset` using the same
versioned JSON shape and canonical dataset fingerprint used by the knowledge
evaluation fixtures. The batch endpoint is:

`POST /api/platform/v1/rag-playground/evaluate`

The authenticated KnowledgePrincipal is resolved by the platform; tenant and
user identity are never accepted from the request body. The selected embedding
model and rerank setting are passed through the same configured RAG resolver as
the Query playground. Each case uses the existing ACL-first
`KnowledgeService.retrieve` path and applies the configured minimum score.

## Metrics

- **Hit Rate@K**: 1 when at least one expected source document appears in the
  top K; otherwise 0. The report is the mean across cases with positive
  relevance labels.
- **MRR**: mean reciprocal rank of the first expected source document in the
  top K; a miss contributes 0.
- **NDCG@K**: binary-relevance discounted cumulative gain normalized by the
  ideal ranking. Source documents are deduplicated at their first retrieved
  rank so multiple chunks from one document cannot inflate ranking scores.
- **Token coverage**: proportion of reference-answer lexical tokens present in
  the retrieved chunk text. It uses the deterministic `unicode-word-v1`
  tokenizer and is a retrieval-context coverage signal, not a model tokenizer
  or a semantic faithfulness score.

Cases with no expected source documents (for example, expected-abstention
cases) are excluded from ranking-metric means. Cases with an empty reference
answer are excluded from token-coverage means. The report includes the number
of scored cases so null metrics are not mistaken for zero.

## Reproducibility and limits

The report includes the dataset fingerprint and a run fingerprint over the
dataset fingerprint, top K, embedding model, rerank flag, minimum score, and
tokenizer version. Repeating a run with the same inputs and deterministic
retrieval provider produces the same fingerprint and metric definitions.
Provider-level nondeterminism can still change retrieved results.

The API caps a run at 100 cases, 1,000 documents, 200,000 characters per
document, and 20 top-K results. The ranking metrics are deterministic
retrieval metrics; they do not invoke an LLM judge or claim to replace RAGAS
faithfulness, context precision, or answer relevancy evaluations. They can be
consumed as a structured report by the broader Model Eval workflow without
coupling this RAG-only task to its multi-model execution lifecycle.
