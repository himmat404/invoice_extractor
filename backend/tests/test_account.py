from app.models import ActivityEvent
from tests.conftest import PASSWORD, make_client, register


def test_update_profile(client):
    register(client)
    resp = client.patch(
        "/api/v1/me", json={"timezone": "Asia/Kolkata", "currency": "INR", "locale": "en-IN"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["timezone"], body["currency"], body["locale"]) == ("Asia/Kolkata", "INR", "en-IN")
    assert client.patch("/api/v1/me", json={"timezone": "Mars/Base"}).status_code == 422
    assert client.patch("/api/v1/me", json={"currency": "rupees"}).status_code == 422


def test_change_password_keeps_current_session_only():
    a = make_client()
    register(a)
    b = make_client()
    b.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    resp = a.post(
        "/api/v1/me/change-password",
        json={"current_password": PASSWORD, "new_password": "another long password"},
    )
    assert resp.status_code == 200
    assert a.get("/api/v1/me").status_code == 200
    assert b.get("/api/v1/me").status_code == 401


def test_change_password_requires_current(client):
    register(client)
    resp = client.post(
        "/api/v1/me/change-password",
        json={"current_password": "wrong-one", "new_password": "another long password"},
    )
    assert resp.status_code == 401


def test_list_and_revoke_sessions():
    a = make_client()
    register(a)
    b = make_client()
    b.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    sessions = a.get("/api/v1/me/sessions").json()
    assert len(sessions) == 2
    other = next(s for s in sessions if not s["current"])
    assert a.delete(f"/api/v1/me/sessions/{other['id']}").status_code == 200
    assert b.get("/api/v1/me").status_code == 401


def test_update_workspace_records_activity(client, db):
    register(client)
    resp = client.patch(
        "/api/v1/workspace",
        json={"name": "Acme", "tax_id": "27AAPFU0939F1ZV", "country": "IN", "name_extra": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["tax_id"] == "27AAPFU0939F1ZV"
    actions = [e.action for e in db.query(ActivityEvent).all()]
    assert "workspace.updated" in actions
    feed = client.get("/api/v1/workspace/activity").json()
    assert feed["total"] == 2
    assert feed["items"][0]["action"] == "workspace.updated"


def test_workspace_name_cannot_be_nulled(client):
    register(client, workspace_name="Keep Me")
    resp = client.patch("/api/v1/workspace", json={"name": None, "legal_name": "Keep Me LLP"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Keep Me"
