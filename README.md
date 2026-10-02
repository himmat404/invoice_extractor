# InvoiceFlow

AI-powered invoice extraction, validation, and accounting export platform.

- **Product specification:** [`docs/SPECIFICATION.md`](docs/SPECIFICATION.md)
- **Build checklist / progress:** [`CHECKLIST.md`](CHECKLIST.md)

## Repository layout

```
backend/            FastAPI API, workers, migrations (Python 3.11)
  app/api/customer  Customer API  → /api/v1/*
  app/api/admin     Admin console API → /api/admin/*  (separate auth + RBAC)
  app/models        SQLAlchemy models
  app/services      Business logic (auth, audit, email, permissions…)
  alembic/          Database migrations
  tests/            pytest suite (runs against a real Postgres)
frontend/           Customer app and admin console (coming in Phase 6 / 9)
docs/               Specification and design docs
infra/              Local infrastructure helpers
```

## Local development

```bash
# 1. Infrastructure (Postgres, Redis, MinIO, Mailpit)
docker compose up -d

# 2. Backend
cd backend
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run python -m app.cli seed-plans      # default plans + credit packages
uv run python -m app.cli create-admin --email you@example.com --full-name "Your Name"
uv run uvicorn app.main:app --reload
```

- API docs: http://localhost:8000/docs
- Mailpit (verification / reset emails): http://localhost:8025

### Tests and lint

```bash
cd backend
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests use the `invoiceflow_test` database (created automatically by docker compose) and
rebuild its schema from migrations on every run.

## Security model (summary)

- Customer and admin accounts are separate tables with separate session stores and cookies.
- Sessions are opaque random tokens; only their SHA-256 hash is stored. Cookies are `HttpOnly`,
  `SameSite=Lax`, and unsafe requests must carry an `X-Requested-With` header (CSRF defence).
- Every customer query is scoped to the workspace resolved from the user's memberships.
- Admin actions are written to an append-only `audit_logs` table (UPDATE/DELETE blocked by trigger).
- Errors use a consistent envelope `{"error": {code, message, request_id, details?}}` and never
  expose internals.
