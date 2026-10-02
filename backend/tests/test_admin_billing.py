from decimal import Decimal

from app.models import AuditLog, Plan
from tests.conftest import admin_login, create_admin, make_client, register


def setup(db):
    create_admin(db)
    customer = make_client()
    register(customer)
    admin = make_client()
    admin_login(admin)
    ws = customer.get("/api/v1/workspace").json()["id"]
    return customer, admin, ws


PRO = {
    "code": "pro",
    "name": "Pro",
    "monthly_price": "49.00",
    "annual_price": "490.00",
    "included_credits": 500,
    "entitlements": {"api_access": True, "export_formats": ["csv", "tally"]},
    "features": ["500 invoices"],
}


def test_create_and_update_plan(db):
    _, admin, _ = setup(db)
    resp = admin.post("/api/admin/plans", json=PRO)
    assert resp.status_code == 201, resp.text
    plan = resp.json()
    assert plan["entitlements"]["api_access"] is True
    assert plan["entitlements"]["max_file_size_mb"] == 10  # defaults filled in
    assert admin.post("/api/admin/plans", json=PRO).status_code == 409

    resp = admin.patch(f"/api/admin/plans/{plan['id']}", json={"monthly_price": "59.00"})
    assert resp.json()["monthly_price"] == "59.00"
    log = db.query(AuditLog).filter_by(action="plan.updated").one()
    assert log.summary == {
        "before": {"monthly_price": "49.00"},
        "after": {"monthly_price": "59.00"},
    }


def test_plan_validation(db):
    _, admin, _ = setup(db)
    bad_format = {**PRO, "entitlements": {"export_formats": ["docx"]}}
    resp = admin.post("/api/admin/plans", json=bad_format)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_entitlements"
    unknown_key = {**PRO, "entitlements": {"teleport": True}}
    assert admin.post("/api/admin/plans", json=unknown_key).status_code == 400
    negative = {**PRO, "monthly_price": "-1"}
    assert admin.post("/api/admin/plans", json=negative).status_code == 422
    assert admin.post("/api/admin/plans", json={**PRO, "code": "Bad Code"}).status_code == 422


def test_default_plan_rules(db):
    _, admin, _ = setup(db)
    free = db.query(Plan).filter_by(code="free").one()
    resp = admin.patch(f"/api/admin/plans/{free.id}", json={"is_active": False})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "default_plan_required"
    pro = admin.post("/api/admin/plans", json={**PRO, "is_default": True}).json()
    assert pro["is_default"] is True
    db.expire_all()
    assert db.get(Plan, free.id).is_default is False
    assert admin.patch(f"/api/admin/plans/{free.id}", json={"is_active": False}).status_code == 200


def test_price_change_does_not_rewrite_existing_subscription(db):
    customer, admin, ws = setup(db)
    pro = admin.post("/api/admin/plans", json=PRO).json()
    admin.post(
        f"/api/admin/workspaces/{ws}/subscription",
        json={"plan_id": pro["id"], "reason": "Sales deal"},
    )
    admin.patch(f"/api/admin/plans/{pro['id']}", json={"monthly_price": "99.00"})
    sub = customer.get("/api/v1/billing/subscription").json()
    assert sub["price"] == "49.00"
    assert sub["plan"]["monthly_price"] == "99.00"


def test_admin_change_plan_reallocates_credits_and_keeps_extra(db):
    customer, admin, ws = setup(db)
    admin.post(
        f"/api/admin/workspaces/{ws}/credits/grant", json={"amount": 7, "reason": "Goodwill"}
    )
    pro = admin.post("/api/admin/plans", json=PRO).json()
    resp = admin.post(
        f"/api/admin/workspaces/{ws}/subscription",
        json={"plan_id": pro["id"], "billing_interval": "year", "reason": "Upgrade request"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_name"] == "Pro"
    assert body["subscription"]["source"] == "admin"
    assert body["subscription"]["price"] == "490.00"
    assert body["usage"]["period_credits_remaining"] == 500
    assert body["usage"]["extra_credits_remaining"] == 7
    assert customer.get("/api/v1/billing/subscription").json()["entitlements"]["api_access"]
    types = [e["entry_type"] for e in customer.get("/api/v1/credits/ledger").json()["items"]]
    assert types[:3] == ["allocation", "expiry", "grant"]
    log = db.query(AuditLog).filter_by(action="subscription.plan_changed").one()
    assert log.summary["from_plan"] == "free" and log.summary["to_plan"] == "pro"


def test_grant_and_revoke_credits(db):
    customer, admin, ws = setup(db)
    resp = admin.post(
        f"/api/admin/workspaces/{ws}/credits/grant",
        json={"amount": 25, "reason": "Support compensation"},
    )
    assert resp.json()["usage"]["total_credits_remaining"] == 35
    resp = admin.post(
        f"/api/admin/workspaces/{ws}/credits/revoke",
        json={"amount": 30, "bucket": "extra", "reason": "Mistake"},
    )
    assert resp.status_code == 409
    resp = admin.post(
        f"/api/admin/workspaces/{ws}/credits/revoke",
        json={"amount": 5, "bucket": "period", "reason": "Mistake"},
    )
    assert resp.json()["usage"]["period_credits_remaining"] == 5
    ledger = admin.get(f"/api/admin/workspaces/{ws}/credits/ledger").json()
    assert ledger["items"][0]["actor_admin_id"] is not None
    actions = {a.action for a in db.query(AuditLog).all()}
    assert {"credits.granted", "credits.revoked"} <= actions
    assert customer.get("/api/v1/usage").json()["total_credits_remaining"] == 30


def test_entitlement_overrides(db):
    customer, admin, ws = setup(db)
    resp = admin.put(
        f"/api/admin/workspaces/{ws}/entitlement-overrides",
        json={
            "overrides": {"max_files_per_batch": 50, "api_access": True},
            "reason": "Pilot customer",
        },
    )
    assert resp.status_code == 200
    ent = customer.get("/api/v1/billing/subscription").json()["entitlements"]
    assert ent["max_files_per_batch"] == 50 and ent["api_access"] is True
    bad = admin.put(
        f"/api/admin/workspaces/{ws}/entitlement-overrides",
        json={"overrides": {"fly": True}, "reason": "Nope"},
    )
    assert bad.status_code == 400
    bad = admin.put(
        f"/api/admin/workspaces/{ws}/entitlement-overrides",
        json={"overrides": {"max_files_per_batch": 0}, "reason": "Nope"},
    )
    assert bad.status_code == 400


def test_billing_rbac(db):
    customer, _, ws = setup(db)
    create_admin(db, role_name="support", email="support@invoiceflow.example.com")
    support = make_client()
    admin_login(support, email="support@invoiceflow.example.com")
    assert support.get(f"/api/admin/workspaces/{ws}").status_code == 200
    assert support.get("/api/admin/plans").status_code == 200  # billing.read
    assert support.post("/api/admin/plans", json=PRO).status_code == 403
    assert (
        support.post(
            f"/api/admin/workspaces/{ws}/credits/grant", json={"amount": 5, "reason": "please"}
        ).status_code
        == 403
    )
    assert customer.get("/api/admin/plans").status_code == 401


def test_credit_packages(db):
    _, admin, _ = setup(db)
    resp = admin.post(
        "/api/admin/credit-packages", json={"name": "100 credits", "credits": 100, "price": "20.00"}
    )
    assert resp.status_code == 201
    pkg = resp.json()
    assert (
        admin.post(
            "/api/admin/credit-packages", json={"name": "zero", "credits": 0, "price": "1"}
        ).status_code
        == 422
    )
    admin.patch(f"/api/admin/credit-packages/{pkg['id']}", json={"is_active": False})
    assert make_client().get("/api/v1/credit-packages").json() == []
    assert Decimal(pkg["price"]) == Decimal("20.00")
