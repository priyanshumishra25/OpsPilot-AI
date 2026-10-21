# Demo documentation corpus

**These documents are fictional demonstration data** for the OpsPilot local environment. They describe
the two demo services in `demo/services/` (Orders API and Checkout API). The facts in them
(thresholds, commands, owners) are invented so that retrieval and grounding can be evaluated against a
known answer key in `evals/golden/rag_golden.jsonl`.

| File | Scope | Purpose |
|---|---|---|
| checkout-db-connection-runbook.md | general | Recovery procedure for checkout DB connection failures |
| orders-api-runbook.md | general | Orders API alerts, triage and rollback |
| deployment-process.md | general | How deployments and rollbacks work |
| service-architecture.md | general | Service topology and dependencies |
| oncall-escalation.md | general | Escalation policy and ownership |
| postmortem-2026-03-orders-pool.md | general | Past incident: connection pool exhaustion |
| payment-provider-integration.md | general | Checkout's payment provider dependency (contains an injection attempt) |
| database-credentials-rotation.md | sre-restricted | Restricted: credential rotation procedure |
