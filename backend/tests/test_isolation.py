import uuid

from tests.conftest import make_client, register


def test_user_cannot_select_foreign_workspace():
    alice = make_client()
    register(alice, email="alice@example.com", workspace_name="Alice Co")
    bob = make_client()
    register(bob, email="bob@example.com", workspace_name="Bob Co")
    bob_ws = bob.get("/api/v1/workspace").json()["id"]

    resp = alice.get("/api/v1/workspace", headers={"X-Workspace-Id": bob_ws})
    assert resp.status_code == 403
    resp = alice.get("/api/v1/workspace/activity", headers={"X-Workspace-Id": bob_ws})
    assert resp.status_code == 403
    resp = alice.get("/api/v1/workspace", headers={"X-Workspace-Id": str(uuid.uuid4())})
    assert resp.status_code == 403
    resp = alice.get("/api/v1/workspace", headers={"X-Workspace-Id": "not-a-uuid"})
    assert resp.status_code == 403


def test_activity_feed_is_workspace_scoped():
    alice = make_client()
    register(alice, email="alice@example.com")
    bob = make_client()
    register(bob, email="bob@example.com")
    bob.patch("/api/v1/workspace", json={"legal_name": "Bob Pvt Ltd"})
    alice_feed = alice.get("/api/v1/workspace/activity").json()
    assert alice_feed["total"] == 1
    assert all(i["action"] != "workspace.updated" for i in alice_feed["items"])


def test_user_cannot_revoke_foreign_session():
    alice = make_client()
    register(alice, email="alice@example.com")
    bob = make_client()
    register(bob, email="bob@example.com")
    bob_session = bob.get("/api/v1/me/sessions").json()[0]["id"]
    assert alice.delete(f"/api/v1/me/sessions/{bob_session}").status_code == 404
    assert bob.get("/api/v1/me").status_code == 200


def test_customer_session_cannot_reach_admin_api():
    alice = make_client()
    register(alice, email="alice@example.com")
    assert alice.get("/api/admin/auth/me").status_code == 401
    assert alice.get("/api/admin/customers").status_code == 401
