"""Confidence bands (spec 21.2). Bands prioritise review; they never claim correctness.

Field confidence is only what the provider supplied; missing values are reported as
"unavailable", never invented. The overall indicator is defined (method ``min_review_fields_v1``)
as the lowest confidence among the review fields, and is unavailable unless every review field
that has a value also has a confidence.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConfidenceThresholdVersion

OVERALL_METHOD = "min_review_fields_v1"
DEFAULT_REVIEW_FIELDS = [
    "invoice_number",
    "invoice_date",
    "supplier.name",
    "grand_total",
    "total_tax",
    "currency",
]


def active_thresholds(db: Session) -> ConfidenceThresholdVersion:
    current = db.scalar(
        select(ConfidenceThresholdVersion).where(ConfidenceThresholdVersion.is_active.is_(True))
    )
    if current is None:
        current = ConfidenceThresholdVersion(
            version=1,
            high_min=0.9,
            medium_min=0.7,
            review_fields=list(DEFAULT_REVIEW_FIELDS),
            is_active=True,
            notes="Built-in default",
        )
        db.add(current)
        db.flush()
    return current


def band(value: float | None, t: ConfidenceThresholdVersion) -> str:
    if value is None:
        return "unavailable"
    if value >= t.high_min:
        return "high"
    if value >= t.medium_min:
        return "medium"
    return "low"


def summarize(
    field_confidence: dict[str, float],
    present_fields: set[str],
    t: ConfidenceThresholdVersion,
    method: str | None,
) -> dict[str, Any]:
    bands = {f: band(v, t) for f, v in field_confidence.items()}
    low = sorted(f for f, b in bands.items() if b == "low")
    low_review = sorted(f for f in low if f in t.review_fields)
    review_present = [f for f in t.review_fields if f in present_fields]
    overall = None
    if review_present and all(f in field_confidence for f in review_present):
        overall = min(field_confidence[f] for f in review_present)
    return {
        "method": method,
        "threshold_version": t.version,
        "overall": overall,
        "overall_band": band(overall, t),
        "overall_method": OVERALL_METHOD,
        "fields": bands,
        "low_fields": low,
        "low_review_fields": low_review,
        "available": bool(field_confidence),
    }
