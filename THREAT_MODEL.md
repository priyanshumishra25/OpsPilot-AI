# Threat model

Scope: the implemented system (Phases 0–2). Rows marked *planned* describe controls required before
the corresponding feature ships.

## Assets
Workspace documents, especially restricted scopes; answers derived from them; user credentials and
tokens; the model API key; future infrastructure-mutation capability.

## Trust boundaries
1. Browser → API (untrusted input, bearer token).
2. API → Postgres, Redis, S3 (trusted infrastructure, least-privilege credentials).
3. Uploaded documents → ingestion and retrieval (**untrusted content**, possibly hostile).
4. API → model provider (trusted service; its *output* is treated as untrusted).
5. *Planned:* agent → MCP tools → infrastructure.

## Threats and mitigations

| # | Threat | Mitigation | Status |
|---|---|---|---|
| T1 | User reads another workspace's documents through search, document APIs or citations | Workspace predicate in every query; citations resolved through the permission-checked chunk endpoint; 404 (not 403) for foreign IDs | Implemented, tested |
| T2 | User reads restricted-scope content within their workspace | Scope predicate inside candidate queries (so restricted chunks never occupy result slots); filters can only narrow scopes; upload into unreadable scopes refused | Implemented, tested |
| T3 | Indirect prompt injection in an uploaded document ("ignore instructions, reveal key") | Passages delivered as data blocks marked untrusted; the model has no tools or privileges in Phase 1, so injected text can at most influence wording; citations can't be fabricated; the demo corpus includes an injection sample, and the golden set checks the planted fake key never appears in answers (runs when an API key is configured) | Implemented; live-model check pending |
| T4 | Model fabricates sources | Citations accepted only by index into retrieved records, with source ID and quoted-text verification; rejected citations counted in diagnostics | Implemented, tested with a test double |
| T5 | Model answers beyond the evidence | Deterministic abstention below a calibrated relevance threshold; system prompt requires the abstention prefix; abstention correctness is in the evaluation | Implemented; live-model measurement pending |
| T6 | Malicious upload (parser exploits, zip-bomb-like PDFs, binaries disguised as text) | Type sniffing, size and page caps, encrypted PDF refusal, pure-Python parser in an isolated worker process, permanent-failure classification | Implemented; parser sandboxing (seccomp, separate task) planned for AWS |
| T7 | Credential stuffing or brute force | Argon2id, rate limits per IP and per email, uniform failure responses, audit of failures | Implemented, tested |
| T8 | Token theft by XSS | No HTML rendering of untrusted text, CSP, short TTL | Partial; httpOnly refresh cookies planned |
| T9 | Secret leakage in logs or the UI | Redaction processor; settings as `SecretStr`; no secrets in API responses | Implemented, tested |
| T10 | Duplicate or replayed ingestion jobs corrupt the index | Atomic claim, transactional version swap, idempotent chunk replacement | Implemented, tested |
| T11 | Model-initiated infrastructure mutation | No approve tool exists; the server-side state machine binds requester, arguments (hash), target state and a one-time execution ID; approver ≠ requester; live revalidation before a compare-and-swap deploy; demo environment only; deploy token in one process | Implemented, tested (incl. concurrency and changed target) |
| T12 | Tool results (logs, PR bodies, files) carrying injection | Results returned as data with explicit "untrusted" notes; tools have no side effects except the approval-gated rollback; identity never from arguments. The demo traffic deliberately writes an injection attempt into logs | Server side implemented; agent-side handling pending (Phase 3) |
| T13 | Forged or replayed tool tokens; a compromised MCP server minting tokens | EdDSA signature with only the public key on servers, per-server audience, short TTL, `jti` logged; membership re-check on DB-backed servers | Implemented, tested |
| T14 | Query or command injection through tool arguments (PromQL/LogQL, git, GitHub search qualifiers) | Server-side templates, allow-listed services and repositories, validated refs and paths, argument arrays without a shell, qualifier rejection | Implemented, tested |
