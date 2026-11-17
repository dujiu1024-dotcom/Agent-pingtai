# Reproducible RAG evaluation datasets

RAG regression fixtures are versioned JSON datasets, not executable Python. Load
them with `load_evaluation_dataset(path)` from
`agent_platform.knowledge.evaluation_dataset`.

Each dataset has exactly five root fields: `dataset_id`, positive integer
`version`, `description`, `documents`, and `cases`. Document keys and case
IDs are unique. Every expected document key must resolve to a document in the
same dataset. Case queries and document identity/source/content fields must be
non-empty; abstention expectations must be booleans. Unknown fields and
malformed shapes are rejected so typos cannot silently weaken an evaluation.

The loader returns immutable document/case records and a SHA-256 fingerprint
of canonical JSON (sorted object keys, compact separators, UTF-8, finite JSON
numbers only). The fingerprint changes when fixture content or expected
results change, but not when JSON object key order changes. Store the dataset
version and fingerprint alongside evaluation output to make comparisons
reproducible.

The retrieval evaluator measures document-level Recall@K, Precision@K, MRR,
nDCG@K and citation coverage. Answer evaluation measures expected-term
coverage, citation precision/recall, citation-source integrity and expected
abstention. These deterministic checks are regression signals; synthetic
fixtures and lexical answer-term checks do not establish production retrieval
quality or replace human review on representative, authorized corpora.
