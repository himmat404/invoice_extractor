import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models import AuditLog
from tests.conftest import PASSWORD, admin_login, create_admin, make_client, register


def test_admin_login_and_me(db):
    create_admin(db)
    c = make_client()
    admin_login(c)
    me = c.get("/api/admin/auth/me").json()
    assert me["admin"]["role"]["name"] == "super_admin"
    assert "*" in me["permissions"]
    actions = [a.action for a in db.query(AuditLog).all()]
    assert "admin.login" in actions


def test_admin_cookie_scoped_to_admin_path(db):
    create_admin(db)
    c = make_client()
    resp = c.post(
        "/api/admin/auth/login",
        json={"email": "root@invoiceflow.example.com", "password": PASSWORD},
    )
    assert "path=/api/admin" in resp.headers["set-cookie"].lower()
    # an admin session is not a customer session
    assert c.get("/api/v1/me").status_code == 401


def test_customer_credentials_cannot_log_into_admin():
    register(make_client())
    c = make_client()
    resp = c.post("/api/admin/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert resp.status_code == 401


def test_failed_admin_login_is_audited(db):
    create_admin(db)
    c = make_client()
    resp = c.post(
        "/api/admin/auth/login",
        json={"email": "root@invoiceflow.example.com", "password": "bad-password"},
    )
    assert resp.status_code == 401
    failures = db.query(AuditLog).filter_by(action="admin.login", outcome="failure").all()
    assert len(failures) == 1


def test_suspend_and_reactivate_customer(db):
    create_admin(db)
    customer = make_client()
    user = register(customer)
    admin = make_client()
    admin_login(admin)

    listing = admin.get("/api/admin/customers", params={"q": "ada"}).json()
    assert listing["total"] == 1

    resp = admin.post(f"/api/admin/customers/{user['id']}/suspend", json={"reason": "Fraud check"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"
    assert customer.get("/api/v1/me").status_code == 401  # sessions revoked
    login = make_client().post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    assert login.status_code == 403

    resp = admin.post(f"/api/admin/customers/{user['id']}/reactivate", json={"reason": "Cleared"})
    assert resp.json()["status"] == "active"
    logs = admin.get("/api/admin/audit-logs", params={"action": "customer."}).json()
    assert {i["action"] for i in logs["items"]} == {"customer.suspended", "customer.reactivated"}
    assert logs["items"][0]["summary"]["reason"] == "Cleared"


def test_rbac_blocks_missing_permission(db):
    create_admin(db, role_name="support", email="support@invoiceflow.example.com")
    user = register(make_client())
    c = make_client()
    admin_login(c, email="support@invoiceflow.example.com")
    assert c.get("/api/admin/customers").status_code == 200  # users.read
    resp = c.post(f"/api/admin/customers/{user['id']}/suspend", json={"reason": "nope"})
    assert resp.status_code == 403
    assert c.get("/api/admin/audit-logs").status_code == 403
    assert c.put("/api/admin/settings/max_upload_mb", json={"value": 20}).status_code == 403


def test_system_settings_are_audited(db):
    create_admin(db)
    c = make_client()
    admin_login(c)
    resp = c.put(
        "/api/admin/settings/uploads.max_file_mb", json={"value": 25, "description": "Max"}
    )
    assert resp.status_code == 200
    c.put("/api/admin/settings/uploads.max_file_mb", json={"value": 30})
    assert c.get("/api/admin/settings/uploads.max_file_mb").json()["value"] == 30
    logs = c.get("/api/admin/audit-logs", params={"action": "system_setting"}).json()
    assert logs["items"][0]["summary"] == {"before": 25, "after": 30}
    assert c.put("/api/admin/settings/Bad Key", json={"value": 1}).status_code == 422


def test_manage_roles_and_admins(db):
    root = create_admin(db)
    c = make_client()
    admin_login(c)
    resp = c.post(
        "/api/admin/roles",
        json={"name": "auditor", "permissions": ["audit.read", "users.read"]},
    )
    assert resp.status_code == 201
    role_id = resp.json()["id"]
    bad = c.post("/api/admin/roles", json={"name": "bad", "permissions": ["launch.missiles"]})
    assert bad.status_code == 400
    resp = c.post(
        "/api/admin/admins",
        json={
            "email": "Auditor@InvoiceFlow.example.com",
            "full_name": "Audrey",
            "password": PASSWORD,
            "role_id": role_id,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "auditor@invoiceflow.example.com"
    # cannot change own role/status
    resp = c.patch(f"/api/admin/admins/{root.id}", json={"status": "suspended"})
    assert resp.status_code == 400
    auditor = make_client()
    admin_login(auditor, email="auditor@invoiceflow.example.com")
    assert auditor.get("/api/admin/audit-logs").status_code == 200
    assert auditor.get("/api/admin/roles").status_code == 403


def test_deactivating_admin_revokes_their_sessions(db):
    create_admin(db)
    other = create_admin(db, role_name="finance", email="fin@invoiceflow.example.com")
    root = make_client()
    admin_login(root)
    fin = make_client()
    admin_login(fin, email="fin@invoiceflow.example.com")
    assert fin.get("/api/admin/auth/me").status_code == 200
    root.patch(f"/api/admin/admins/{other.id}", json={"status": "suspended"})
    assert fin.get("/api/admin/auth/me").status_code in (401, 403)


def test_audit_log_is_append_only(db):
    create_admin(db)
    admin_login(make_client())
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE audit_logs SET action = 'tampered'"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM audit_logs"))
    db.rollback()
