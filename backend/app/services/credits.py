"""Credit ledger and balances (spec 5.7).

Every balance change locks the workspace's ``credit_accounts`` row and writes a ledger entry.
Consumption draws from period credits first, then extra (purchased/granted) credits. Operations
that may be retried take an idempotency key so they are applied at most once.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError
from app.models import CreditAccount, CreditBucket, CreditLedgerEntry, LedgerEntryType


class InsufficientCreditsError(AppError):
    status_code = 402
    code = "insufficient_credits"


@dataclass(frozen=True)
class Reference:
    type: str
    id: str


def get_account(db: Session, workspace_id: uuid.UUID) -> CreditAccount:
    account = db.get(CreditAccount, workspace_id)
    return account or CreditAccount(workspace_id=workspace_id, period_balance=0, extra_balance=0)


def lock_account(db: Session, workspace_id: uuid.UUID) -> CreditAccount:
    db.execute(
        insert(CreditAccount)
        .values(workspace_id=workspace_id, period_balance=0, extra_balance=0)
        .on_conflict_do_nothing(index_elements=["workspace_id"])
    )
    return db.scalars(
        select(CreditAccount)
        .where(CreditAccount.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _existing(db: Session, idempotency_key: str | None) -> CreditLedgerEntry | None:
    if idempotency_key is None:
        return None
    return db.scalar(
        select(CreditLedgerEntry).where(CreditLedgerEntry.idempotency_key == idempotency_key)
    )


def _entry(
    db: Session,
    account: CreditAccount,
    *,
    entry_type: LedgerEntryType,
    bucket: CreditBucket,
    amount: int,
    subscription_id: uuid.UUID | None = None,
    reference: Reference | None = None,
    idempotency_key: str | None = None,
    actor_admin_id: uuid.UUID | None = None,
    description: str | None = None,
) -> CreditLedgerEntry:
    entry = CreditLedgerEntry(
        workspace_id=account.workspace_id,
        entry_type=entry_type,
        bucket=bucket,
        amount=amount,
        balance_after=account.total,
        subscription_id=subscription_id,
        reference_type=reference.type if reference else None,
        reference_id=reference.id if reference else None,
        idempotency_key=idempotency_key,
        actor_admin_id=actor_admin_id,
        description=description,
    )
    db.add(entry)
    # Flush so idempotency lookups later in the same transaction see this entry.
    db.flush()
    return entry


def allocate_period_credits(
    db: Session,
    workspace_id: uuid.UUID,
    amount: int,
    *,
    subscription_id: uuid.UUID,
    idempotency_key: str,
    description: str,
) -> None:
    """Expire any remaining period credits, then allocate the new period's credits."""
    account = lock_account(db, workspace_id)
    if _existing(db, idempotency_key):
        return
    if account.period_balance:
        expired = account.period_balance
        account.period_balance = 0
        _entry(
            db,
            account,
            entry_type=LedgerEntryType.EXPIRY,
            bucket=CreditBucket.PERIOD,
            amount=-expired,
            subscription_id=subscription_id,
            description="Unused credits expired at end of billing period",
        )
    account.period_balance = amount
    _entry(
        db,
        account,
        entry_type=LedgerEntryType.ALLOCATION,
        bucket=CreditBucket.PERIOD,
        amount=amount,
        subscription_id=subscription_id,
        idempotency_key=idempotency_key,
        description=description,
    )


def add_extra_credits(
    db: Session,
    workspace_id: uuid.UUID,
    amount: int,
    *,
    entry_type: LedgerEntryType,
    description: str,
    reference: Reference | None = None,
    idempotency_key: str | None = None,
    actor_admin_id: uuid.UUID | None = None,
) -> CreditLedgerEntry:
    if amount <= 0:
        raise AppError("Amount must be positive.", code="invalid_amount")
    account = lock_account(db, workspace_id)
    if existing := _existing(db, idempotency_key):
        return existing
    account.extra_balance += amount
    return _entry(
        db,
        account,
        entry_type=entry_type,
        bucket=CreditBucket.EXTRA,
        amount=amount,
        reference=reference,
        idempotency_key=idempotency_key,
        actor_admin_id=actor_admin_id,
        description=description,
    )


def revoke_credits(
    db: Session,
    workspace_id: uuid.UUID,
    amount: int,
    bucket: CreditBucket,
    *,
    description: str,
    actor_admin_id: uuid.UUID | None,
) -> CreditLedgerEntry:
    if amount <= 0:
        raise AppError("Amount must be positive.", code="invalid_amount")
    if bucket == CreditBucket.OVERAGE:
        raise AppError("Overage credits cannot be revoked.", code="invalid_bucket")
    account = lock_account(db, workspace_id)
    attr = "period_balance" if bucket == CreditBucket.PERIOD else "extra_balance"
    if getattr(account, attr) < amount:
        raise ConflictError(
            "Cannot revoke more credits than the balance holds.", code="balance_too_low"
        )
    setattr(account, attr, getattr(account, attr) - amount)
    return _entry(
        db,
        account,
        entry_type=LedgerEntryType.REVOKE,
        bucket=bucket,
        amount=-amount,
        actor_admin_id=actor_admin_id,
        description=description,
    )


def consume_credit(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    idempotency_key: str,
    reference: Reference,
    subscription_id: uuid.UUID | None,
    overage_allowed: bool,
) -> CreditLedgerEntry:
    """Consume one credit for a successfully processed item. Safe to call more than once."""
    account = lock_account(db, workspace_id)
    if existing := _existing(db, idempotency_key):
        return existing
    if account.period_balance > 0:
        account.period_balance -= 1
        bucket = CreditBucket.PERIOD
    elif account.extra_balance > 0:
        account.extra_balance -= 1
        bucket = CreditBucket.EXTRA
    elif overage_allowed:
        bucket = CreditBucket.OVERAGE
    else:
        raise InsufficientCreditsError(
            "You've used all your invoice credits. Upgrade your plan or buy more credits."
        )
    return _entry(
        db,
        account,
        entry_type=LedgerEntryType.CONSUMPTION,
        bucket=bucket,
        amount=-1,
        subscription_id=subscription_id,
        reference=reference,
        idempotency_key=idempotency_key,
        description="Invoice processed",
    )


def reverse_consumption(db: Session, workspace_id: uuid.UUID, consume_key: str) -> bool:
    """Refund a consumption (e.g. charge recorded but the job was later classed as failed)."""
    account = lock_account(db, workspace_id)
    original = _existing(db, consume_key)
    reversal_key = f"reverse:{consume_key}"
    if original is None or original.workspace_id != workspace_id or _existing(db, reversal_key):
        return False
    if original.bucket == CreditBucket.PERIOD:
        account.period_balance += 1
    elif original.bucket == CreditBucket.EXTRA:
        account.extra_balance += 1
    reference = (
        Reference(original.reference_type, original.reference_id)
        if original.reference_type and original.reference_id
        else None
    )
    _entry(
        db,
        account,
        entry_type=LedgerEntryType.REVERSAL,
        bucket=original.bucket,
        amount=1,
        subscription_id=original.subscription_id,
        reference=reference,
        idempotency_key=reversal_key,
        description="Credit returned for failed processing",
    )
    return True
