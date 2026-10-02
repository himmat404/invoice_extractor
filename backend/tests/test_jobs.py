import uuid
from datetime import timedelta

import pytest

from app.core.db import SessionLocal
from app.models import (
    AuditLog,
    CreditLedgerEntry,
    ExtractionJob,
    Invoice,
    InvoiceStatus,
    JobStatus,
    LedgerEntryType,
    UsageRecord,
)
from app.models.base import utcnow
from app.services import credits, jobs
from app.services.jobs import ExtractionOutcome, register_handler
from tests import samples
from tests.conftest import admin_login, create_admin, make_client, verified_client
from tests.test_uploads import set_overrides, upload

WORKER = "test-worker"


def work(n: int = 1) -> int:
    done = 0
    for _ in range(n):
        with SessionLocal() as db:
            if not jobs.work_once(db, WORKER):
                break
            done += 1
    return done


def upload_one(client, name="invoice.pdf") -> str:
    resp = upload(client, (name, samples.pdf()))
    assert resp.status_code == 201, resp.text
    return resp.json()["items"][0]["invoice_id"]


def make_ready(db):
    """Skip retry backoff."""
    db.query(ExtractionJob).filter_by(status=JobStatus.QUEUED).update(
        {"available_at": utcnow() - timedelta(seconds=1)}
    )
    db.commit()


def charges(db, invoice_id) -> int:
    return (
        db.query(CreditLedgerEntry)
        .filter_by(reference_id=str(invoice_id), entry_type=LedgerEntryType.CONSUMPTION)
        .count()
    )


def test_successful_processing_consumes_one_credit(db):
    c = verified_client()
    invoice_id = upload_one(c)
    assert work() == 1
    assert work() == 0
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert inv["status"] == "extracted"
    assert inv["status_label"] == "Ready"
    assert inv["processed_at"] is not None
    assert charges(db, invoice_id) == 1
    assert db.query(UsageRecord).filter_by(metric="invoice_processed").count() == 1
    assert c.get("/api/v1/usage").json()["total_credits_remaining"] == 9
    actions = [a["action"] for a in c.get("/api/v1/workspace/activity").json()["items"]]
    assert "invoice.processed" in actions


def test_permanent_failure_is_not_charged_and_hides_internals(db):
    c = verified_client()
    invoice_id = upload_one(c, "please-fail.pdf")
    work()
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert inv["status"] == "failed"
    assert inv["error_message"].startswith("We couldn't read this invoice")
    assert "fake handler" not in str(inv)
    assert inv["can_retry"] is True
    assert charges(db, invoice_id) == 0
    job = db.query(ExtractionJob).one()
    assert job.error_detail == "fake handler: forced permanent failure"
    assert c.get("/api/v1/usage").json()["total_credits_remaining"] == 10


def test_transient_failure_retries_with_backoff(db):
    c = verified_client()
    invoice_id = upload_one(c, "flaky.pdf")
    work()
    job = db.query(ExtractionJob).one()
    assert job.status == JobStatus.QUEUED
    assert job.available_at > utcnow()
    assert c.get(f"/api/v1/invoices/{invoice_id}").json()["status"] == "queued"
    assert work() == 0  # not ready yet
    make_ready(db)
    assert work() == 1
    db.expire_all()
    job = db.query(ExtractionJob).one()
    assert (job.status, job.attempts) == (JobStatus.SUCCEEDED, 2)
    assert [a["outcome"] for a in job.attempt_log] == ["retrying", "succeeded"]
    assert charges(db, invoice_id) == 1


def test_retry_exhaustion_fails(db):
    c = verified_client()

    def always_down(ctx):
        raise jobs.RetryableJobError("provider_unavailable", "down")

    register_handler("down", always_down)
    from app.core.config import get_settings

    settings = get_settings()
    settings.extraction_handler = "down"
    try:
        invoice_id = upload_one(c)
        for _ in range(3):
            make_ready(db)
            work()
    finally:
        settings.extraction_handler = "fake"
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert inv["status"] == "failed"
    assert inv["error_code"] == "provider_unavailable"
    assert db.query(ExtractionJob).one().attempts == 3


def test_customer_retry_after_failure(db):
    c = verified_client()
    invoice_id = upload_one(c, "fail-once.pdf")
    work()
    from app.services.ai.providers import FAKE_INVOICE
    from app.services.ai.schema import parse_model_output

    valid = parse_model_output(FAKE_INVOICE).model_dump(mode="json")
    register_handler("fake-ok", lambda ctx: ExtractionOutcome(data=valid))
    from app.core.config import get_settings

    get_settings().extraction_handler = "fake-ok"
    try:
        resp = c.post(f"/api/v1/invoices/{invoice_id}/retry")
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"
        assert c.post(f"/api/v1/invoices/{invoice_id}/retry").status_code == 400
        work()
    finally:
        get_settings().extraction_handler = "fake"
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert inv["status"] == "extracted"
    assert inv["extraction_result"]["invoice_number"] == "INV-1001"
    assert charges(db, invoice_id) == 1


def test_reprocess_does_not_charge_again_and_failure_keeps_results(db):
    c = verified_client()
    invoice_id = upload_one(c)
    work()
    resp = c.post(f"/api/v1/invoices/{invoice_id}/reprocess")
    assert resp.status_code == 200
    assert resp.json()["status"] == "extracted"  # previous results stay visible
    work()
    assert charges(db, invoice_id) == 1

    def broken(ctx):
        raise jobs.PermanentJobError("extraction_failed", "nope")

    register_handler("broken", broken)
    from app.core.config import get_settings

    get_settings().extraction_handler = "broken"
    try:
        c.post(f"/api/v1/invoices/{invoice_id}/reprocess")
        work()
    finally:
        get_settings().extraction_handler = "fake"
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert inv["status"] == "extracted"
    assert inv["extraction_result"] is not None
    assert "previous results were kept" in inv["error_message"]


def test_cancel_queued_invoice_and_batch():
    c = verified_client()
    set_overrides(c, max_files_per_batch=5)
    batch = upload(c, *[(f"{i}.pdf", samples.pdf()) for i in range(3)]).json()
    first = batch["items"][0]["invoice_id"]
    assert c.post(f"/api/v1/invoices/{first}/cancel").json()["status"] == "canceled"
    assert c.post(f"/api/v1/invoices/{first}/cancel").status_code == 400
    resp = c.post(f"/api/v1/batches/{batch['id']}/cancel").json()
    assert resp["status"] == "canceled"
    assert resp["counts"]["canceled"] == 3
    assert work() == 0
    resp = c.post(f"/api/v1/batches/{batch['id']}/retry-failed").json()
    assert resp["counts"]["queued"] == 3
    assert work(5) == 3
    final = c.get(f"/api/v1/batches/{batch['id']}").json()
    assert final["status"] == "completed"
    assert final["counts"]["completed"] == 3


def test_batch_counts_mixed_outcomes():
    c = verified_client()
    set_overrides(c, max_files_per_batch=5)
    batch = upload(
        c, ("ok.pdf", samples.pdf()), ("fail.pdf", samples.pdf()), ("junk.pdf", b"nope")
    ).json()
    work(5)
    final = c.get(f"/api/v1/batches/{batch['id']}").json()
    counts = final["counts"]
    assert (counts["total"], counts["completed"], counts["failed"], counts["rejected"]) == (
        3,
        1,
        1,
        1,
    )
    assert final["status"] == "completed_with_errors"
    assert c.get("/api/v1/batches").json()["items"][0]["counts"] == counts


def test_deleting_invoice_while_processing_discards_result(db):
    c = verified_client()
    invoice_id = upload_one(c)
    with SessionLocal() as s:
        job = jobs.claim_next(s, WORKER)
        assert c.delete(f"/api/v1/invoices/{invoice_id}").status_code == 200
        jobs.run_job(s, job, WORKER)
    db.expire_all()
    assert db.query(ExtractionJob).one().status == JobStatus.CANCELED
    assert db.get(Invoice, uuid.UUID(invoice_id)).status == InvoiceStatus.ARCHIVED
    assert charges(db, invoice_id) == 0
    assert c.get(f"/api/v1/invoices/{invoice_id}").status_code == 404


def test_expired_lease_is_recovered(db):
    c = verified_client()
    upload_one(c)
    with SessionLocal() as s:
        job = jobs.claim_next(s, WORKER)
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        s.commit()
    with SessionLocal() as s:
        assert jobs.requeue_expired(s) == 1
    db.expire_all()
    job = db.query(ExtractionJob).one()
    assert (job.status, job.attempts, job.error_code) == (JobStatus.QUEUED, 1, "timeout")


def test_result_discarded_when_lease_lost(db):
    c = verified_client()
    invoice_id = upload_one(c)
    with SessionLocal() as s:
        job = jobs.claim_next(s, WORKER)
        with SessionLocal() as other:  # another worker took over after lease expiry
            other.get(ExtractionJob, job.id).worker_id = "other-worker"
            other.commit()
        jobs.run_job(s, job, WORKER)
    assert charges(db, invoice_id) == 0
    assert db.get(Invoice, uuid.UUID(invoice_id)).status == InvoiceStatus.PROCESSING


def test_insufficient_credits_at_completion_fails_without_charge(db):
    c = verified_client()
    invoice_id = upload_one(c)
    ws = db.get(Invoice, uuid.UUID(invoice_id)).workspace_id
    credits.revoke_credits(
        db, ws, 10, credits.CreditBucket.PERIOD, description="t", actor_admin_id=None
    )
    db.commit()
    work()
    inv = c.get(f"/api/v1/invoices/{invoice_id}").json()
    assert (inv["status"], inv["error_code"]) == ("failed", "insufficient_credits")
    assert charges(db, invoice_id) == 0


def test_per_workspace_concurrency_is_fair(db):
    busy = verified_client("busy@example.com")
    set_overrides(busy, max_files_per_batch=10)
    upload(busy, *[(f"{i}.pdf", samples.pdf()) for i in range(5)])
    quiet = verified_client("quiet@example.com")
    quiet_invoice = upload_one(quiet)
    claimed = []
    with SessionLocal() as s:
        for _ in range(4):
            claimed.append(jobs.claim_next(s, WORKER))
    workspaces = [j.workspace_id for j in claimed]
    quiet_ws = db.get(Invoice, uuid.UUID(quiet_invoice)).workspace_id
    assert workspaces.count(quiet_ws) == 1  # not starved behind the busy workspace
    assert len(set(workspaces)) == 2


def test_enqueue_is_idempotent(db):
    c = verified_client()
    invoice_id = upload_one(c)
    invoice = db.get(Invoice, uuid.UUID(invoice_id))
    first = jobs.enqueue(db, invoice)
    second = jobs.enqueue(db, invoice)
    db.commit()
    assert first.id == second.id
    assert db.query(ExtractionJob).count() == 1


def test_admin_job_monitoring(db):
    create_admin(db)
    c = verified_client()
    invoice_id = upload_one(c, "fail.pdf")
    work()
    admin = make_client()
    admin_login(admin)
    stats = admin.get("/api/admin/jobs/stats").json()
    assert stats["by_status"] == {"failed": 1}
    assert stats["failed_last_24h"] == 1
    listing = admin.get("/api/admin/jobs", params={"status": "failed"}).json()
    job_id = listing["items"][0]["id"]
    detail = admin.get(f"/api/admin/jobs/{job_id}").json()
    assert detail["error_detail"] == "fake handler: forced permanent failure"
    assert detail["attempt_log"][0]["outcome"] == "failed"
    resp = admin.post(f"/api/admin/jobs/{job_id}/retry")
    assert resp.status_code == 200
    new_job = resp.json()
    assert new_job["status"] == "queued" and new_job["requested_by_admin_id"]
    assert admin.post(f"/api/admin/jobs/{job_id}/retry").status_code == 200  # idempotent
    assert admin.post(f"/api/admin/jobs/{new_job['id']}/cancel").json()["status"] == "canceled"
    actions = {a.action for a in db.query(AuditLog).all()}
    assert {"job.retried", "job.canceled"} <= actions
    assert c.get(f"/api/v1/invoices/{invoice_id}").json()["status"] == "canceled"


def test_job_admin_rbac(db):
    create_admin(db, role_name="finance", email="fin@invoiceflow.example.com")
    fin = make_client()
    admin_login(fin, email="fin@invoiceflow.example.com")
    assert fin.get("/api/admin/jobs").status_code == 403
    create_admin(db, role_name="support", email="sup@invoiceflow.example.com")
    sup = make_client()
    admin_login(sup, email="sup@invoiceflow.example.com")
    assert sup.get("/api/admin/jobs").status_code == 200
    assert sup.post(f"/api/admin/jobs/{uuid.uuid4()}/retry").status_code == 403


@pytest.fixture(autouse=True)
def _reset_handler():
    yield
    from app.core.config import get_settings

    get_settings().extraction_handler = "fake"
