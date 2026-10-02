import uuid
from decimal import Decimal

import httpx
import pytest

from app.core.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret, secret_hint
from app.core.db import SessionLocal
from app.models import (
    AICall,
    AuditLog,
    ExtractionJob,
    Invoice,
    JobStatus,
    ModelConfiguration,
    ModelProvider,
    ProviderCredential,
)
from app.services.ai.providers import GeminiAdapter, register_adapter
from tests.conftest import admin_login, create_admin, make_client, verified_client
from tests.test_jobs import make_ready, upload_one, work


@pytest.fixture(autouse=True)
def ai_handler():
    get_settings().extraction_handler = "ai"
    yield
    get_settings().extraction_handler = "fake"


def provider(db, code="fake", adapter="fake") -> ModelProvider:
    p = db.query(ModelProvider).filter_by(code=code).one_or_none()
    if p is None:
        p = ModelProvider(code=code, name=code.title(), adapter=adapter)
        db.add(p)
        db.commit()
    return p


def model(
    db,
    name,
    priority,
    *,
    prov=None,
    credential=None,
    fallback_on=None,
    prices=(Decimal("0.30"), Decimal("2.50")),
) -> ModelConfiguration:
    prov = prov or provider(db)
    m = ModelConfiguration(
        provider_id=prov.id,
        model_name=name,
        display_name=name,
        priority=priority,
        is_active=True,
        credential_id=credential.id if credential else None,
        input_price_per_million=prices[0],
        output_price_per_million=prices[1],
        **({"fallback_on": fallback_on} if fallback_on is not None else {}),
    )
    db.add(m)
    db.commit()
    return m


def job_for(db, invoice_id) -> ExtractionJob:
    db.expire_all()
    return db.query(ExtractionJob).filter_by(invoice_id=uuid.UUID(invoice_id)).one()


def test_primary_model_success_records_everything(db):
    m = model(db, "fake-ok", 0)
    c = verified_client()
    invoice_id = upload_one(c)
    work()
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert inv["status"] == "extracted"
    result = inv["extraction_result"]
    assert result["invoice_number"] == "INV-1001"
    assert result["grand_total"] == "2950.00"
    assert result["field_confidence"]["grand_total"] == 0.97
    # customers never see which model handled it
    assert "fake-ok" not in str(inv) and "provider" not in str(inv)

    job = job_for(db, invoice_id)
    assert (job.provider_code, job.model_name) == ("fake", "fake-ok")
    assert job.model_configuration_id == m.id
    assert job.prompt_version_id is not None
    assert job.extraction_schema_version == "1.0"
    assert job.confidence_method == "model_self_reported_v1"
    call = db.query(AICall).one()
    assert call.success and call.route_position == 0 and call.job_id == job.id
    assert call.plan_code == "free"
    # 1200 in * 0.30/M + 350 out * 2.50/M
    assert call.estimated_cost == Decimal("0.001235")
    assert db.get(Invoice, uuid.UUID(invoice_id)).extraction_job_id == job.id


def test_fallback_on_retryable_error(db):
    model(db, "fake-rate-limited", 0)
    model(db, "fake-ok", 10)
    c = verified_client()
    invoice_id = upload_one(c)
    work()
    assert c.get(f"/api/v1/invoices/{invoice_id}").json()["status"] == "extracted"
    assert job_for(db, invoice_id).model_name == "fake-ok"
    calls = db.query(AICall).order_by(AICall.route_position).all()
    assert [(c.model_name, c.success, c.error_class) for c in calls] == [
        ("fake-rate-limited", False, "rate_limited"),
        ("fake-ok", True, None),
    ]
    assert calls[0].estimated_cost is None  # no tokens reported, cost unknown (not zero)


def test_no_fallback_for_errors_outside_rules(db):
    model(db, "fake-invalid", 0)  # invalid_request isn't in the default fallback rules
    model(db, "fake-ok", 10)
    c = verified_client()
    invoice_id = upload_one(c)
    work()
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert (inv["status"], inv["error_code"]) == ("failed", "extraction_failed")
    assert db.query(AICall).count() == 1


def test_custom_fallback_rules(db):
    model(db, "fake-invalid", 0, fallback_on=["invalid_request"])
    model(db, "fake-ok", 10)
    c = verified_client()
    invoice_id = upload_one(c)
    work()
    assert c.get(f"/api/v1/invoices/{invoice_id}").json()["status"] == "extracted"


def test_all_models_malformed_is_permanent_failure(db):
    model(db, "fake-malformed", 0)
    model(db, "fake-malformed", 10)
    c = verified_client()
    invoice_id = upload_one(c)
    work()
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert inv["status"] == "failed"
    job = job_for(db, invoice_id)
    assert job.error_code == "extraction_failed"
    assert "fake-malformed:malformed_response" in job.error_detail
    calls = db.query(AICall).all()
    assert all(c.input_tokens == 1200 for c in calls)  # tokens still billed on bad output


def test_all_models_transient_is_retried(db):
    model(db, "fake-timeout", 0)
    model(db, "fake-unavailable", 10)
    c = verified_client()
    invoice_id = upload_one(c)
    work()
    job = job_for(db, invoice_id)
    assert job.status == JobStatus.QUEUED and job.error_code == "provider_unavailable"
    assert c.get(f"/api/v1/invoices/{invoice_id}").json()["status"] == "queued"


def test_no_active_models_retries_later(db):
    c = verified_client()
    invoice_id = upload_one(c)
    work()
    job = job_for(db, invoice_id)
    assert job.status == JobStatus.QUEUED
    assert job.error_detail == "no active model configuration"
    model(db, "fake-ok", 0)
    make_ready(db)
    work()
    assert c.get(f"/api/v1/invoices/{invoice_id}").json()["status"] == "extracted"


def test_inactive_models_and_providers_are_skipped(db):
    off = model(db, "fake-ok", 0)
    off.is_active = False
    other = provider(db, "fake2")
    other.is_active = False
    db.commit()
    model(db, "fake-ok", 5, prov=other)
    good = model(db, "fake-ok", 10)
    c = verified_client()
    invoice_id = upload_one(c)
    work()
    assert job_for(db, invoice_id).model_configuration_id == good.id


def test_gemini_with_encrypted_credential_and_missing_credential_fallback(db):
    seen = {}

    def handler(req):
        seen["key"] = req.headers["x-goog-api-key"]
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": '{"invoice_number": "G-7"}'}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            },
        )

    register_adapter("gemini", GeminiAdapter(httpx.Client(transport=httpx.MockTransport(handler))))
    try:
        gemini = provider(db, "gemini", "gemini")
        model(db, "gemini-nokey", 0, prov=gemini)  # no credential -> credential_missing
        cred = ProviderCredential(
            provider_id=gemini.id,
            label="prod",
            encrypted_secret=encrypt_secret("AIza-secret-0042"),
            secret_hint=secret_hint("AIza-secret-0042"),
        )
        db.add(cred)
        db.commit()
        model(db, "gemini-pro", 10, prov=gemini, credential=cred)
        c = verified_client()
        invoice_id = upload_one(c)
        work()
    finally:
        register_adapter("gemini", GeminiAdapter())
    assert seen["key"] == "AIza-secret-0042"
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert inv["extraction_result"]["invoice_number"] == "G-7"
    errors = [c.error_class for c in db.query(AICall).order_by(AICall.route_position)]
    assert errors == ["credential_missing", None]
    db.expire_all()
    assert db.get(ProviderCredential, cred.id).last_used_at is not None


def test_crypto_roundtrip():
    token = encrypt_secret("super-secret-value")
    assert "super-secret" not in token
    assert decrypt_secret(token) == "super-secret-value"
    assert secret_hint("super-secret-value") == "…alue"
    with pytest.raises(ValueError):
        decrypt_secret("garbage")


# --- admin API ----------------------------------------------------------------------------------


def admin(db, role="super_admin", email="root@invoiceflow.example.com"):
    create_admin(db, role_name=role, email=email)
    client = make_client()
    admin_login(client, email=email)
    return client


def test_admin_configures_providers_credentials_and_models(db):
    a = admin(db)
    prov = a.post(
        "/api/admin/ai/providers", json={"code": "gem", "name": "Gemini", "adapter": "gemini"}
    ).json()
    assert (
        a.post(
            "/api/admin/ai/providers", json={"code": "xx", "name": "X", "adapter": "magic"}
        ).status_code
        == 400
    )
    resp = a.post(
        "/api/admin/ai/credentials",
        json={"provider_id": prov["id"], "label": "Main", "api_key": "AIza-TOPSECRET-9876"},
    )
    assert resp.status_code == 201
    cred = resp.json()
    assert cred["secret_hint"] == "…9876"
    assert "TOPSECRET" not in resp.text
    assert "TOPSECRET" not in a.get("/api/admin/ai/credentials").text
    stored = db.get(ProviderCredential, uuid.UUID(cred["id"]))
    assert "TOPSECRET" not in stored.encrypted_secret
    assert decrypt_secret(stored.encrypted_secret) == "AIza-TOPSECRET-9876"

    m1 = a.post(
        "/api/admin/ai/models",
        json={
            "provider_id": prov["id"],
            "credential_id": cred["id"],
            "model_name": "gemini-2.5-flash",
            "display_name": "Flash",
            "is_active": True,
            "input_price_per_million": "0.30",
            "output_price_per_million": "2.50",
            "settings": {"timeout_seconds": 60},
        },
    ).json()
    assert m1["fallback_on"] and m1["settings"]["temperature"] == 0.0
    bad = a.post(
        "/api/admin/ai/models",
        json={
            "provider_id": prov["id"],
            "model_name": "m",
            "display_name": "M",
            "fallback_on": ["cosmic_rays"],
        },
    )
    assert bad.status_code == 422
    bad = a.post(
        "/api/admin/ai/models",
        json={
            "provider_id": prov["id"],
            "model_name": "m",
            "display_name": "M",
            "settings": {"timeout_seconds": 1},
        },
    )
    assert bad.status_code == 400
    m2 = a.post(
        "/api/admin/ai/models",
        json={"provider_id": prov["id"], "model_name": "gemini-2.5-pro", "display_name": "Pro"},
    ).json()
    order = a.put("/api/admin/ai/models/order", json={"model_ids": [m2["id"], m1["id"]]}).json()
    assert [m["id"] for m in order] == [m2["id"], m1["id"]]

    rotated = a.post(
        f"/api/admin/ai/credentials/{cred['id']}/rotate", json={"api_key": "AIza-NEWSECRET-1111"}
    ).json()
    assert rotated["secret_hint"] == "…1111" and rotated["rotated_at"]
    revoked = a.delete(f"/api/admin/ai/credentials/{cred['id']}").json()
    assert revoked["is_active"] is False
    db.expire_all()
    assert db.get(ProviderCredential, uuid.UUID(cred["id"])).encrypted_secret is None

    logs = db.query(AuditLog).filter(AuditLog.action.like("ai_%")).all()
    assert {
        "ai_credential.created",
        "ai_credential.rotated",
        "ai_credential.revoked",
        "ai_model.created",
        "ai_model.reordered",
    } <= {log.action for log in logs}
    assert all("SECRET" not in str(log.summary) for log in logs)


def test_admin_test_run_and_prompt_versions(db):
    a = admin(db)
    m = model(db, "fake-ok", 0)
    resp = a.post(f"/api/admin/ai/models/{m.id}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] and body["result"]["invoice_number"] == "INV-1001"
    assert db.query(AICall).one().purpose == "admin_test"

    prompts = a.get("/api/admin/ai/prompts").json()
    assert len(prompts) == 1 and prompts[0]["is_active"]
    v2 = a.post(
        "/api/admin/ai/prompts",
        json={
            "system_prompt": "Extract invoice fields carefully. Never invent values.",
            "user_prompt": "Extract it.",
            "notes": "Stricter",
        },
    ).json()
    assert v2["version"] == 2 and v2["is_active"] is False
    a.post(f"/api/admin/ai/prompts/{v2['id']}/activate")
    prompts = a.get("/api/admin/ai/prompts").json()
    assert [p["is_active"] for p in prompts] == [True, False]

    c = verified_client()
    invoice_id = upload_one(c)
    work()
    assert str(job_for(db, invoice_id).prompt_version_id) == v2["id"]


def test_usage_summary(db):
    model(db, "fake-rate-limited", 0)
    model(db, "fake-ok", 10)
    c = verified_client()
    upload_one(c)
    work()
    a = admin(db)
    summary = a.get("/api/admin/ai/usage/summary").json()
    assert summary["totals"]["calls"] == 2
    assert summary["totals"]["successful_calls"] == 1
    assert summary["totals"]["calls_without_cost"] == 1
    assert summary["invoices_processed"] == 1
    assert summary["fallback_successes"] == 1
    assert summary["cost_per_processed_invoice"] == "0.001235"
    assert summary["errors"] == {"rate_limited": 1}
    assert {b["key"] for b in summary["by_model"]} == {"fake-ok", "fake-rate-limited"}
    assert summary["by_plan"][0]["key"] == "free"
    assert len(summary["by_day"]) == 1 and len(summary["top_workspaces"]) == 1


def test_ai_admin_rbac(db):
    finance = admin(db, "finance", "fin@invoiceflow.example.com")
    assert finance.get("/api/admin/ai/usage/summary").status_code == 200  # costs.read
    assert finance.get("/api/admin/ai/models").status_code == 403
    ops = admin(db, "operations", "ops@invoiceflow.example.com")
    assert ops.get("/api/admin/ai/models").status_code == 200
    prov = provider(db)
    # operations can manage models but not credentials
    resp = ops.post(
        "/api/admin/ai/credentials",
        json={"provider_id": str(prov.id), "label": "x", "api_key": "12345678abc"},
    )
    assert resp.status_code == 403
    support = admin(db, "support", "sup@invoiceflow.example.com")
    assert support.get("/api/admin/ai/usage/summary").status_code == 403
    customer = verified_client()
    assert customer.get("/api/admin/ai/models").status_code == 401


def test_worker_handles_ai_handler_session_cleanly(db):
    """Regression guard: the AI handler opens its own sessions; the worker must still apply
    the result under its lease."""
    model(db, "fake-ok", 0)
    c = verified_client()
    upload_one(c)
    with SessionLocal() as s:
        from app.services import jobs

        assert jobs.work_once(s, "w1")
    assert db.query(ExtractionJob).one().status == JobStatus.SUCCEEDED
