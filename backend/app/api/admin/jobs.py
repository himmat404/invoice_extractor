import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentAdmin, client_ip, request_id, require_permission
from app.core.db import get_db
from app.core.errors import AppError, NotFoundError
from app.models import ExtractionJob, JobKind, JobStatus
from app.models.base import utcnow
from app.schemas.common import Page
from app.schemas.invoices import AdminJobDetailOut, AdminJobOut, QueueStatsOut
from app.services import jobs
from app.services.audit import record_audit

router = APIRouter(prefix="/jobs", tags=["admin-jobs"])


def _detail(job: ExtractionJob) -> AdminJobDetailOut:
    return AdminJobDetailOut(
        **AdminJobOut.model_validate(job, from_attributes=True).model_dump(),
        attempt_log=job.attempt_log or [],
        invoice_status=job.invoice.status,
        filename=job.invoice.file.filename,
    )


@router.get("/stats", response_model=QueueStatsOut)
def queue_stats(
    _: CurrentAdmin = Depends(require_permission("jobs.read")), db: Session = Depends(get_db)
) -> QueueStatsOut:
    by_status = {
        s.value: n
        for s, n in db.execute(
            select(ExtractionJob.status, func.count()).group_by(ExtractionJob.status)
        )
    }
    now = utcnow()
    ready = select(ExtractionJob).where(
        ExtractionJob.status == JobStatus.QUEUED, ExtractionJob.available_at <= now
    )
    oldest = db.scalar(select(func.min(ready.subquery().c.available_at)))
    failed_24h = db.scalar(
        select(func.count())
        .select_from(ExtractionJob)
        .where(
            ExtractionJob.status == JobStatus.FAILED,
            ExtractionJob.finished_at >= now - timedelta(hours=24),
        )
    )
    return QueueStatsOut(
        by_status=by_status,
        ready_now=db.scalar(select(func.count()).select_from(ready.subquery())) or 0,
        oldest_ready_seconds=(now - oldest).total_seconds() if oldest else None,
        failed_last_24h=failed_24h or 0,
    )


@router.get("", response_model=Page[AdminJobOut])
def list_jobs(
    status: JobStatus | None = None,
    workspace_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
    error_code: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: CurrentAdmin = Depends(require_permission("jobs.read")),
    db: Session = Depends(get_db),
) -> Page[AdminJobOut]:
    stmt = select(ExtractionJob)
    if status:
        stmt = stmt.where(ExtractionJob.status == status)
    if workspace_id:
        stmt = stmt.where(ExtractionJob.workspace_id == workspace_id)
    if invoice_id:
        stmt = stmt.where(ExtractionJob.invoice_id == invoice_id)
    if error_code:
        stmt = stmt.where(ExtractionJob.error_code == error_code)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(ExtractionJob.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[AdminJobOut.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=AdminJobDetailOut)
def get_job(
    job_id: uuid.UUID,
    _: CurrentAdmin = Depends(require_permission("jobs.read")),
    db: Session = Depends(get_db),
) -> AdminJobDetailOut:
    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise NotFoundError("Job not found.")
    return _detail(job)


def _audit(db, request, admin, action, job, **summary):
    record_audit(
        db,
        action=action,
        actor_admin_id=admin.admin.id,
        entity_type="extraction_job",
        entity_id=job.id,
        workspace_id=job.workspace_id,
        summary=summary or None,
        request_id=request_id(request),
        ip_address=client_ip(request),
    )


@router.post("/{job_id}/retry", response_model=AdminJobDetailOut)
def retry_job(
    job_id: uuid.UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("jobs.manage")),
    db: Session = Depends(get_db),
) -> AdminJobDetailOut:
    job = db.get(ExtractionJob, job_id)
    if job is None:
        raise NotFoundError("Job not found.")
    if job.status not in (JobStatus.FAILED, JobStatus.CANCELED):
        raise AppError("Only failed or canceled jobs can be retried.", code="not_retryable")
    invoice = job.invoice
    if invoice.deleted_at is not None:
        raise AppError("The invoice was deleted by the customer.", code="invoice_deleted")
    kind = JobKind.REPROCESS if invoice.extraction_result is not None else JobKind.INITIAL
    new_job = jobs.enqueue(db, invoice, kind=kind, requested_by_admin_id=admin.admin.id)
    _audit(db, request, admin, "job.retried", job, new_job_id=str(new_job.id))
    db.commit()
    return _detail(new_job)


@router.post("/{job_id}/cancel", response_model=AdminJobDetailOut)
def cancel_job(
    job_id: uuid.UUID,
    request: Request,
    admin: CurrentAdmin = Depends(require_permission("jobs.manage")),
    db: Session = Depends(get_db),
) -> AdminJobDetailOut:
    job = db.scalar(select(ExtractionJob).where(ExtractionJob.id == job_id).with_for_update())
    if job is None:
        raise NotFoundError("Job not found.")
    if not jobs.cancel(db, job):
        raise AppError("Only queued jobs can be canceled.", code="not_cancelable")
    _audit(db, request, admin, "job.canceled", job)
    db.commit()
    return _detail(job)
