from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PromptVersion
from app.models.base import utcnow
from app.services.ai.schema import EXTRACTION_SCHEMA_VERSION

EXTRACTION_PROMPT = "invoice_extraction"

DEFAULT_SYSTEM_PROMPT = """You extract structured data from business invoices.

Rules:
- Only report values that are printed on the document. If a field is absent, unclear or not
  applicable, return null. Never guess, infer or invent values.
- Copy identifiers (invoice number, tax IDs, codes) exactly as printed.
- Dates: YYYY-MM-DD. If the day/month order is ambiguous, use the country implied by the
  addresses or tax IDs; if still ambiguous, return null.
- Amounts: plain decimal strings with no currency symbols or thousands separators ("1234.50").
  Negative amounts use a leading minus sign.
- Currency: ISO 4217 code (INR, USD, EUR...). Use null if no currency is shown or implied by
  a symbol.
- Indian GST: report CGST, SGST and IGST separately both in "taxes" and in the cgst/sgst/igst
  fields. Use "taxes" for all other tax lines (VAT, CESS...).
- Include every line item in order. Do not merge or split lines.
- field_confidence: for each field you extracted, your confidence from 0 to 1 that the value is
  correct. Omit fields you returned as null.
"""

DEFAULT_USER_PROMPT = (
    "Extract the invoice data from the attached document and respond with JSON matching the "
    "provided schema."
)


def active_prompt(db: Session, name: str = EXTRACTION_PROMPT) -> PromptVersion:
    """Return the active prompt version, creating the built-in default on first use."""
    prompt = db.scalar(
        select(PromptVersion).where(PromptVersion.name == name, PromptVersion.is_active.is_(True))
    )
    if prompt is None:
        latest = db.scalar(
            select(PromptVersion.version)
            .where(PromptVersion.name == name)
            .order_by(PromptVersion.version.desc())
            .limit(1)
        )
        prompt = PromptVersion(
            name=name,
            version=(latest or 0) + 1,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            user_prompt=DEFAULT_USER_PROMPT,
            schema_version=EXTRACTION_SCHEMA_VERSION,
            is_active=True,
            notes="Built-in default",
            activated_at=utcnow(),
        )
        db.add(prompt)
        db.flush()
    return prompt
