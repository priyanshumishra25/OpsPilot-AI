# Postmortem: Orders API connection pool exhaustion (2026-03-12)

Severity: SEV-2. Duration: 47 minutes. Author: Commerce Core.

## Summary

Release v2.14.0 of `orders-api` introduced a code path that opened a database transaction before
calling the Inventory service and held it open for the duration of the call. Under normal traffic,
Inventory latency of around 300 ms meant each request held a connection about 6 times longer than
before. The connection pool (`ORDERS_DB_POOL_SIZE=20`) was exhausted within 9 minutes of the deploy,
and the API returned HTTP 500 `PoolTimeoutError` for 38% of requests.

## Timeline (UTC)

- 14:02 — v2.14.0 deployed to production.
- 14:11 — `OrdersApiHigh5xxRate` alert fired.
- 14:24 — On-call correlated the alert with the deployment.
- 14:31 — Rollback to v2.13.2 approved and executed.
- 14:49 — Error rate returned to baseline.

## Root cause

A database transaction was held open across a network call to Inventory, multiplying connection hold
time. The change was in `orders/service.py` in the `create_order` function.

## Lessons and action items

1. Never hold a database transaction open across a network call. Added a lint rule.
2. Add an alert on `orders_db_pool_in_use / orders_db_pool_size > 0.9`.
3. Rollback decision took 20 minutes; the runbook now says to roll back first when errors start within
   15 minutes of a deployment.
