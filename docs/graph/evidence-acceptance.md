# Graph extraction evidence acceptance (A-22-01)

## Contract

Knowledge-backed graph candidates must retain both the canonical document revision and at least one verifiable source-fragment locator.

`GraphEvidenceSpan` records:
- the Knowledge chunk ID;
- the half-open character range `[start_offset, end_offset)` in the canonical, cleaned document text;
- the exact fragment text;
- an optional one-based page number.

Its stable evidence reference is `chunk://<chunk-id>?start=<n>&end=<n>&sha256=<digest>[&page=<n>]`. The digest is SHA-256 over the exact UTF-8 fragment. `GraphExtractionService` validates the offsets, fragment bytes, page number and unique chunk IDs before invoking the extractor. When fragment spans are supplied, each entity and relation candidate must cite the canonical source reference and at least one locator from that source's verified fragment catalog. Unknown or fabricated locators fail the whole source batch before persistence.

The canonical source reference on each candidate retains tenant, source type, document ID and version. The chunk locator then resolves the extracted assertion to a bounded text range within that exact revision.

## Review chain

Extraction creates reviewable candidates only. Confidence is finite and constrained to `[0, 1]`. Candidate materialization is not implied by extraction. The durable candidate store accepts a reviewer decision (`accepted` or `rejected`), reviewer identity and non-empty reason, and applies it only while the candidate remains in `candidate` or `conflict` state under the same tenant. The trusted materializer remains responsible for re-checking current source ACL, schema and endpoint state before accepted candidates become graph records.

## Automated evidence

`tests/test_issue_22_a_22_01_graph_evidence.py` ingests a controlled knowledge document through `KnowledgeService`, uses the actual KnowledgeService ingestion output chunk IDs/offsets, extracts two entities and one relation, and checks that 100% of the generated candidates resolve to verified fragments (the required threshold is 80%). It also exercises accepted/rejected review transitions and negative cases for forged fragment references and mismatched offsets.

## Acceptance boundary

The checked-in sample is deterministic test data, not a customer or production knowledge-base sample. The automated test proves the evidence contract and review workflow; it does **not** establish the required ≥80% traceability rate on real knowledge-base samples or validate human semantic correctness of extraction. A-22-01 must remain open until a representative, ACL-authorized real-document sample is reviewed and its measured traceability rate and confidence distribution are recorded.
