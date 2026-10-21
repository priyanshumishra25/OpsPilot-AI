# Service Architecture

## Services

| Service | Owner | Language | Datastore |
|---|---|---|---|
| orders-api | Commerce Core | Python (FastAPI) | orders-db (PostgreSQL 16) |
| checkout-api | Payments Platform | Python (FastAPI) | checkout-db (PostgreSQL 16) |
| inventory | Commerce Core | Go | inventory-db (PostgreSQL 16) |

## Request flow

A customer order flows through the system as follows: the web frontend calls `checkout-api` to take
payment; on success, `checkout-api` calls `orders-api` to create the order; `orders-api` calls the
`inventory` service to reserve stock.

## Dependencies and timeouts

- `checkout-api` → payment provider: 10 second timeout, 2 retries with exponential backoff.
- `checkout-api` → `orders-api`: 5 second timeout, no retries (order creation is not idempotent
  without an idempotency key).
- `orders-api` → `inventory`: 2 second timeout.

## Observability

All services export Prometheus metrics on `/metrics` and write structured JSON logs. Each request
carries a `trace_id` that is propagated across service calls.
