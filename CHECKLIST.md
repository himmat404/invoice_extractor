# InvoiceFlow — Build Checklist

Master tracker for building the platform described in [`docs/SPECIFICATION.md`](docs/SPECIFICATION.md).
Section references (e.g. `§4.3`) point to that document.

**Legend:** `[x]` done · `[~]` in progress / partial · `[ ]` not started

## Tech stack (decided in Phase 0)

| Layer | Choice |
|---|---|
| Backend API | Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2 |
| Database | PostgreSQL 16 (money as `NUMERIC`, never float) |
| Queue / workers | Redis 7 + background worker (asynchronous extraction) |
| Object storage | S3-compatible (MinIO locally) behind a storage interface; local-disk driver for dev/tests |
| AI | Provider abstraction; Gemini first, fallback routing, server-side keys only |
| Customer app | React + TypeScript + Vite + Tailwind (`frontend/customer`) |
| Admin console | Separate React + TypeScript + Vite app (`frontend/admin`) |
| Auth | Opaque, hashed, revocable sessions in httpOnly cookies; separate customer and admin session stores |
| Infra | Docker Compose for local dev (Postgres, Redis, MinIO, Mailpit) |

---

## Phase 0 — Project foundation
- [x] Repository layout (`backend/`, `frontend/`, `docs/`, `infra/`)
- [x] Spec checked in at `docs/SPECIFICATION.md`
- [x] Docker Compose: Postgres, Redis, MinIO, Mailpit
- [x] Backend project (uv / `pyproject.toml`), lint (ruff), tests (pytest)
- [x] Settings via environment variables (`.env.example`)
- [x] Health endpoint, request/correlation IDs, consistent error envelope (§21.5, §15)
- [x] CI workflow (lint + tests) — GitHub Actions (`.github/workflows/backend.yml`)

## Phase 1 — Core backend: identity, tenancy, admin, audit
- [x] Database base model (UUID PKs, UTC timestamps), Alembic migrations
- [x] `users`, `workspaces`, `memberships` (§11)
- [x] Customer registration, login, logout (§4.1)
- [x] Email verification and password reset (hashed single-use tokens, expiry)
- [x] Secure session management (hashed opaque tokens, revocation, expiry, CSRF header check)
- [x] Profile and preferences (timezone, locale, currency)
- [x] Workspace settings (company info, tax ID)
- [x] `admin_users`, `admin_roles`, permissions — separate admin auth (§5.1)
- [x] `audit_logs` for admin actions, `activity_events` for workspace activity (§21.4)
- [x] `system_settings` key/value store
- [x] Email service abstraction (console + SMTP drivers)
- [x] Tenant isolation helpers (every customer query scoped by workspace)
- [x] Rate limiting on auth endpoints
- [x] Admin bootstrap CLI (create first super-admin)
- [x] Tests for auth, isolation, admin RBAC

## Phase 2 — Plans, entitlements, credits, usage
- [ ] `plans` with entitlements (credits/period, file size, bulk limits, export formats, retention, API/webhook access) (§5.5)
- [ ] `subscriptions` (status, period start/end, cancel at period end)
- [ ] `credit_ledger` (allocation, purchase, grant, adjustment, consumption, reversal) (§5.7)
- [ ] `credit_packages`
- [ ] `usage_records`
- [ ] Backend entitlement enforcement service (§14)
- [ ] Free plan auto-assigned on registration
- [ ] Customer endpoints: current plan, usage, credits
- [ ] Admin endpoints: plan CRUD, grant/revoke credits (audited)

## Phase 3 — Upload, storage, processing jobs
- [ ] Storage interface (local + S3), private objects, signed URLs (§8)
- [ ] `invoice_files`, `invoices`, `extraction_jobs`, `batches`, `batch_items`
- [ ] Upload endpoint: type/size/empty validation, magic-byte check, SHA-256 hash (§4.3)
- [ ] PDF, JPG/JPEG, PNG; HEIC conversion where available
- [ ] ZIP upload with safe extraction limits (zip-bomb, path traversal) (§21.3)
- [ ] Credit/subscription check before queueing (§19 step 5)
- [ ] Job queue + worker, idempotent job lifecycle, unique job IDs
- [ ] Processing status model (§10)
- [ ] Cancel queued jobs; retry failed jobs without double charging

## Phase 4 — AI orchestration
- [ ] `model_providers`, `model_configurations`, `provider_credentials` (encrypted at rest) (§5.3)
- [ ] Prompt templates with versions
- [ ] Provider adapter interface; Gemini adapter with structured output schema (§9)
- [ ] Primary/fallback routing by admin-defined order and retryable error classes
- [ ] Timeouts, retries, rate-limit handling
- [ ] Safe parsing of model response into canonical invoice schema; absent fields → null (§4.4)
- [ ] Record provider, model, prompt version, extraction version per job
- [ ] Token usage + estimated cost recording (§5.8)
- [ ] Credit consumed only on success (§5.7)
- [ ] Fake provider for tests/dev

## Phase 5 — Validation, confidence, duplicates
- [ ] Validation engine: required fields, dates, numerics, currency codes (§4.6)
- [ ] GSTIN/tax ID format + checksum
- [ ] Line total, subtotal, tax, discount, grand-total reconciliation
- [ ] Validation states: Passed / Warning / Needs Review / Failed
- [ ] `validation_results` persisted per invoice
- [ ] Field-level confidence storage; "Unavailable" when not supplied (§21.2)
- [ ] Admin-configurable confidence thresholds (versioned)
- [ ] `duplicate_detection_rules`, `duplicate_match_results` (§21.1)
- [ ] File hash, exact, multi-field and similarity rules; workspace-scoped only
- [ ] Customer decision: keep both / mark duplicate / cancel — recorded in activity

## Phase 6 — Customer app (frontend)
- [ ] App shell, routing, auth pages (register, login, verify, forgot/reset)
- [ ] Landing, pricing, privacy, terms pages
- [ ] Dashboard with metrics and quick actions (§4.2)
- [ ] Upload (drag-and-drop, multi-file, progress, error states)
- [ ] Processing queue / batch details
- [ ] Invoice review: side-by-side preview, editable fields & line items, confidence highlights, warnings, approve (§4.5)
- [ ] Invoice history: search, filters, sort, details, reprocess, download, delete (§4.7)
- [ ] Duplicate review page
- [ ] Account, workspace, notification settings
- [ ] Responsive layout

## Phase 7 — Exports
- [ ] Canonical → export mapping engine (§4.9)
- [ ] CSV, XLSX, JSON, PDF summary
- [ ] Tally, QuickBooks, Zoho Books, Xero profiles (per current import docs)
- [ ] Export validation with missing-field warnings
- [ ] Column selection/order, date & decimal formats
- [ ] `export_profiles`, `exports` (linked to included invoices), history + re-download
- [ ] Bulk/ZIP export
- [ ] Customer exports UI

## Phase 8 — Billing & payments
- [ ] Payment gateway interface + adapters (Stripe, Razorpay) (§14, §18)
- [ ] Checkout for subscriptions and credit packages
- [ ] Webhook handling with signature verification and idempotency
- [ ] `payments`, `billing_invoices` (separate from customer invoices) (§5.6)
- [ ] Upgrade/downgrade/cancel; grace period; renewals
- [ ] Autopay / mandate lifecycle recording
- [ ] `coupons`, `coupon_redemptions` (§5.11)
- [ ] Customer billing & usage pages

## Phase 9 — Admin console (frontend + remaining APIs)
- [ ] Admin login, shell, RBAC-aware navigation
- [ ] Dashboard: users, revenue, AI cost, margin, success rate, trends (§5.2)
- [ ] Users & workspaces: details, suspend/reactivate, credits, plan change (§5.4)
- [ ] Plans & entitlements, credit packages, coupons
- [ ] Subscriptions, payments, gateway configuration
- [ ] AI providers, models, fallback order, credentials, prompts, test run
- [ ] AI cost reports
- [ ] Processing jobs monitor: inspect, retry
- [ ] Export templates, system settings, notification templates
- [ ] Audit log search, admin roles & permissions

## Phase 10 — Notifications & reporting
- [ ] `notifications` (in-app + email) for processing, review, credits, billing, exports (§6)
- [ ] Admin operational alerts
- [ ] Customer reports: supplier, tax, monthly totals, spend; CSV/XLSX download (§7)
- [ ] Multi-currency reporting with `exchange_rates` snapshots (§21.9)

## Phase 11 — Directories & preferences
- [ ] Supplier directory with confirmable matching (§21.8)
- [ ] Item directory, aliases, supplier-specific mappings (§21.15)
- [ ] Item CSV/XLSX import with dry-run, conflicts, transactional commit (§21.16)
- [ ] Learning from confirmed corrections; snapshots on approval
- [ ] Invoice templates and saved preferences (§21.7)

## Phase 12 — Customer API & webhooks
- [ ] Versioned public API `/api/public/v1` with OpenAPI docs (§21.5)
- [ ] `api_credentials` (hashed, scoped, revocable, last-used)
- [ ] Rate limits, quotas, idempotency keys, pagination
- [ ] `api_request_logs` (secret-safe)
- [ ] Event outbox, `webhook_endpoints`, `webhook_deliveries` (§21.6)
- [ ] HMAC-signed deliveries with timestamp, retries with backoff, SSRF protection
- [ ] API keys & webhooks UI

## Phase 13 — Scheduling, support, status, flags
- [ ] `scheduled_exports`, `scheduled_export_runs` with idempotent, timezone-aware scheduler (§21.10)
- [ ] `support_cases`, `support_notes`, time-limited audited support access (§21.11)
- [ ] Status page + `status_incidents` (§21.12)
- [ ] `feature_flags`, assignments, audit; never bypass entitlements (§21.14)

## Phase 14 — Security hardening & operations
- [ ] Encryption at rest for secrets (provider keys, gateway keys, webhook secrets)
- [ ] Malicious-file protection (AV scanning hook)
- [ ] Account deletion + data export workflows; retention jobs (§8)
- [ ] Backup & DR runbook, `backup_records`, `recovery_test_records`, RPO/RTO (§21.13)
- [ ] Monitoring, structured logs, metrics
- [ ] Production Dockerfiles & deployment guide
- [ ] Security review pass

---

## Progress log

| Date | Phase | Notes |
|---|---|---|
| 2026-10-02 | 0, 1 | Repo scaffold, Docker Compose, CI; backend foundation: auth, sessions, email verification / reset, workspaces, admin RBAC, append-only audit log, activity history, system settings. 33 tests passing. |
