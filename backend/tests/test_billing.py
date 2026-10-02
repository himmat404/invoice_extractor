import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.errors import PermissionDeniedError
from app.models import (
    BillingInterval,
    CreditBucket,
    CreditLedgerEntry,
    LedgerEntryType,
    Plan,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)
from app.models.base import utcnow
from app.services import credits
from app.services.credits import InsufficientCreditsError, Reference
from app.services.entitlements import PlanEntitlements, require_feature, validate_entitlements
from app.services.subscriptions import (
    SubscriptionInactiveError,
    add_interval,
    check_can_process,
    current_subscription,
    ensure_current_period,
    start_subscription,
)
from tests.conftest import make_client, register


def workspace_id(client) -> uuid.UUID:
    return uuid.UUID(client.get("/api/v1/workspace").json()["id"])


def make_plan(db, code="pro", credits_=100, **entitlements) -> Plan:
    plan = Plan(
        code=code,
        name=code.title(),
        monthly_price=Decimal("49.00"),
        annual_price=Decimal("490.00"),
        included_credits=credits_,
        entitlements=validate_entitlements(entitlements),
    )
    db.add(plan)
    db.commit()
    return plan


def consume(db, ws, key, overage=False):
    sub = current_subscription(db, ws)
    entry = credits.consume_credit(
        db,
        ws,
        idempotency_key=key,
        reference=Reference("job", key),
        subscription_id=sub.id if sub else None,
        overage_allowed=overage,
    )
    db.commit()
    return entry


def assert_ledger_consistent(db, ws):
    account = credits.get_account(db, ws)
    total = db.scalar(
        select(func.coalesce(func.sum(CreditLedgerEntry.amount), 0)).where(
            CreditLedgerEntry.workspace_id == ws,
            CreditLedgerEntry.bucket != CreditBucket.OVERAGE,
        )
    )
    assert total == account.total


# --- registration & customer endpoints ---------------------------------------------------------


def test_registration_assigns_default_plan_with_credits(client, db):
    register(client)
    sub = client.get("/api/v1/billing/subscription").json()
    assert sub["plan"]["code"] == "free"
    assert sub["status"] == "active"
    assert sub["next_billing_date"] is None  # free plans aren't billed
    assert sub["entitlements"]["max_files_per_batch"] == 1
    usage = client.get("/api/v1/usage").json()
    assert usage["included_credits"] == 10
    assert usage["total_credits_remaining"] == 10
    ledger = client.get("/api/v1/credits/ledger").json()
    assert ledger["items"][0]["entry_type"] == "allocation"
    assert "actor_admin_id" not in ledger["items"][0]


def test_public_plans_and_packages_need_no_auth(db):
    make_plan(db, "visible")
    hidden = make_plan(db, "hidden")
    hidden.is_public = False
    retired = make_plan(db, "retired")
    retired.is_active = False
    db.commit()
    codes = [p["code"] for p in make_client().get("/api/v1/plans").json()]
    assert "visible" in codes and "hidden" not in codes and "retired" not in codes
    assert make_client().get("/api/v1/credit-packages").status_code == 200


def test_prices_serialize_as_exact_decimals(db):
    make_plan(db, "exact")
    plan = next(p for p in make_client().get("/api/v1/plans").json() if p["code"] == "exact")
    assert plan["monthly_price"] == "49.00"


def test_workspace_without_subscription_gets_default_lazily(db):
    from app.models import Workspace

    ws = Workspace(name="Legacy")
    db.add(ws)
    db.commit()
    sub = ensure_current_period(db, ws.id)
    db.commit()
    assert sub.plan.code == "free"
    assert credits.get_account(db, ws.id).period_balance == 10


# --- credits -----------------------------------------------------------------------------------


def test_consumption_uses_period_then_extra_and_is_idempotent(client, db):
    register(client)
    ws = workspace_id(client)
    credits.add_extra_credits(db, ws, 2, entry_type=LedgerEntryType.GRANT, description="bonus")
    db.commit()
    buckets = [consume(db, ws, f"job-{i}").bucket for i in range(12)]
    assert buckets == [CreditBucket.PERIOD] * 10 + [CreditBucket.EXTRA] * 2
    # retrying the same job does not charge again
    assert consume(db, ws, "job-0").bucket == CreditBucket.PERIOD
    with pytest.raises(InsufficientCreditsError):
        consume(db, ws, "job-13")
    db.rollback()
    assert credits.get_account(db, ws).total == 0
    assert_ledger_consistent(db, ws)


def test_overage_when_allowed(client, db):
    register(client)
    ws = workspace_id(client)
    for i in range(10):
        consume(db, ws, f"j{i}")
    entry = consume(db, ws, "over", overage=True)
    assert entry.bucket == CreditBucket.OVERAGE
    assert credits.get_account(db, ws).total == 0
    usage = client.get("/api/v1/usage").json()
    assert usage["credits_consumed"] == 10
    assert usage["overage_consumed"] == 1


def test_reversal_returns_credit_once(client, db):
    register(client)
    ws = workspace_id(client)
    consume(db, ws, "job-x")
    assert credits.reverse_consumption(db, ws, "job-x") is True
    assert credits.reverse_consumption(db, ws, "job-x") is False
    assert credits.reverse_consumption(db, ws, "unknown") is False
    db.commit()
    assert credits.get_account(db, ws).period_balance == 10
    assert client.get("/api/v1/usage").json()["credits_consumed"] == 0
    assert_ledger_consistent(db, ws)


def test_reversal_cannot_cross_workspaces(db):
    a, b = make_client(), make_client()
    register(a, email="a@example.com")
    register(b, email="b@example.com")
    ws_a, ws_b = workspace_id(a), workspace_id(b)
    consume(db, ws_a, "job-a")
    assert credits.reverse_consumption(db, ws_b, "job-a") is False


def test_concurrent_consumption_never_overspends(client, db):
    register(client)
    ws = workspace_id(client)
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        with SessionLocal() as session:
            try:
                credits.consume_credit(
                    session,
                    ws,
                    idempotency_key=f"c-{i}",
                    reference=Reference("job", str(i)),
                    subscription_id=None,
                    overage_allowed=False,
                )
                session.commit()
                outcome = "ok"
            except InsufficientCreditsError:
                session.rollback()
                outcome = "denied"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 10
    assert results.count("denied") == 6
    db.expire_all()
    assert credits.get_account(db, ws).total == 0
    assert_ledger_consistent(db, ws)


def test_check_can_process(client, db):
    register(client)
    ws = workspace_id(client)
    assert check_can_process(db, ws, count=10).available_credits == 10
    with pytest.raises(InsufficientCreditsError) as exc:
        check_can_process(db, ws, count=11)
    assert exc.value.status_code == 402
    sub = current_subscription(db, ws)
    sub.status = SubscriptionStatus.CANCELED
    db.commit()
    # A canceled current subscription is replaced by the default plan lazily
    assert check_can_process(db, ws).subscription.plan.code == "free"


# --- billing periods ---------------------------------------------------------------------------


def test_add_interval_clamps_month_end():
    jan31 = datetime(2027, 1, 31, 12, tzinfo=UTC)
    assert add_interval(jan31, BillingInterval.MONTH) == datetime(2027, 2, 28, 12, tzinfo=UTC)
    assert add_interval(jan31, BillingInterval.YEAR) == datetime(2028, 1, 31, 12, tzinfo=UTC)
    leap = datetime(2028, 2, 29, tzinfo=UTC)
    assert add_interval(leap, BillingInterval.YEAR) == datetime(2029, 2, 28, tzinfo=UTC)


def _age_subscription(db, ws, days: int) -> Subscription:
    sub = current_subscription(db, ws)
    sub.current_period_start = utcnow() - timedelta(days=days + 30)
    sub.current_period_end = utcnow() - timedelta(days=days)
    db.commit()
    return sub


def test_free_plan_rolls_over_and_expires_unused_credits(client, db):
    register(client)
    ws = workspace_id(client)
    for i in range(3):
        consume(db, ws, f"r{i}")
    credits.add_extra_credits(db, ws, 5, entry_type=LedgerEntryType.GRANT, description="bonus")
    db.commit()
    _age_subscription(db, ws, days=95)  # idle for ~3 periods
    sub = ensure_current_period(db, ws)
    db.commit()
    assert sub.current_period_start <= utcnow() < sub.current_period_end
    account = credits.get_account(db, ws)
    assert (account.period_balance, account.extra_balance) == (10, 5)
    entries = db.scalars(
        select(CreditLedgerEntry.entry_type).where(CreditLedgerEntry.workspace_id == ws)
    ).all()
    assert entries.count(LedgerEntryType.ALLOCATION) == 2  # skipped periods allocate once
    assert entries.count(LedgerEntryType.EXPIRY) == 1
    assert_ledger_consistent(db, ws)


def test_gateway_subscription_grace_then_expiry(client, db):
    register(client)
    ws = workspace_id(client)
    pro = make_plan(db)
    start_subscription(db, ws, pro, source=SubscriptionSource.GATEWAY)
    db.commit()

    _age_subscription(db, ws, days=1)  # inside 3-day grace period
    sub = ensure_current_period(db, ws)
    db.commit()
    assert sub.status == SubscriptionStatus.PAST_DUE
    assert check_can_process(db, ws).subscription.plan.code == "pro"

    _age_subscription(db, ws, days=5)  # grace exhausted
    sub = ensure_current_period(db, ws)
    db.commit()
    assert sub.plan.code == "free"
    statuses = db.scalars(select(Subscription.status).where(Subscription.workspace_id == ws))
    assert SubscriptionStatus.EXPIRED in list(statuses)


def test_cancel_at_period_end_falls_back_to_default(client, db):
    register(client)
    ws = workspace_id(client)
    start_subscription(db, ws, make_plan(db), source=SubscriptionSource.ADMIN)
    db.commit()
    sub = current_subscription(db, ws)
    sub.cancel_at_period_end = True
    db.commit()
    _age_subscription(db, ws, days=0)
    assert ensure_current_period(db, ws).plan.code == "free"


def test_inactive_plan_cannot_be_started(client, db):
    register(client)
    plan = make_plan(db)
    plan.is_active = False
    db.commit()
    from app.core.errors import AppError

    with pytest.raises(AppError):
        start_subscription(db, workspace_id(client), plan)


def test_subscription_inactive_error_is_payment_required():
    assert SubscriptionInactiveError("x").status_code == 402


# --- entitlements ------------------------------------------------------------------------------


def test_require_feature():
    with pytest.raises(PermissionDeniedError) as exc:
        require_feature(PlanEntitlements(), "api_access")
    assert exc.value.code == "plan_feature_unavailable"
    require_feature(PlanEntitlements(api_access=True), "api_access")
    with pytest.raises(ValueError):
        require_feature(PlanEntitlements(), "max_file_size_mb")


def test_record_usage_is_idempotent_and_counted(client, db):
    from app.services.subscriptions import record_usage

    register(client)
    ws = workspace_id(client)
    sub = current_subscription(db, ws)
    assert record_usage(db, ws, "invoice_processed", subscription=sub, idempotency_key="u1")
    assert record_usage(db, ws, "invoice_processed", subscription=sub, idempotency_key="u1") is None
    record_usage(db, ws, "export_generated", subscription=sub)
    db.commit()
    assert client.get("/api/v1/usage").json()["invoices_processed"] == 1
