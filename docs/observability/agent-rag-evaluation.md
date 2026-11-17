# Agent/RAG evaluation orchestration

The `agent_rag_evaluation` module complements the general evaluation runner with
an explicit, immutable Agent/RAG run contract.

- Pin `dataset_id` and positive `dataset_version`; the loader must return that
  exact version or the run fails closed.
- Pin `endpoint_id`, `model_key`, HTTP(S) endpoint URL and `model_version`.
  Store endpoint credentials in the platform secret manager; never put them in
  the evaluation plan or report.
- Set a per-run concurrency limit from 1 through 32. A semaphore bounds in-flight
  endpoint calls, and a failed case prevents partial report persistence.
- Reports include a canonical SHA-256 fingerprint of the complete ordered dataset payload (case IDs, queries, answer terms, relevant documents and required citations). Reusing the same dataset ID/version with changed contents therefore produces a different fingerprint and is detectable during report comparison.
- Reports include per-case and aggregate retrieval Recall/Precision@K, MRR and
  nDCG@K; answer-term precision/recall/F1 and forbidden-term rate; citation
  precision/recall/coverage/validity.
- Dataset order is retained in case results. The run records tenant, dataset
  version, endpoint/model identity and concurrency limit for reproducibility.

The report repository is a narrow persistence port. Production adapters should
serialize the complete report into the existing evaluation-run evidence store
and enforce tenant scoping at the storage boundary.
