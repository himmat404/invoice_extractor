from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import CSRF, PASSWORD, last_token, make_client, register


def test_register_creates_user_workspace_and_session(client):
    user = register(client, workspace_name="Acme Ltd")
    assert user["email"] == "ada@example.com"
    assert user["is_verified"] is False
    me = client.get("/api/v1/me").json()
    assert me["workspace"]["name"] == "Acme Ltd"
    assert me["role"] == "owner"


def test_register_normalizes_email_and_rejects_duplicates(client):
    register(client, email="  Ada@Example.COM ")
    resp = make_client().post(
        "/api/v1/auth/register",
        json={"email": "ada@example.com", "password": PASSWORD, "full_name": "Other"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "email_taken"


def test_register_validates_password_length(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "a@example.com", "password": "short", "full_name": "A"},
    )
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "validation_error"
    assert body["request_id"]


def test_login_logout_flow():
    register(make_client())
    c = make_client()
    assert c.get("/api/v1/me").status_code == 401
    bad = c.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "nope-nope"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_credentials"
    unknown = c.post("/api/v1/auth/login", json={"email": "x@example.com", "password": PASSWORD})
    assert unknown.json()["error"]["message"] == bad.json()["error"]["message"]

    ok = c.post("/api/v1/auth/login", json={"email": "ADA@example.com", "password": PASSWORD})
    assert ok.status_code == 200
    assert c.get("/api/v1/me").status_code == 200
    assert c.post("/api/v1/auth/logout").status_code == 200
    assert c.get("/api/v1/me").status_code == 401


def test_session_cookie_is_httponly(client):
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": "c@example.com", "password": PASSWORD, "full_name": "C"},
    )
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_csrf_header_required_for_unsafe_requests():
    register(make_client())
    c = TestClient(app)  # no CSRF header
    resp = c.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_failed"


def test_email_verification(client):
    register(client)
    token = last_token("verify-email")
    resp = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert resp.status_code == 200
    assert client.get("/api/v1/me").json()["user"]["is_verified"] is True
    # single use
    again = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert again.status_code == 400
    assert again.json()["error"]["code"] == "invalid_token"


def test_resend_verification_invalidates_previous_token(client):
    register(client)
    first = last_token("verify-email")
    assert client.post("/api/v1/auth/resend-verification").status_code == 200
    second = last_token("verify-email")
    assert first != second
    assert client.post("/api/v1/auth/verify-email", json={"token": first}).status_code == 400
    assert client.post("/api/v1/auth/verify-email", json={"token": second}).status_code == 200


def test_password_reset_revokes_sessions():
    browser = make_client()
    register(browser)
    other = make_client()
    resp = other.post("/api/v1/auth/forgot-password", json={"email": "ada@example.com"})
    assert resp.status_code == 202
    token = last_token("reset-password")
    new_password = "a brand new passphrase"
    resp = other.post(
        "/api/v1/auth/reset-password", json={"token": token, "password": new_password}
    )
    assert resp.status_code == 200
    assert browser.get("/api/v1/me").status_code == 401
    login = other.post(
        "/api/v1/auth/login", json={"email": "ada@example.com", "password": new_password}
    )
    assert login.status_code == 200
    assert (
        other.post(
            "/api/v1/auth/reset-password", json={"token": token, "password": new_password}
        ).status_code
        == 400
    )


def test_forgot_password_does_not_reveal_accounts(client):
    resp = client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})
    assert resp.status_code == 202


def test_login_rate_limited():
    register(make_client())
    c = make_client()
    codes = [
        c.post(
            "/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong-pass"}
        ).status_code
        for _ in range(11)
    ]
    assert codes[-1] == 429


def test_request_id_header_echoed(client):
    resp = client.get("/health", headers={"X-Request-ID": "abc12345-test"})
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == "abc12345-test"


def test_csrf_constant_matches_header():
    assert "X-Requested-With" in CSRF
