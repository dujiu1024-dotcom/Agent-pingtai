# Bounded query rewriting contract

`KnowledgeService` accepts an optional host-owned `QueryRewriter` implementation. The adapter receives the normalized user query and returns a single rewritten query string; the service remains responsible for enforcing the input/output contract.

## Contract

- User queries must be strings, non-empty after trimming, and no longer than the configured `max_query_chars` (default 4,096).
- `max_query_chars` must be an integer from 1 through 32,768; booleans and non-integers are rejected.
- A configured rewriter must return a string that is non-empty after trimming and within the same character limit.
- Invalid, empty, oversized, or failed rewrite results fail closed before embedding or repository search. The service does not silently fall back to the original query.
- Rewritten query text is used for both embedding and repository retrieval. ACL enforcement remains inside the repository search and is not delegated to the rewriter.
- Tracing records only whether rewriting occurred and query length; raw user or rewritten query text and provider exception messages are not added to observations.

## Host integration

Implement the exported `QueryRewriter` protocol with an async `rewrite(query: str) -> str` method and inject it into `KnowledgeService`. The host owns provider credentials, client lifecycle, timeouts, and provider-specific policy. A missing rewriter preserves the normalized original query.

The limit is a character bound, not a tokenizer/token budget. Deployments that require token-aware constraints must enforce those separately at their model/provider boundary.
