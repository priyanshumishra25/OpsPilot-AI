# Incident demo (Phase 2: MCP tools + human approval)

Everything below runs against the local stack. The services, telemetry, git history and deployments are
real processes and data; only the *environment* is fictional (the `demo` workspace and services). The Claude
agent that drives these tools autonomously arrives in Phase 3; until then the steps are driven with the
independent MCP Inspector client, which also shows that the servers are standard MCP.

```bash
docker compose --profile demo up -d --build
```

1. **Trigger the incident** (admin): `POST /api/v1/demo/scenarios/orders-bad-deploy/trigger`. The control plane
   deploys `orders-api` v2.16.0 (commit `4f6e9a5`), whose code holds a DB connection across a slow batched
   inventory call. Under the steady synthetic load (12 req/s, pool of 20) the pool saturates within about
   30 seconds.
2. **Observe** (`observability`): `get_service_health` shows unhealthy on v2.16.0 (≈ 20–30% HTTP 500
   `PoolTimeoutError`, p95 ≈ 4–5 s, pool 100%). `search_service_logs level=error` shows "timed out waiting 3000
   ms for a database connection (pool 20/20 in use)". `get_deployment_timeline` places v2.16.0 at the start of
   the spike.
3. **Code** (`github`): `get_commit_diff` on `4f6e9a5` shows `reserve_batch` moved inside the transaction.
4. **Docs** (`knowledge`): `search_knowledge` returns the March postmortem with the same root-cause pattern, and
   the runbook guidance to roll back first when errors start within 15 minutes of a deploy.
5. **Remediate** (`operations`): `get_available_rollback_versions` shows v2.15.0 eligible and v2.14.0
   ineligible (previously rolled back). `request_deployment_rollback` creates a PENDING approval;
   `execute_approved_rollback` is refused until a human approves.
6. **Approve** (`sre@opspilot.demo`): `POST /api/v1/approvals/{id}/approve`. The engineer who requested it cannot
   approve their own request.
7. **Execute**: the rollback deploys v2.15.0 with compare-and-swap, verifies the running version, and records
   the result. A second execution is refused.
8. **Verify**: within about a minute `get_error_rate window_minutes=1` returns 0. The 5-minute health status
   recovers a few minutes later.

`POST /api/v1/demo/reset` restores the seeded state. If you reset (or anything else deploys) between approval and
execution, execution aborts with "target changed since approval". That happened during development and is
covered by `test_target_changed_after_approval_aborts`.
