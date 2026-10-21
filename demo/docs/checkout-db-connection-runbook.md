# Checkout Service Runbook: Database Connection Failure

Owner: Payments Platform team. Last reviewed: 2026-08-14.

## Symptoms

- Alert `CheckoutDBConnectionErrors` fires when more than 5% of checkout requests fail with
  `DatabaseConnectionError` over a 5 minute window.
- Checkout API returns HTTP 503 with error code `CHECKOUT_DB_UNAVAILABLE`.
- The `checkout_db_pool_available` metric drops to 0.

## Preconditions

Before starting recovery, confirm the problem is not a regional outage of the managed database.
Check the status page of the `checkout-db` RDS instance. If the instance itself is down, stop and
follow the database failover procedure instead (owned by the Data Infrastructure team).

## Recovery procedure

1. Put checkout into degraded mode so new carts are queued rather than rejected:
   set the feature flag `checkout.degraded_mode` to `true`.
2. Check the connection pool usage on the `checkout-db` dashboard. If `checkout_db_pool_in_use`
   equals `checkout_db_pool_max` (default 40), the pool is exhausted.
3. Restart the checkout pods one at a time with a rolling restart:
   `opsctl restart checkout-api --strategy rolling --max-unavailable 1`.
   Do not restart all pods at once; doing so drops in-flight payments.
4. Verify that `checkout_db_pool_available` recovers above 10 within 5 minutes.
5. Disable degraded mode by setting `checkout.degraded_mode` back to `false`.
6. Replay queued carts with `opsctl checkout replay-queued-carts --since 1h`.

## If recovery fails

If the pool does not recover after the rolling restart, escalate to the Payments Platform on-call
engineer and open a SEV-2 incident. Do not increase `checkout_db_pool_max` above 60 without approval
from the Data Infrastructure team, because the database has a hard limit of 400 connections shared
across all services.

## Verification

Checkout is considered recovered when the error rate is below 0.5% for 15 consecutive minutes and
p95 latency is below 800 ms.
