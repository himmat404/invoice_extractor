"""Upload intake: validation, duplicate detection, credit checks, storage and queueing
(spec 4.3, 19 steps 4-5, 21.3)."""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import (
    Batch,
    BatchItem,
    BatchItemStatus,
    BatchSource,
    FileKind,
    Invoice,
    InvoiceFile,
    InvoiceStatus,
    User,
    Workspace,
)
from app.services import duplicates, files, jobs
from app.services.audit import record_activity
from app.services.credits import InsufficientCreditsError
from app.services.storage import get_storage
from app.services.subscriptions import check_can_process

MB = 1024 * 1024


@dataclass
class IncomingFile:
    filename: str
    data: bytes | None  # None when the upload exceeded the read limit
    truncated: bool = False


@dataclass
class _Candidate:
    filename: str
    archive_name: str | None
    data: bytes | None = None
    rejection: files.FileRejected | None = None
    ftype: files.FileType | None = None
    page_count: int | None = None
    sha256: str | None = None
    duplicate_of: uuid.UUID | None = None
    converted: bytes | None = None  # JPEG rendition of a HEIC upload


def read_limit_bytes() -> int:
    return get_settings().upload_hard_max_file_mb * MB


def _expand(
    incoming: list[IncomingFile], *, max_bytes: int, zip_allowed: bool, max_files: int
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for item in incoming:
        name = files.safe_filename(item.filename)
        if item.truncated or item.data is None:
            candidates.append(_Candidate(name, None, rejection=files.too_large(max_bytes)))
            continue
        if files.detect_type(item.data) is files.ZIP:
            if not zip_allowed:
                candidates.append(
                    _Candidate(
                        name,
                        None,
                        rejection=files.FileRejected(
                            "zip_not_allowed", "ZIP uploads aren't included in your plan."
                        ),
                    )
                )
                continue
            try:
                for entry in files.iter_zip(
                    item.data, max_entries=max_files, max_entry_bytes=max_bytes
                ):
                    candidates.append(
                        _Candidate(entry.name, name, data=entry.data, rejection=entry.rejection)
                    )
            except files.FileRejected as exc:
                candidates.append(_Candidate(name, None, rejection=exc))
            continue
        candidates.append(_Candidate(name, None, data=item.data))
    return candidates


def _validate(c: _Candidate, max_bytes: int) -> None:
    if c.rejection or c.data is None:
        return
    try:
        if not c.data:
            raise files.FileRejected("empty_file", "This file is empty.")
        if len(c.data) > max_bytes:
            raise files.too_large(max_bytes)
        ftype = files.detect_type(c.data)
        if ftype is None or ftype is files.ZIP:
            raise files.FileRejected(
                "unsupported_file_type", "Unsupported file type. Upload PDF, JPG, PNG or HEIC."
            )
        c.ftype = ftype
        if ftype is files.HEIC:
            c.converted = files.convert_heic_to_jpeg(c.data)
            files.inspect_image(c.converted)
            c.page_count = 1
        else:
            c.page_count = files.inspect_document(c.data, ftype)
        c.sha256 = hashlib.sha256(c.data).hexdigest()
    except files.FileRejected as exc:
        c.rejection = exc


def _find_duplicate(db: Session, workspace_id: uuid.UUID, sha256: str) -> uuid.UUID | None:
    """Exact file match within this workspace only (never across tenants)."""
    return db.scalar(
        select(Invoice.id)
        .join(InvoiceFile, InvoiceFile.id == Invoice.file_id)
        .where(
            Invoice.workspace_id == workspace_id,
            Invoice.deleted_at.is_(None),
            InvoiceFile.sha256 == sha256,
        )
        .order_by(Invoice.created_at)
        .limit(1)
    )


def _store(
    db: Session,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID | None,
    c: _Candidate,
    data: bytes,
    ftype: files.FileType,
    kind: FileKind,
    parent: InvoiceFile | None = None,
) -> InvoiceFile:
    file_id = uuid.uuid4()
    key = f"workspaces/{workspace_id}/invoices/{file_id}/{kind.value}.{ftype.extension}"
    get_storage().put(key, data, ftype.content_type)
    record = InvoiceFile(
        id=file_id,
        workspace_id=workspace_id,
        kind=kind,
        parent_file_id=parent.id if parent else None,
        storage_key=key,
        filename=c.filename
        if kind == FileKind.ORIGINAL
        else c.filename.rsplit(".", 1)[0] + f".{ftype.extension}",
        content_type=ftype.content_type,
        size_bytes=len(data),
        sha256=c.sha256 if kind == FileKind.ORIGINAL else hashlib.sha256(data).hexdigest(),
        page_count=c.page_count,
        uploaded_by_user_id=user_id,
    )
    db.add(record)
    db.flush()
    return record


def create_batch(
    db: Session,
    *,
    workspace: Workspace,
    user: User | None,
    incoming: list[IncomingFile],
    allow_duplicates: bool = False,
    name: str | None = None,
    source: BatchSource = BatchSource.WEB,
    request_id: str | None = None,
) -> Batch:
    if not incoming:
        raise AppError("Choose at least one file to upload.", code="no_files")
    eligibility = check_can_process(db, workspace.id, count=0)
    ent = eligibility.entitlements
    max_bytes = min(ent.max_file_size_mb, get_settings().upload_hard_max_file_mb) * MB

    candidates = _expand(
        incoming, max_bytes=max_bytes, zip_allowed=ent.zip_upload, max_files=ent.max_files_per_batch
    )
    if len(candidates) > ent.max_files_per_batch:
        raise AppError(
            f"Your plan allows {ent.max_files_per_batch} file(s) per upload.",
            code="batch_limit_exceeded",
            details={"limit": ent.max_files_per_batch, "received": len(candidates)},
        )
    for c in candidates:
        _validate(c, max_bytes)

    exact_rule = duplicates.exact_file_rule_active(db, workspace.id)
    seen: dict[str, int] = {}
    for idx, c in enumerate(candidates):
        if c.rejection or c.sha256 is None or allow_duplicates or not exact_rule:
            continue
        c.duplicate_of = _find_duplicate(db, workspace.id, c.sha256)
        if c.duplicate_of is None and c.sha256 in seen:
            c.rejection = files.FileRejected(
                "duplicate_in_upload", "This file appears more than once in this upload."
            )
        seen.setdefault(c.sha256, idx)

    accepted = [c for c in candidates if not c.rejection and not c.duplicate_of]
    if accepted:
        available = eligibility.available_credits - jobs.pending_uncharged_jobs(db, workspace.id)
        if available < len(accepted) and not ent.overage_allowed:
            raise InsufficientCreditsError(
                "You don't have enough invoice credits for this upload. "
                "Upgrade your plan, buy credits, or upload fewer files.",
                details={"available": max(available, 0), "required": len(accepted)},
            )

    batch = Batch(
        workspace_id=workspace.id,
        created_by_user_id=user.id if user else None,
        name=name,
        source=source,
        file_count=len(candidates),
    )
    db.add(batch)
    db.flush()
    user_id = user.id if user else None
    for position, c in enumerate(candidates):
        item = BatchItem(
            batch_id=batch.id,
            workspace_id=workspace.id,
            position=position,
            filename=c.filename,
            archive_name=c.archive_name,
            status=BatchItemStatus.ACCEPTED,
        )
        if c.rejection:
            item.status = BatchItemStatus.REJECTED
            item.rejection_code = c.rejection.code
            item.rejection_message = c.rejection.message
        elif c.duplicate_of:
            item.status = BatchItemStatus.DUPLICATE
            item.duplicate_of_invoice_id = c.duplicate_of
            item.rejection_code = "duplicate_file"
            item.rejection_message = (
                "This file was already uploaded. Upload again with “keep both” to process it."
            )
        else:
            original = _store(db, workspace.id, user_id, c, c.data, c.ftype, FileKind.ORIGINAL)
            processing = None
            if c.ftype is files.HEIC:
                processing = _store(
                    db,
                    workspace.id,
                    user_id,
                    c,
                    files.convert_heic_to_jpeg(c.data),
                    files.JPEG,
                    FileKind.DERIVED,
                    parent=original,
                )
            invoice = Invoice(
                workspace_id=workspace.id,
                batch_id=batch.id,
                file_id=original.id,
                processing_file_id=processing.id if processing else None,
                uploaded_by_user_id=user_id,
                status=InvoiceStatus.UPLOADED,
            )
            db.add(invoice)
            db.flush()
            item.invoice_id = invoice.id
            jobs.enqueue(db, invoice)
        db.add(item)
    counts = {s.value: 0 for s in BatchItemStatus}
    for c in candidates:
        key = "rejected" if c.rejection else "duplicate" if c.duplicate_of else "accepted"
        counts[key] += 1
    record_activity(
        db,
        workspace_id=workspace.id,
        actor_user_id=user_id,
        actor_type="user" if source == BatchSource.WEB else "api",
        action="batch.created",
        entity_type="batch",
        entity_id=batch.id,
        summary=counts,
        request_id=request_id,
    )
    db.flush()
    return batch
