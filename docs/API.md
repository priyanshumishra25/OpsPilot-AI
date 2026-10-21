# API reference

The OpenAPI schema is generated from the code and served at `/api/openapi.json`, with an interactive
UI at `/api/docs`. All `/api/v1` endpoints except login require `Authorization: Bearer <token>`.
`X-Workspace-Id` optionally selects one of the caller's workspaces.

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/api/v1/auth/login` | — | Rate-limited per IP and per email |
| GET | `/api/v1/me` | authenticated | Role, permissions, scopes, workspaces |
| POST | `/api/v1/documents` | `documents:write` | multipart `file`, `access_scope`, `title?`; 202 with `job_id`, or `deduplicated: true` |
| GET | `/api/v1/documents` | `documents:read` | Only documents in the caller's scopes |
| GET | `/api/v1/documents/{id}` | `documents:read` | Versions and recent jobs; 404 if out of scope |
| GET | `/api/v1/documents/{id}/chunks` | `documents:read` | Passages of the live version |
| POST | `/api/v1/documents/{id}/reindex` | `documents:write` | |
| DELETE | `/api/v1/documents/{id}` | `documents:delete` | Removes chunks, versions and artifacts |
| GET | `/api/v1/chunks/{id}` | `documents:read` | Citation resolution; same predicate as retrieval |
| GET | `/api/v1/ingestion/jobs` | `documents:read` | `?status=` |
| POST | `/api/v1/search` | `query` | Retrieval only, with full diagnostics; `strategy`, `rerank`, `k`, `filters` |
| POST | `/api/v1/query` | `query` | SSE events: `started`, `retrieval`, `text`*, `citation`*, `done` or `error` |
| GET | `/api/v1/queries`, `/api/v1/queries/{id}` | `query` | Caller's own history |
| GET | `/api/v1/audit` | `audit:read` | |
| GET | `/healthz`, `/readyz`, `/metrics` | — | Liveness, dependency readiness, Prometheus |

Errors share one shape: `{"error": {"code", "message", "details", "trace_id"}}`.
