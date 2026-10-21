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
