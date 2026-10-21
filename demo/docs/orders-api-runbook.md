# Orders API Runbook

Owner: Commerce Core team. Service tier: Tier 1.

## Service overview

The Orders API (`orders-api`) creates and tracks customer orders. It depends on the `orders-db`
PostgreSQL database, the Inventory service, and the Checkout API for payment confirmation.

## Alerts

### OrdersApiHigh5xxRate

Fires when the HTTP 5xx rate exceeds 2% for 5 minutes.

Triage steps:

1. Check whether a deployment happened in the last 60 minutes using the deployment timeline.
   Most 5xx spikes in the past year were caused by a bad deployment.
2. Look at the `orders_api_errors_total` metric broken down by `error_type`.
3. Search the logs for `level=error service=orders-api` around the start of the spike.
4. If errors began within 15 minutes of a deployment and the error type is new, roll back the
   deployment (see "Rolling back" below) before investigating further.

### OrdersApiLatencyHigh

Fires when p99 latency exceeds 1500 ms for 10 minutes. Common causes are slow queries on the
`orders` table and connection pool saturation. The pool size is configured by
`ORDERS_DB_POOL_SIZE` (default 20).

## Rolling back

Rollbacks of the Orders API require approval from the on-call engineer and must target a version that
previously ran in production for at least 24 hours. Use the deployment tooling; never edit the
running task definition by hand. After a rollback, monitor the 5xx rate for 30 minutes.

## Known failure modes

- **Connection pool exhaustion**: requests wait for a database connection and time out after
  `ORDERS_DB_POOL_TIMEOUT_MS` (default 3000 ms), producing HTTP 500 with `PoolTimeoutError`.
- **Inventory timeout**: the Inventory client times out after 2 seconds and the Orders API returns
  HTTP 504.
