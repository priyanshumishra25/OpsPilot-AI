# Design decisions

Each entry records the decision, the reason, and what would change it. Where a claim depends on a
measurement, the measurement is in [EVALUATION.md](EVALUATION.md).

## PostgreSQL + pgvector instead of a dedicated vector database
One transactional store holds documents, versions, chunks, vectors, job state and audit rows. The
version swap ("new chunks visible, old chunks gone") is a single transaction, and permission
predicates are ordinary `WHERE` clauses evaluated in the same query as the vector scan. A separate
vector store would need a two-phase sync and duplicated access-control metadata.
*Revisit when:* the corpus reaches tens of millions of chunks per workspace, or filtered-HNSW recall
degrades (see below).

## Filtered HNSW with iterative scans
Filtering an approximate index can return fewer than *k* rows. Dense queries set
`hnsw.iterative_scan = relaxed_order` (pgvector ≥ 0.8) and re-sort the outer query, because relaxed
ordering can return slightly unordered rows. Vectors are L2-normalised and indexed with
`vector_cosine_ops`, matching the bge model's training objective.

## Hybrid retrieval with RRF, and the measured caveat
Dense, lexical and RRF hybrid retrieval are all implemented and evaluated. On the current golden set,
RRF hybrid without reranking scores *below* dense alone (Recall@5 0.946 vs 0.964), and with reranking
the two tie. Hybrid remains the default because lexical search is the only path that reliably matches
exact identifiers (alert names, error codes, flags) and costs about 7 ms. This is a judgement the
current data doesn't prove. The evaluation keeps all configurations so the decision can be reversed
on evidence.

## Rerank depth of 20
Cross-encoder latency is linear in the number of candidates. Reranking the top 40, 20 and 10 fused
candidates gave identical quality on the golden set (p50 324, 198 and 83 ms). 20 was chosen over 10 to
keep headroom for larger corpora, where relevant passages are more likely to fall below fused rank 10.

## Evidence-sufficiency threshold calibrated on a held-out split
The abstention threshold on the top rerank score is fit on the dev split only (maximising balanced
accuracy) and reported on the test split. The current value is -4.47. With only 4 unanswerable test
questions, perfect test separation is weak evidence. The threshold is configuration, not code.

## Citations as indices, never strings
Passages are sent as Anthropic `search_result` blocks. A citation is accepted only if its
`search_result_index` points at a passage we sent, its `source` matches that passage's ID, and its
`cited_text` occurs in the passage. The UI then resolves the citation through `/api/v1/chunks/{id}`
using the caller's permissions. Nothing the model writes in free text can become a citation.

## LangChain where it earns its place
LangChain provides the `Embeddings` and `BaseRetriever` interfaces (the hybrid retriever is exposed as
a LangChain retriever for later agent and evaluation use) and the recursive splitter. Markdown heading
sectioning is custom: LangChain's `MarkdownHeaderTextSplitter` rewrites whitespace, so its output isn't
a substring of the source, which breaks exact citation offsets. A test asserts offset exactness for
every chunk.

## Local embedding and reranking models
`BAAI/bge-small-en-v1.5` and `ms-marco-MiniLM-L-6-v2` run on CPU through ONNX (fastembed). The stack
and CI run without paid APIs, results are reproducible, and the provider sits behind LangChain's
`Embeddings` interface. Changing the embedding dimension requires a migration and a full re-index;
the API refuses to start if the configured dimension and schema disagree.

## Postgres as job state, queue as transport
At-least-once delivery from Redis Streams or SQS is made safe by an atomic claim in Postgres. That
also makes retries, scheduling (`available_at`), dead-lettering and stale-lease recovery queue-agnostic
and testable without the queue.

## Separate API and worker processes
They share code but not processes, so ingestion bursts (CPU-heavy embedding) don't contend with
interactive queries, and each can scale on its own signal (request rate vs queue depth).

## SeaweedFS for local S3
MinIO no longer publishes container images. SeaweedFS's S3 gateway is S3-compatible for the operations
used (put, get, delete, head-bucket). Server-side encryption headers are configurable because the local
gateway doesn't implement SSE-S3 without a KEK; in AWS, encryption is also enforced by bucket policy.

## Pillow dependency override
fastembed 0.9 caps `pillow<13`, but Pillow 12.x has known CVEs fixed in 13.0. Only fastembed's text
models are used (PIL serves its image models), so `pillow>=13` is overridden in `pyproject.toml`. A
smoke test runs both real models to guard the override.

## Bearer tokens in sessionStorage (for now)
Simple and CSRF-immune, but readable by any script that achieves XSS. Mitigations: no HTML rendering
of model or document text (answers render as text nodes), a strict CSP from nginx, and short token
lifetimes. Planned: httpOnly refresh cookie with rotation and server-side revocation.

## MCP servers instead of in-process tool functions
Each tool family runs as its own MCP server with typed schemas. Concretely this buys: (1) credential isolation:
the control-plane deploy token lives only in `mcp-operations`, the GitHub token only in `mcp-github`; (2) the
same tools are usable by any MCP client (verified with the MCP Inspector CLI) and by the agent, with one
implementation; (3) independent scaling and failure isolation (a slow Loki doesn't block knowledge search). The
cost is an extra network hop per call (single-digit milliseconds locally) and more processes.

## Identity via short-lived, audience-bound, asymmetric tool tokens
The model never supplies identity: the API mints a token per connection for the authenticated user, and
servers verify it. Ed25519 rather than a shared HMAC secret means a compromised MCP server can verify tokens but
not mint them. The audience is per server, so a token for `knowledge` is rejected by `operations`. Knowledge and
operations also re-resolve membership per call, closing the revocation window. Not yet done: binding tokens to an
agent session (the `sid` claim exists and is logged, but isn't enforced).

## Approval as a database state machine, not a prompt rule
The model can request and (after approval) trigger an action, but cannot approve, change approved arguments,
reuse an approval, or act on a changed target. All of that is enforced by conditional updates and hash
comparison, tested with concurrent execution attempts. Two-person approval (approver ≠ requester) is on by
default (`OPSPILOT_APPROVAL_REQUIRE_DISTINCT_APPROVER`).

## A simulated deployment system for the demo
Rollbacks need something real to act on that is safe to break. The demo control plane is a minimal deployment
system (releases from git tags, compare-and-swap deploys, idempotency keys) whose services really change
behaviour by version, so telemetry and recovery are observed rather than scripted. It is deliberately the
*only* system the operations server can mutate.

## Deterministic demo git history
Building the demo repositories with fixed identities and timestamps makes commit SHAs reproducible, so the
control plane, the GitHub server, tests and documentation all refer to the same real commits without committing
nested repositories.

## Claude Agent SDK, configured down to an MCP-only agent
The SDK provides the agent loop, MCP client, tool permissions, hooks, budgets, structured output, interruption and
session resume. Writing those by hand is where agent harnesses usually go wrong. OpsPilot turns off what it doesn't
want: built-in tools (`tools=[]`), local settings (`setting_sources=[]`), and any tool outside the role policy
(`disallowed_tools`, so it never enters the model's context). Costs: a CLI subprocess per turn (~1 s start-up),
and a larger image. Turns run in a separate worker so they scale independently of the API.

## Testing the agent with a scripted model, not a mocked SDK
The bundled CLI honours `ANTHROPIC_BASE_URL`, so tests point it at a scripted Messages API. Everything except the
model's choices is real (SDK, CLI, MCP over HTTP, approvals, Postgres), which catches integration bugs a mocked SDK
would hide. It found two: resume needs a stable working directory, because the session-store key derives from the
cwd; and the CLI merges tool rounds in the history it sends. Model quality still needs live evaluation.

## Not yet decided by implementation
Why ECS instead of Kubernetes is deferred to the deployment phase.
