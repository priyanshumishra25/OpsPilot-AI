# Architecture

This describes what is implemented. Planned components (the agent, approval UI, AWS deployment) are listed at
the end and don't exist yet.

## Processes

| Process | Entry point | Scales on |
|---|---|---|
| API | `uvicorn opspilot.main:app` | interactive request rate; holds the embedding and reranker models for query-time work |
| Ingestion worker | `python -m opspilot.cli worker` | ingestion backlog; CPU-bound embedding |
| Migrate / bootstrap | `alembic upgrade head && python -m opspilot.cli bootstrap` | one-shot |
| Frontend | nginx serving the Vite build | static |
| MCP servers ×4 | `python -m opspilot.mcpservers.<name>` | per tool family; knowledge holds the models |
| Demo control plane, services, traffic | `demo/` image | demo only |

The API and worker share one Python package and image but run as separate processes. Ingestion is
the expensive, bursty workload, and isolating it keeps a large upload from degrading query latency.
They communicate only through Postgres (state) and the queue (wake-ups).

## Module boundaries (`backend/src/opspilot`)

```
api/          HTTP transport only: routing, request/response schemas, SSE, error mapping
auth/         password hashing, token issue/verify, Principal + role→permission policy
ingestion/    IngestionService (upload/version/dedupe/delete) and IngestionWorker (claim→extract→chunk→embed→publish)
rag/          extraction, chunking, embeddings, retrieval SQL, fusion, reranking, generation, citations, cost
queue/        JobQueue protocol + Redis Streams and SQS adapters
storage/      ObjectStore protocol + S3 adapter
db/           SQLAlchemy models and engine/session lifecycle
evaluation/   metrics and the golden-set runner
container.py  composition root (the only place concrete adapters are chosen)
```

Route handlers resolve the `Principal` and call services. They contain no retrieval or ingestion
logic. Domain code raises typed errors (`errors.py`), which `api/errors.py` maps to HTTP statuses
with a stable JSON error body that carries the trace ID.

## Data model

```
workspaces ─┬─ memberships (user, role, access_scopes[])
            ├─ documents (source, access_scope, current_version_id, status)
            │     └─ document_versions (content_hash, object_key, status, chunk_count)
            │            └─ chunks (content, section_path, offsets, access_scope, embedding vector(384), tsv)
            ├─ ingestion_jobs (status, stage, attempts, available_at, error, timings)
            ├─ rag_queries (query, answer, citations, diagnostics, usage, trace_id)
            └─ audit_log
```

Every tenant-owned row carries `workspace_id`. `chunks.access_scope` is denormalised from the
document so the scope predicate applies inside the vector and text index scans. `tsv` is a generated
column (heading weighted A, body weighted B) with a GIN index. `embedding` has an HNSW index using
`vector_cosine_ops`.

## Ingestion flow

1. `POST /api/v1/documents`: validate (size, extension and magic bytes, UTF-8, scope the caller can
   read). Hash the bytes. If the same document already has this hash, return `deduplicated` with no
   new work. Otherwise store the artifact in S3, then insert the version and job rows in one
   transaction (with an audit row), commit, and publish the job ID.
2. The worker receives the job ID and **claims** it with a conditional `UPDATE … WHERE status IN
   (queued, retry_scheduled) AND available_at <= now() RETURNING`. A duplicate or early delivery
   claims nothing and is acknowledged.
3. Extract (pypdf / UTF-8), normalise, section on Markdown headings (fence-aware), and split each
   section with LangChain's `RecursiveCharacterTextSplitter`, keeping exact offsets.
4. Embed. Vectors are reused for passages whose `(content_hash, heading path, title)` already exist in
   the workspace.
5. In one transaction: replace this version's chunks, mark it indexed, supersede and delete the
   previous version's chunks (only if this version is newer), switch `current_version_id`, and mark
   the job succeeded. Retrieval never sees a half-indexed document.
6. Failures: extraction and validation errors fail permanently. Anything else is rescheduled with
   exponential backoff and full jitter. When attempts are exhausted the job is marked `dead` and sent
   to the DLQ. A sweeper republishes due jobs (covering lost messages) and recovers jobs whose worker
   lease expired.

The queue is a delivery mechanism, not a source of truth. Postgres holds the job state, so Redis
Streams (local) and SQS (AWS) are interchangeable behind `JobQueue`.

## Query flow

```
POST /api/v1/query (SSE)
  → Principal (token → membership → role, scopes)           [api/deps.py]
  → analyse(query): rule-based class + keyword OR-query     [rag/query.py]
  → dense top-30  ║  lexical top-30   (parallel, each filtered by workspace + scope + live version)
  → RRF fuse → hydrate (re-applies the same predicate) → cross-encoder rerank top 20
  → budget (token budget, dedupe, ≤4 passages/doc) → sufficiency gate
  → event: retrieval (full diagnostics)
  → if insufficient: deterministic abstention, no model call
  → else Claude stream with search_result blocks → resolve each citation → events: text / citation
  → persist rag_queries (answer, citations, diagnostics, usage, latency, status) → event: done
```

Client disconnects cancel generation. The record is still finalised as `cancelled` (shielded write).

## Observability

Structured JSON logs with request ID, trace ID and span ID, and recursive redaction of secret-like
keys and token patterns. OpenTelemetry spans cover HTTP (FastAPI instrumentation), `rag.retrieve`,
`llm.generate_answer` and `ingestion.process_job`, exported over OTLP when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set. The MCP gateway injects W3C trace context into every MCP request and
the servers extract it (ASGI middleware), so one trace covers API request → gateway → MCP server → tool span
(`mcp.tool <name>`) → retrieval spans. Agent turns get an `agent.turn` root span. Verified live in Jaeger
(`docker compose --profile observability up -d jaeger`, UI on :16686): a single trace spans `opspilot-api` and
`opspilot-mcp-knowledge`. Tool calls made by the agent's CLI subprocess are not yet linked to the turn span. Prometheus metrics at `/metrics`: HTTP rate and latency,
ingestion outcomes and stage durations, retrieval latency, provider-reported LLM tokens, and
estimated cost. `/readyz` checks the database, Redis, object store and queue.

## MCP layer

```
API (MCP client, McpGateway) --Ed25519 tool token (aud = server, 120 s)--> MCP server (public key only)
   knowledge     -> Postgres (same hybrid retriever + permission predicates), re-resolves membership per call
   github        -> allow-listed repos: GitHub REST API or local git (demo), validated refs/paths
   observability -> Prometheus / Loki via fixed query templates; control plane (read token)
   operations    -> control plane (deploy token, the only holder), approvals + incidents in Postgres
```

Each server is a separate process so a credential is held by exactly one process (for example, the deploy
token only by `mcp-operations`) and tool families scale independently. Tool calls are logged as structured
`mcp_tool_call` audit events (caller, tool, redacted arguments, outcome, duration); approval transitions,
incident creation and refused operations are written to `audit_log`.

## Approval state machine (`approvals/service.py`)

```
PENDING --approve (approvals:decide, approver != requester)--> APPROVED --begin_execution--> EXECUTING --> SUCCEEDED | FAILED
   |--reject--> REJECTED        |--expiry--> EXPIRED
   '--expiry--> EXPIRED
```

Every arrow is a conditional `UPDATE ... WHERE status = <from> [AND expires_at > now()] RETURNING`. Execution
requires the hash of the presented arguments to equal the approved hash, sets a unique `execution_id` (also the
control-plane idempotency key), and re-validates the target's `current_version` and `state_version` against the
snapshot taken at request time before deploying with compare-and-swap.

## Demo environment (`demo/`)

- **Control plane** (FastAPI + SQLite): releases read from git tags, a seeded deployment history, deploys
  (compare-and-swap on `state_version`, idempotency keys), scenarios. Separate read, deploy and admin tokens.
- **orders-api / checkout-api**: one codebase whose behaviour follows the deployed version and active faults
  (e.g. v2.16.0 holds a pooled DB connection across a 1.5–3 s inventory call). Prometheus metrics; JSON logs to
  stdout and Loki.
- **traffic**: steady synthetic load (12 + 6 req/s), including occasional customer notes containing an injection
  attempt that ends up in logs.
- **git history**: `demo/repos/build_repos.py` builds the services' repositories with fixed authors and dates, so
  commit SHAs are identical everywhere.

## Agent (`agent/`)

```
UI --POST /agent/sessions--> API --(user_message event + queue)--> agent-worker
agent-worker: ClaudeSDKClient(tools=[], setting_sources=[], strict MCP config, permission_mode=dontAsk,
              allowed_tools=<role policy>, disallowed_tools=<everything else>, output_format=TurnOutput schema,
              PreToolUse budget hook, session_store=Postgres, resume=<sdk session id>)
   --> bundled Claude Code CLI --> Anthropic API
   --> MCP servers over HTTP with per-user, per-session tool tokens (TTL = turn time budget)
events (assistant text, tool calls/results, approvals, report, usage) --> agent_events (Postgres) --> SSE (Redis wake-up)
```

- **Isolation:** no built-in tools (no shell, files or web), no user or project settings, a per-session working
  directory with no project files.
- **Budgets:** `max_turns`, `max_budget_usd`, a wall-clock limit (interrupts the turn), and a tool-call cap
  enforced by a `PreToolUse` hook.
- **Durability:** SDK transcripts are mirrored to `agent_transcript_entries` through a `SessionStore`
  implementation that passes the SDK's conformance suite. A turn can therefore resume on any worker.
- **Output:** the turn ends with the SDK's `StructuredOutput` tool against `TurnOutput` (answer and an optional
  `IncidentReport`). The runner validates it, checks each evidence item against tool calls that actually succeeded,
  persists the incident, links any approvals it requested, and marks the session `awaiting_approval` while one is
  open. Thinking blocks are never recorded or shown.
- **Recovery:** a worker restart fails in-flight turns explicitly; side effects are approval-gated anyway.

## Planned (not implemented)

Linking agent CLI tool calls into the turn trace,
Terraform for AWS (ECS Fargate, RDS, S3, SQS, Secrets Manager), and a deployment workflow with GitHub OIDC.
