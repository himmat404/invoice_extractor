import copy
import uuid

import pytest

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import (
    CreditLedgerEntry,
    DuplicateDetectionRule,
    DuplicateRuleType,
    Invoice,
    LedgerEntryType,
)
from app.services.ai.providers import FAKE_INVOICE
from app.services.ai.schema import parse_model_output
from app.services.duplicates import ensure_default_rules
from app.services.jobs import ExtractionOutcome, register_handler
from tests import samples
from tests.conftest import admin_login, create_admin, make_client, verified_client
from tests.test_jobs import upload_one, work
from tests.test_uploads import set_overrides, upload

_responses: list[dict] = []


def _handler(ctx):
    raw = _responses.pop(0) if _responses else FAKE_INVOICE
    return ExtractionOutcome(
        data=parse_model_output(raw).model_dump(mode="json"),
        metadata={"confidence_method": "model_self_reported_v1"},
    )


register_handler("scripted", _handler)


@pytest.fixture(autouse=True)
def scripted():
    get_settings().extraction_handler = "scripted"
    _responses.clear()
    yield
    get_settings().extraction_handler = "fake"


def invoice_like(**changes) -> dict:
    data = copy.deepcopy(FAKE_INVOICE)
    data.update(changes)
    return data


def process(client, **changes) -> dict:
    _responses.append(invoice_like(**changes))
    invoice_id = upload_one(client)
    work()
    return client.get(f"/api/v1/invoices/{invoice_id}").json()


def test_clean_extraction_is_ready_with_quality_details():
    c = verified_client()
    inv = process(c)
    assert inv["status"] == "extracted"
    assert inv["validation"]["status"] == "passed"
    assert inv["validation"]["computed"]["expected_grand_total"] == "2950.00"
    assert inv["invoice_number"] == "INV-1001" and inv["grand_total"] == "2950.00"
    assert inv["supplier_name"] == "Acme Supplies Pvt Ltd"
    conf = inv["confidence"]
    assert conf["fields"] == {"grand_total": "high", "invoice_number": "high"}
    assert conf["overall"] is None and conf["overall_band"] == "unavailable"  # not invented
    assert conf["method"] == "model_self_reported_v1" and conf["threshold_version"] == 1
    assert inv["can_approve"] and inv["can_edit"]


def test_low_confidence_needs_review_until_corrected():
    c = verified_client()
    inv = process(c, field_confidence=[{"field": "grand_total", "confidence": 0.4}])
    assert inv["status"] == "needs_review"
    assert inv["confidence"]["low_review_fields"] == ["grand_total"]
    data = inv["data"]
    data["grand_total"] = "2950.00"
    data.pop("field_confidence"), data.pop("parse_warnings")
    data["notes"] = "checked"
    resp = c.put(f"/api/v1/invoices/{inv['id']}/data", json=data)
    assert resp.status_code == 200, resp.text
    assert resp.json()["edited_fields"] == ["notes"]
    data["grand_total"] = "2950.0"
    after = c.put(f"/api/v1/invoices/{inv['id']}/data", json=data).json()
    assert "grand_total" in after["edited_fields"]
    assert after["confidence"]["low_review_fields"] == []
    assert after["status"] == "extracted"


def test_review_corrects_totals_and_keeps_original_extraction():
    c = verified_client()
    inv = process(c, grand_total="3100.00")
    assert inv["status"] == "needs_review"
    issue = next(i for i in inv["validation"]["issues"] if i["code"] == "grand_total_mismatch")
    assert issue["details"] == {"expected": "2950.00", "found": "3100.00"}
    data = {k: v for k, v in inv["data"].items() if k not in ("field_confidence", "parse_warnings")}
    data["grand_total"] = "2950.00"
    fixed = c.put(f"/api/v1/invoices/{inv['id']}/data", json=data).json()
    assert fixed["status"] == "extracted"
    assert fixed["validation"]["status"] == "passed"
    assert fixed["grand_total"] == "2950.00"
    assert fixed["extraction_result"]["grand_total"] == "3100.00"  # original kept
    feed = [a["action"] for a in c.get("/api/v1/workspace/activity").json()["items"]]
    assert "invoice.edited" in feed


def test_review_input_is_validated():
    c = verified_client()
    inv = process(c)
    bad = c.put(f"/api/v1/invoices/{inv['id']}/data", json={"grand_total": "lots"})
    assert bad.status_code == 422
    bad = c.put(f"/api/v1/invoices/{inv['id']}/data", json={"currency": "rupees"})
    assert bad.status_code == 422
    bad = c.put(f"/api/v1/invoices/{inv['id']}/data", json={"field_confidence": {}})
    assert bad.status_code == 422


def test_approval_flow():
    c = verified_client()
    inv = process(c, currency=None)  # warning: missing currency
    assert inv["status"] == "validation_warning"
    resp = c.post(f"/api/v1/invoices/{inv['id']}/approve", json={})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "issues_need_acknowledgement"
    resp = c.post(f"/api/v1/invoices/{inv['id']}/approve", json={"acknowledge_issues": True})
    assert resp.status_code == 200
    approved = resp.json()
    assert approved["status"] == "approved" and approved["approved_at"]
    assert c.post(f"/api/v1/invoices/{inv['id']}/approve", json={}).status_code == 200  # idempotent
    # editing an approved invoice reopens it
    data = {
        k: v for k, v in approved["data"].items() if k not in ("field_confidence", "parse_warnings")
    }
    data["currency"] = "INR"
    reopened = c.put(f"/api/v1/invoices/{inv['id']}/data", json=data).json()
    assert reopened["status"] == "extracted" and reopened["approved_at"] is None
    assert c.post(f"/api/v1/invoices/{inv['id']}/approve", json={}).json()["status"] == "approved"


def test_failed_validation_cannot_be_approved():
    c = verified_client()
    _responses.append({})
    invoice_id = upload_one(c)
    work()
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert inv["validation"]["status"] == "failed" and inv["status"] == "needs_review"
    assert inv["can_approve"] is False
    resp = c.post(f"/api/v1/invoices/{invoice_id}/approve", json={"acknowledge_issues": True})
    assert resp.json()["error"]["code"] == "validation_failed"


def test_confirmed_duplicate_keep_both():
    c = verified_client()
    first = process(c)
    second = process(c)
    assert first["duplicate_status"] == "no_match"
    assert second["duplicate_status"] == "confirmed_duplicate"
    assert second["status"] == "needs_review"
    match = second["duplicates"][0]
    assert match["matched_invoice"]["id"] == first["id"]
    assert match["rule_name"] == "Same supplier and invoice number"
    resp = c.post(
        f"/api/v1/invoices/{second['id']}/duplicates/decision", json={"decision": "keep_both"}
    ).json()
    assert resp["status"] == "extracted"
    assert resp["duplicates"][0]["decision"] == "kept_both"
    again = c.post(
        f"/api/v1/invoices/{second['id']}/duplicates/decision", json={"decision": "keep_both"}
    )
    assert again.json()["error"]["code"] == "no_pending_duplicates"


def test_mark_duplicate_blocks_approval_and_review_queue():
    c = verified_client()
    process(c)
    second = process(c)
    c.post(
        f"/api/v1/invoices/{second['id']}/duplicates/decision", json={"decision": "mark_duplicate"}
    )
    inv = c.get(f"/api/v1/invoices/{second['id']}").json()
    assert inv["can_approve"] is False
    resp = c.post(f"/api/v1/invoices/{second['id']}/approve", json={"acknowledge_issues": True})
    assert resp.json()["error"]["code"] == "marked_duplicate"
    queue = c.get("/api/v1/invoices/review-queue").json()
    assert second["id"] not in [i["id"] for i in queue["items"]]


def test_cancel_duplicate_refunds_credit(db):
    c = verified_client()
    process(c)
    second = process(c)
    assert c.get("/api/v1/usage").json()["total_credits_remaining"] == 8
    resp = c.post(
        f"/api/v1/invoices/{second['id']}/duplicates/decision", json={"decision": "cancel"}
    )
    assert resp.status_code == 200
    assert c.get(f"/api/v1/invoices/{second['id']}").status_code == 404
    assert c.get("/api/v1/usage").json()["total_credits_remaining"] == 9
    assert db.query(CreditLedgerEntry).filter_by(entry_type=LedgerEntryType.REVERSAL).count() == 1


def test_potential_and_similarity_matches():
    c = verified_client()
    process(c)
    potential = process(c, invoice_number="ZZ-9999")  # same supplier, date, total
    assert potential["duplicate_status"] == "potential_duplicate"
    assert potential["duplicates"][0]["rule_name"] == "Same supplier, date and total"
    similar = process(
        c, invoice_number="INV-1001A", invoice_date="2026-09-20", due_date="2026-10-20"
    )
    assert similar["duplicate_status"] == "potential_duplicate"
    match = similar["duplicates"][0]
    assert match["rule_name"].startswith("Similar invoice number") and match["score"] >= 0.85


def test_blocking_rule_requires_decision(db):
    ensure_default_rules(db)
    rule = (
        db.query(DuplicateDetectionRule)
        .filter_by(rule_type=DuplicateRuleType.INVOICE_NUMBER_SUPPLIER)
        .one()
    )
    rule.blocking = True
    db.commit()
    c = verified_client()
    process(c)
    second = process(c)
    assert second["duplicate_status"] == "review_required"
    resp = c.post(f"/api/v1/invoices/{second['id']}/approve", json={"acknowledge_issues": True})
    assert resp.json()["error"]["code"] == "duplicate_decision_required"
    c.post(f"/api/v1/invoices/{second['id']}/duplicates/decision", json={"decision": "keep_both"})
    resp = c.post(f"/api/v1/invoices/{second['id']}/approve", json={"acknowledge_issues": True})
    assert resp.status_code == 200


def test_duplicates_never_cross_workspaces():
    a = verified_client("a@example.com")
    b = verified_client("b@example.com")
    process(a)
    other = process(b)
    assert other["duplicate_status"] == "no_match" and other["duplicates"] == []


def test_disabling_exact_file_rule_allows_same_file(db):
    ensure_default_rules(db)
    rule = db.query(DuplicateDetectionRule).filter_by(rule_type=DuplicateRuleType.EXACT_FILE).one()
    rule.is_active = False
    db.commit()
    c = verified_client()
    data = samples.pdf()
    assert upload(c, ("a.pdf", data)).json()["items"][0]["status"] == "accepted"
    assert upload(c, ("b.pdf", data)).json()["items"][0]["status"] == "accepted"


def test_review_queue_and_filters():
    c = verified_client()
    ok = process(c, invoice_number="A-1")
    warn = process(
        c, invoice_number="B-2", currency=None, invoice_date="2026-08-01", due_date="2026-09-01"
    )
    bad = process(
        c,
        invoice_number="C-3",
        grand_total="9999.00",
        invoice_date="2026-07-01",
        due_date="2026-08-01",
    )
    queue = [i["id"] for i in c.get("/api/v1/invoices/review-queue").json()["items"]]
    assert queue == [bad["id"], warn["id"]]

    def ids(**params):
        return [i["id"] for i in c.get("/api/v1/invoices", params=params).json()["items"]]

    assert ids(q="b-2") == [warn["id"]]
    assert ids(q="acme") and len(ids(q="acme")) == 3
    assert ids(currency="INR", sort="date_asc") == [bad["id"], ok["id"]]
    assert ids(min_amount="5000") == [bad["id"]]
    assert ids(date_from="2026-09-01") == [ok["id"]]
    assert ids(validation_status="needs_review") == [bad["id"]]
    assert ids(sort="amount_desc")[0] == bad["id"]
    assert len(ids(status=["needs_review", "validation_warning"])) == 2


def test_reprocess_resets_review_edits(db):
    c = verified_client()
    inv = process(c)
    data = {k: v for k, v in inv["data"].items() if k not in ("field_confidence", "parse_warnings")}
    data["notes"] = "edited"
    c.put(f"/api/v1/invoices/{inv['id']}/data", json=data)
    c.post(f"/api/v1/invoices/{inv['id']}/reprocess")
    work()
    after = c.get(f"/api/v1/invoices/{inv['id']}").json()
    assert after["edited_fields"] == [] and after["data"]["notes"] is None
    assert after["duplicate_status"] == "no_match"  # doesn't match itself


# --- admin & workspace configuration ------------------------------------------------------------


def test_admin_thresholds_and_rules(db):
    create_admin(db)
    a = make_client()
    admin_login(a)
    history = a.get("/api/admin/confidence-thresholds").json()
    assert history[0]["version"] == 1
    resp = a.post(
        "/api/admin/confidence-thresholds",
        json={"high_min": 0.99, "medium_min": 0.95, "review_fields": ["grand_total"]},
    )
    assert resp.status_code == 201 and resp.json()["version"] == 2
    assert (
        a.post(
            "/api/admin/confidence-thresholds",
            json={"high_min": 0.5, "medium_min": 0.9, "review_fields": []},
        ).status_code
        == 422
    )
    c = verified_client()
    inv = process(c)
    assert inv["confidence"]["threshold_version"] == 2
    assert inv["confidence"]["fields"]["grand_total"] == "medium"  # 0.97: >= 0.95, < 0.99
    rules = a.get("/api/admin/duplicate-rules").json()
    assert len(rules) == 4
    resp = a.post(
        "/api/admin/duplicate-rules",
        json={"name": "Same PO", "rule_type": "multi_field", "fields": ["supplier", "nope"]},
    )
    assert resp.status_code == 422
    resp = a.post(
        "/api/admin/duplicate-rules",
        json={
            "name": "Supplier+total",
            "rule_type": "multi_field",
            "fields": ["supplier", "grand_total"],
            "blocking": True,
        },
    )
    assert resp.status_code == 201
    patched = a.patch(
        f"/api/admin/duplicate-rules/{resp.json()['id']}", json={"is_active": False}
    ).json()
    assert patched["is_active"] is False


def test_workspace_rules_require_entitlement():
    c = verified_client()
    body = {
        "name": "Same PO and supplier",
        "rule_type": "multi_field",
        "fields": ["supplier", "invoice_number"],
    }
    resp = c.post("/api/v1/duplicate-rules", json=body)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "plan_feature_unavailable"
    set_overrides(c, duplicate_rule_overrides=True)
    resp = c.post("/api/v1/duplicate-rules", json=body)
    assert resp.status_code == 201
    exact = c.post("/api/v1/duplicate-rules", json={"name": "x", "rule_type": "exact_file"})
    assert exact.status_code == 400
    listed = c.get("/api/v1/duplicate-rules").json()
    assert len(listed) == 5
    other = verified_client("other@example.com")
    assert len(other.get("/api/v1/duplicate-rules").json()) == 4
    rule_id = resp.json()["id"]
    set_overrides(other, duplicate_rule_overrides=True)
    assert (
        other.patch(f"/api/v1/duplicate-rules/{rule_id}", json={"is_active": False}).status_code
        == 404
    )


def test_quality_admin_rbac(db):
    create_admin(db, role_name="support", email="sup@invoiceflow.example.com")
    s = make_client()
    admin_login(s, email="sup@invoiceflow.example.com")
    assert s.get("/api/admin/duplicate-rules").status_code == 403
    assert (
        s.post(
            "/api/admin/confidence-thresholds",
            json={"high_min": 0.9, "medium_min": 0.7, "review_fields": []},
        ).status_code
        == 403
    )


def test_header_columns_synced(db):
    c = verified_client()
    inv = process(c)
    with SessionLocal() as s:
        row = s.get(Invoice, uuid.UUID(inv["id"]))
        assert row.norm_invoice_number == "INV1001"
        assert row.norm_supplier_name == "ACME SUPPLIES"
        assert row.supplier_tax_id == "27AAPFU0939F1ZV"
