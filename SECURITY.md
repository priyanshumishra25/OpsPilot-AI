# Security

## Reporting
Please report vulnerabilities privately to the repository owner rather than opening a public issue.

## Controls implemented

| Area | Control | Where | Tested in |
|---|---|---|---|
| Authentication | Argon2id hashing; constant-shape failures (unknown user verifies against a dummy hash, same error message) | `auth/passwords.py`, `auth/service.py` | `test_api.py::test_auth_required_and_login_failures` |
| Tokens | HS256 JWT with `iss`, `aud`, `exp`, `nbf`, `jti` required; 30 min default TTL; `alg=none` and wrong key/audience rejected; secret ≥ 32 chars; local dev secret refused outside `environment=local` | `auth/tokens.py`, `config.py` | `test_core_units.py` |
| Authorisation | Role → permission map evaluated server-side; roles and scopes loaded from the membership row on every request, never from the token or body | `auth/principal.py`, `api/deps.py` | `test_api.py`, `test_ingestion.py` |
| Tenant isolation | `workspace_id` predicate in every query; `X-Workspace-Id` is checked against membership (403 if not a member) | repositories and routes | `test_retrieval_isolation.py` (dense, lexical, hybrid), `test_api.py` |
| Document scopes | Scope predicate inside each retrieval candidate query (pre-ranking); caller filters can only narrow scopes; citation resolution and document reads use the same predicate; out-of-scope reads return 404 | `rag/repository.py`, `routes/documents.py` | `test_access_scope_filtering_and_filters_cannot_widen`, `test_restricted_scope_hidden_from_general_users` |
| Upload safety | Size cap enforced while reading; extension and magic bytes must agree; UTF-8 only; NUL bytes rejected; filenames sanitised (no paths); PDF page cap; encrypted PDFs rejected; client `Content-Type` ignored | `rag/extraction.py`, `ingestion/service.py` | `test_upload_abuse_rejected`, `test_extraction_chunking.py` |
| Query injection | Lexical queries are rebuilt from alphanumeric tokens and passed as bound parameters to `websearch_to_tsquery` | `rag/query.py` | `test_lexical_search_handles_hostile_query_text` |
| Prompt injection | Retrieved passages go in `search_result` blocks, labelled untrusted in the system prompt; the model has no tools in Phase 1; citations are validated against retrieved records | `rag/generation.py` | `test_citation_resolver_accepts_only_verifiable_citations` |
| Secrets | No secrets in code; `.env` git-ignored; `SecretStr` settings; log redaction of secret-like keys and token formats (`sk-ant-`, `ghp_`, `github_pat_`, `AKIA`) | `logging.py` | `test_redaction` |
| Abuse | Redis fixed-window rate limits on login (per IP and per email) and queries (per user) | `api/deps.py` | `test_login_rate_limited` |
| Audit | Login success and failure, upload, dedupe, re-index and delete written in the same transaction as the action | `audit.py` | `test_document_lifecycle_and_cross_tenant_isolation` |
| Transport and browser | nginx CSP (`script-src 'self'`, `frame-ancestors 'none'`), `nosniff`, no-referrer; answers rendered as text, never HTML | `frontend/nginx.conf` | — |
| Supply chain | Lockfiles committed; `pip-audit`, `npm audit`, gitleaks and bandit in CI; non-root containers | `.github/workflows/ci.yml`, Dockerfiles | CI (not yet run on GitHub) |

## Known gaps (tracked)
- No refresh tokens or server-side token revocation; logout is client-side.
- Rate limiting fails closed if Redis is unavailable (requests error rather than bypass), which
  trades availability for safety.
- Postgres row-level security isn't enabled; isolation relies on application predicates covered by
  tests.
- TLS termination is expected at the load balancer (AWS phase); the local stack is HTTP on loopback
  only.
- MCP tool, agent and approval controls are not implemented yet; see THREAT_MODEL.md.
