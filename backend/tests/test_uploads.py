import io
import uuid

from app.core.db import SessionLocal
from app.models import ExtractionJob, Invoice, InvoiceFile, JobStatus, Workspace
from tests import samples
from tests.conftest import make_client, register, verified_client


def upload(client, *files, **form):
    payload = [("files", (name, data, "application/octet-stream")) for name, data in files]
    data = {k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()}
    return client.post("/api/v1/uploads", files=payload, data=data)


def set_overrides(client, **overrides):
    ws_id = uuid.UUID(client.get("/api/v1/workspace").json()["id"])
    with SessionLocal() as db:
        db.get(Workspace, ws_id).entitlement_overrides = overrides
        db.commit()
    return ws_id


def test_upload_requires_verified_email():
    c = make_client()
    register(c)
    resp = upload(c, ("a.pdf", samples.pdf()))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "email_not_verified"


def test_upload_single_pdf_queues_job(db):
    c = verified_client()
    resp = upload(c, ("Invoice 001.pdf", samples.pdf(pages=2)), name="March")
    assert resp.status_code == 201, resp.text
    batch = resp.json()
    assert batch["name"] == "March"
    assert batch["status"] == "processing"
    assert batch["counts"]["accepted"] == 1 and batch["counts"]["queued"] == 1
    item = batch["items"][0]
    assert item["invoice_status"] == "queued"
    assert item["invoice_status_label"] == "Waiting to process"
    invoice = c.get(f"/api/v1/invoices/{item['invoice_id']}").json()
    assert invoice["page_count"] == 2
    assert invoice["content_type"] == "application/pdf"
    assert invoice["can_cancel"] is True
    assert db.query(ExtractionJob).filter_by(status=JobStatus.QUEUED).count() == 1


def test_content_type_detected_from_bytes_not_name():
    c = verified_client()
    set_overrides(c, max_files_per_batch=10)
    resp = upload(
        c,
        ("notes.pdf", b"just some text, not a pdf"),
        ("photo.txt", samples.image("PNG")),
        ("empty.pdf", b""),
        ("broken.pdf", b"%PDF-1.4\n garbage garbage"),
        ("locked.pdf", samples.pdf(encrypt=True)),
        ("tiny.png", samples.image("PNG", size=(10, 10))),
    )
    assert resp.status_code == 201
    codes = [(i["filename"], i["status"], i["rejection_code"]) for i in resp.json()["items"]]
    assert codes == [
        ("notes.pdf", "rejected", "unsupported_file_type"),
        ("photo.txt", "accepted", None),
        ("empty.pdf", "rejected", "empty_file"),
        ("broken.pdf", "rejected", "unreadable_file"),
        ("locked.pdf", "rejected", "encrypted_pdf"),
        ("tiny.png", "rejected", "unreadable_file"),
    ]
    assert resp.json()["status"] == "processing"


def test_file_size_limit_from_plan():
    c = verified_client()
    set_overrides(c, max_file_size_mb=1)
    big = samples.pdf() + b"\n%" + b"0" * (1024 * 1024)
    item = upload(c, ("big.pdf", big)).json()["items"][0]
    assert item["rejection_code"] == "file_too_large"


def test_batch_limit_enforced_on_backend():
    c = verified_client()  # free plan: 1 file per upload
    resp = upload(c, ("a.pdf", samples.pdf()), ("b.pdf", samples.pdf()))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "batch_limit_exceeded"


def test_zip_requires_entitlement():
    c = verified_client()
    item = upload(c, ("batch.zip", samples.zip_of({"a.pdf": samples.pdf()}))).json()["items"][0]
    assert item["rejection_code"] == "zip_not_allowed"


def test_zip_upload_is_inspected_safely():
    c = verified_client()
    set_overrides(c, zip_upload=True, max_files_per_batch=10)
    archive = samples.zip_of(
        {
            "march/inv-1.pdf": samples.pdf(),
            "../../etc/inv-2.png": samples.image("PNG"),
            "__MACOSX/._inv-1.pdf": b"junk",
            ".DS_Store": b"junk",
            "inner.zip": samples.zip_of({"x.pdf": samples.pdf()}),
            "bomb.pdf": b"%PDF-1.4" + b"\0" * (5 * 1024 * 1024),
        }
    )
    batch = upload(c, ("march.zip", archive)).json()
    items = {
        i["filename"]: (i["status"], i["rejection_code"], i["archive_name"]) for i in batch["items"]
    }
    assert items == {
        "inv-1.pdf": ("accepted", None, "march.zip"),
        "inv-2.png": ("accepted", None, "march.zip"),
        "inner.zip": ("rejected", "nested_archive", "march.zip"),
        "bomb.pdf": ("rejected", "archive_invalid", "march.zip"),
    }


def test_zip_entry_count_counts_against_batch_limit():
    c = verified_client()
    set_overrides(c, zip_upload=True, max_files_per_batch=2)
    archive = samples.zip_of({f"{i}.pdf": samples.pdf() for i in range(3)})
    item = upload(c, ("many.zip", archive)).json()["items"][0]
    assert item["rejection_code"] == "batch_limit_exceeded"


def test_corrupt_zip_rejected():
    c = verified_client()
    set_overrides(c, zip_upload=True)
    item = upload(c, ("bad.zip", b"PK\x03\x04garbage")).json()["items"][0]
    assert item["rejection_code"] == "archive_invalid"


def test_heic_is_converted_for_processing(db):
    c = verified_client()
    item = upload(c, ("photo.heic", samples.image("HEIC"))).json()["items"][0]
    assert item["status"] == "accepted", item
    invoice = db.get(Invoice, uuid.UUID(item["invoice_id"]))
    assert invoice.file.content_type == "image/heic"
    assert invoice.processing_file.content_type == "image/jpeg"
    assert invoice.processing_file.parent_file_id == invoice.file_id


def test_duplicate_detection_and_keep_both():
    c = verified_client()
    data = samples.pdf()
    first = upload(c, ("a.pdf", data)).json()["items"][0]
    second = upload(c, ("a-copy.pdf", data)).json()["items"][0]
    assert second["status"] == "duplicate"
    assert second["duplicate_of_invoice_id"] == first["invoice_id"]
    third = upload(c, ("a-again.pdf", data), allow_duplicates=True).json()["items"][0]
    assert third["status"] == "accepted"


def test_duplicate_within_same_upload():
    c = verified_client()
    set_overrides(c, max_files_per_batch=5)
    data = samples.pdf()
    items = upload(c, ("a.pdf", data), ("b.pdf", data)).json()["items"]
    assert [i["status"] for i in items] == ["accepted", "rejected"]
    assert items[1]["rejection_code"] == "duplicate_in_upload"


def test_duplicate_detection_is_workspace_scoped():
    data = samples.pdf()
    a = verified_client("a@example.com")
    b = verified_client("b@example.com")
    assert upload(a, ("x.pdf", data)).json()["items"][0]["status"] == "accepted"
    assert upload(b, ("x.pdf", data)).json()["items"][0]["status"] == "accepted"


def test_credit_check_counts_pending_jobs():
    c = verified_client()  # 10 free credits
    set_overrides(c, max_files_per_batch=20)
    resp = upload(c, *[(f"{i}.pdf", samples.pdf()) for i in range(11)])
    assert resp.status_code == 402
    assert resp.json()["error"]["details"] == {"available": 10, "required": 11}
    assert upload(c, *[(f"{i}.pdf", samples.pdf()) for i in range(6)]).status_code == 201
    assert upload(c, *[(f"{i}.pdf", samples.pdf()) for i in range(4)]).status_code == 201
    resp = upload(c, ("one-more.pdf", samples.pdf()))
    assert resp.status_code == 402
    with SessionLocal() as db:  # nothing stored for the rejected request
        assert db.query(InvoiceFile).count() == 10


def test_tenant_isolation_for_batches_and_invoices():
    a = verified_client("a@example.com")
    b = verified_client("b@example.com")
    batch = upload(a, ("x.pdf", samples.pdf())).json()
    invoice_id = batch["items"][0]["invoice_id"]
    assert b.get(f"/api/v1/batches/{batch['id']}").status_code == 404
    assert b.get(f"/api/v1/invoices/{invoice_id}").status_code == 404
    assert b.get(f"/api/v1/invoices/{invoice_id}/download").status_code == 404
    assert b.post(f"/api/v1/invoices/{invoice_id}/cancel").status_code == 404
    assert b.delete(f"/api/v1/invoices/{invoice_id}").status_code == 404
    assert b.get("/api/v1/invoices").json()["total"] == 0


def test_signed_download_link():
    c = verified_client()
    data = samples.pdf()
    invoice_id = upload(c, ("My Invoice.pdf", data)).json()["items"][0]["invoice_id"]
    link = c.get(f"/api/v1/invoices/{invoice_id}/download").json()
    path = link["url"].split("localhost:8000", 1)[1]
    anon = make_client()
    resp = anon.get(path)
    assert resp.status_code == 200
    assert resp.content == data
    assert resp.headers["content-type"] == "application/pdf"
    assert 'attachment; filename="My Invoice.pdf"' in resp.headers["content-disposition"]
    assert resp.headers["cache-control"] == "private, no-store"
    tampered = path[:-3] + ("AAA" if not path.endswith("AAA") else "BBB")
    assert anon.get(tampered).status_code == 403


def test_expired_signed_link_rejected():
    import time

    from app.services.storage import sign_payload

    token = sign_payload(
        {"k": "x", "f": "x.pdf", "t": "application/pdf", "exp": int(time.time()) - 1}
    )
    assert make_client().get("/api/v1/files/signed", params={"token": token}).status_code == 403


def test_storage_keys_cannot_escape_root(tmp_path):
    import pytest

    from app.services.storage import LocalStorage, StorageError

    store = LocalStorage(str(tmp_path))
    with pytest.raises(StorageError):
        store.put("../../etc/passwd", b"x", "text/plain")


def test_uploaded_bytes_are_stored_privately(db):
    from app.services.storage import get_storage

    c = verified_client()
    data = samples.pdf()
    invoice_id = upload(c, ("a.pdf", data)).json()["items"][0]["invoice_id"]
    invoice = db.get(Invoice, uuid.UUID(invoice_id))
    assert get_storage().get(invoice.file.storage_key) == data
    assert invoice.file.storage_key.startswith(f"workspaces/{invoice.workspace_id}/")
    assert io.BytesIO(data).getbuffer().nbytes == invoice.file.size_bytes
