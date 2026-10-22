# Contributing

1. Backend: `cd backend && uv sync`. Run `uv run ruff check src tests`, `uv run ruff format src tests`,
   `uv run mypy` (strict) and `uv run pytest` (Docker required for testcontainers).
2. Frontend: `cd frontend && npm ci`. Run `npm run lint`, `npm run typecheck` and `npm test`. For E2E,
   start the stack, then run `npx playwright test`.
3. Schema changes: edit `db/models.py`, run `uv run alembic revision --autogenerate -m "..."`, review
   the generated file, and confirm `alembic upgrade head && alembic downgrade -1 && alembic upgrade head
   && alembic check` passes.
4. Retrieval changes: run the evaluation with `--compare-baseline`. Update the baseline
   (`--update-baseline`) only in the same PR that explains why the numbers changed.
5. Test doubles live in `backend/tests/fakes.py` and must never be imported by application code.
6. Don't commit secrets, `.env`, Terraform state, or evaluation run artifacts.

## Troubleshooting

- **`ModuleNotFoundError: opspilot` on macOS** when the repository lives in an iCloud-synced folder: macOS can
  set the `hidden` flag on files in `.venv`, and Python skips hidden `.pth` files. Pytest and Alembic add `src`
  to the path explicitly; for ad-hoc commands use `PYTHONPATH=src` or `chflags nohidden .venv/lib/python3.12/site-packages/*.pth`.
- **Slow or "hanging" tests on a laptop**: the test suite starts its own Postgres and Redis containers. With the
  full demo stack also running, a 16 GB machine can start swapping heavily. Either stop the stack, or reuse its
  services with a separate database:
  `docker compose exec postgres createdb -U opspilot opspilot_test` and run pytest with
  `OPSPILOT_TEST_DATABASE_URL=postgresql+asyncpg://opspilot:opspilot-local@localhost:5432/opspilot_test`
  `OPSPILOT_TEST_REDIS_URL=redis://localhost:6379/15`. Each test has a 300 s hard limit (thread method), so a
  real hang fails with a stack dump instead of blocking CI.
