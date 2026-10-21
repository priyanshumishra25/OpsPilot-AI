# MCP tool reference

Four MCP servers, built with the official `mcp` Python SDK (v2.3) and served over Streamable HTTP (stateless,
JSON responses). Every tool has a typed input schema (validated by the SDK) and a typed output schema. Errors are
returned as `isError` results carrying `{"error": {"code", "message", "details"}}`. Codes: `invalid_argument`,
`not_found`, `permission_denied`, `conflict`, `upstream_unavailable`, `timeout`.

## Authentication and identity

The OpsPilot API mints a **tool token** per connection for the authenticated user: an EdDSA (Ed25519) JWT with
`iss=opspilot-api`, `aud=<server>`, `sub` (user), `ws` (workspace), `role`, `doc_scopes`, `scope` (tool scopes),
`jti`, and a 120 s default TTL. Each server verifies it with the **public key only** and requires its read scope at
the transport (HTTP 401/403 otherwise). Tools read identity from the verified token, never from arguments. The
knowledge and operations servers also re-resolve the membership from the database on every call, so revoking a
user takes effect before their token expires.

| Role | Tool scopes |
|---|---|
| viewer | `knowledge:read` |
| engineer, operator | all `*:read`, `operations:request`, `operations:execute` |
| admin | all |

Approving is **not** a tool. It requires the `approvals:decide` permission (operator, admin) through the
OpsPilot API, and the approver must differ from the requester.

## knowledge (`:8101`)

| Tool | Purpose | Bounds |
|---|---|---|
| `search_knowledge(query, top_k=5, source_types?)` | Hybrid retrieval over documents the caller may read; returns passages with `chunk_id` and `evidence_sufficient` | query ≤ 500 chars, `top_k` ≤ 10, passage ≤ 1,500 chars |
| `get_document(document_id)` | Metadata and section outline | — |
| `get_document_section(document_id, section)` | Text of one section (overlaps merged) | ≤ 8,000 chars |
| `list_available_sources()` | Readable indexed documents | ≤ 200 |

## github (`:8102`)

Repositories must be in `OPSPILOT_GITHUB_REPOSITORIES` and permitted for the caller's workspace. Two providers:
the GitHub REST API (a token from `OPSPILOT_GITHUB_TOKEN`; read-only fine-grained scopes suffice) and local git
(used for the deterministic demo repositories).

| Tool | Notes |
|---|---|
| `list_repositories()` | Repositories visible to the workspace |
| `search_repository_code(repository, query, limit=20)` | Plain terms only: qualifiers such as `repo:` or `org:` are rejected so search cannot escape the allow-list. GitHub provider requires a token |
| `get_repository_file(repository, path, ref="HEAD")` | Path and ref validated (no `..`, absolute paths, or option-like refs); ≤ 100 KB |
| `get_recent_commits(repository, ref="HEAD", limit=10, path?)` | ≤ 50 |
| `get_pull_request(repository, number)` | GitHub provider only |
| `get_commit_diff(repository, sha)` | ≤ 50 files, patches ≤ 20 KB each |

## observability (`:8103`)

PromQL and LogQL are generated from fixed templates for allow-listed services (`orders-api`, `checkout-api`).

| Tool | Notes |
|---|---|
| `query_service_metrics(service, metric, window_minutes=30, step_seconds=30)` | `metric` ∈ request_rate, error_rate, latency_p95, latency_p99, db_pool_utilization; ≤ 400 points; returns the generated query |
| `search_service_logs(service, level?, contains?, since_minutes=30, limit=50)` | Newest first; `contains` is a literal filter (no backticks or newlines); ≤ 200 lines; marked untrusted |
| `get_service_health(service)` | Classification with the rule returned verbatim: unhealthy if 5xx ratio > 5% (5m); degraded if > 1%, p95 > 1.5 s or pool ≥ 95%. The 5-minute windows lag recovery by up to 5 minutes |
| `get_error_rate(service, window_minutes=15)` | Ratio and breakdown by status and error type |
| `get_deployment_timeline(service, limit=10)` | From the demo control plane |

## operations (`:8104`, demo environment only)

| Tool | Scope | Notes |
|---|---|---|
| `get_deployment_status(service)` | read | Version, commit, `state_version` |
| `get_available_rollback_versions(service)` | read | Eligibility per policy: older than current, ≥ 24 h in production, never rolled back; > 2 releases back flags owner sign-off |
| `create_incident_report(title, severity, service, report)` | request | `report` must match `IncidentReport` exactly (unknown fields, such as a numeric confidence, are rejected) |
| `request_deployment_rollback(service, target_version, reason, incident_id?)` | request | Creates a PENDING approval bound to `{service, target_version}`, the target state snapshot and an expiry (30 min). Changes nothing |
| `execute_approved_rollback(approval_id, service, target_version)` | execute | Only after a human approved it; arguments must hash-match the approval; the caller must be the requester or approver; consumed once (atomic `APPROVED → EXECUTING` with a one-time execution id); re-checks live version and `state_version` and aborts if changed; deploys with compare-and-swap and the execution id as idempotency key; verifies the running version afterwards |

## Connecting an independent client

```bash
TOKEN=$(docker compose exec -T api python -m opspilot.cli mint-tool-token \
  --email engineer@opspilot.demo --workspace demo --server github)
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:8102/mcp --transport http \
  --header "Authorization: Bearer $TOKEN" --method tools/call \
  --tool-name get_recent_commits --tool-arg repository=opspilot-demo/orders-api --tool-arg limit=2
```

`mint-tool-token` needs the API's private signing key, so it is an operator tool. The token carries the user's
real permissions.
