"""Canonical invoice schema and safe parsing of model output (spec 4.4, 9).

Every field is optional: anything absent from the document stays ``null`` instead of being
invented. Monetary values are ``Decimal`` (serialised as strings) — never floats. Values the model
returns in an unusable shape are dropped to ``null`` and reported in ``parse_warnings``.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

EXTRACTION_SCHEMA_VERSION = "1.0"
CONFIDENCE_METHOD = "model_self_reported_v1"


class Party(BaseModel):
    name: str | None = None
    address: str | None = None
    tax_id: str | None = None


class BankDetails(BaseModel):
    account_name: str | None = None
    account_number: str | None = None
    bank_name: str | None = None
    ifsc_or_swift: str | None = None
    upi_id: str | None = None


class LineItem(BaseModel):
    description: str | None = None
    sku: str | None = None
    hsn_sac: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    discount: Decimal | None = None
    tax_rate: Decimal | None = None
    tax_amount: Decimal | None = None
    line_total: Decimal | None = None


class TaxLine(BaseModel):
    type: str | None = None  # CGST, SGST, IGST, VAT, GST, CESS, OTHER
    rate: Decimal | None = None
    amount: Decimal | None = None


class CanonicalInvoice(BaseModel):
    model_config = ConfigDict(ser_json_inf_nan="null")

    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    po_number: str | None = None
    reference_number: str | None = None
    currency: str | None = None
    payment_terms: str | None = None
    notes: str | None = None
    supplier: Party = Field(default_factory=Party)
    customer: Party = Field(default_factory=Party)
    bank_details: BankDetails = Field(default_factory=BankDetails)
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    shipping: Decimal | None = None
    other_charges: Decimal | None = None
    total_discount: Decimal | None = None
    taxes: list[TaxLine] = Field(default_factory=list)
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    total_tax: Decimal | None = None
    grand_total: Decimal | None = None
    amount_paid: Decimal | None = None
    balance_due: Decimal | None = None
    # Provider-supplied confidence per field path (0..1). Absent means "unavailable".
    field_confidence: dict[str, float] = Field(default_factory=dict)
    parse_warnings: list[str] = Field(default_factory=list)


# --- JSON schema sent to providers (OpenAPI subset accepted by Gemini) --------------------------


def _s(desc: str) -> dict:
    return {"type": "STRING", "nullable": True, "description": desc}


_MONEY = "Decimal number as a string without currency symbols or thousands separators, e.g. 1234.50"
_PARTY = {
    "type": "OBJECT",
    "nullable": True,
    "properties": {
        "name": _s("Legal or trading name"),
        "address": _s("Full postal address on one line"),
        "tax_id": _s("Tax ID such as GSTIN, VAT number or EIN"),
    },
}

PROVIDER_RESPONSE_SCHEMA: dict = {
    "type": "OBJECT",
    "properties": {
        "invoice_number": _s("Invoice number exactly as printed"),
        "invoice_date": _s("Invoice date as YYYY-MM-DD"),
        "due_date": _s("Payment due date as YYYY-MM-DD"),
        "po_number": _s("Purchase order number"),
        "reference_number": _s("Other reference number"),
        "currency": _s("ISO 4217 currency code, e.g. INR, USD"),
        "payment_terms": _s("Payment terms, e.g. Net 30"),
        "notes": _s("Notes or terms printed on the invoice"),
        "supplier": _PARTY,
        "customer": _PARTY,
        "bank_details": {
            "type": "OBJECT",
            "nullable": True,
            "properties": {
                "account_name": _s("Account holder name"),
                "account_number": _s("Bank account number"),
                "bank_name": _s("Bank name"),
                "ifsc_or_swift": _s("IFSC, SWIFT/BIC or routing code"),
                "upi_id": _s("UPI ID"),
            },
        },
        "line_items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "description": _s("Item description as printed"),
                    "sku": _s("SKU or product code"),
                    "hsn_sac": _s("HSN or SAC code"),
                    "quantity": _s("Quantity as a decimal string"),
                    "unit": _s("Unit of measure"),
                    "unit_price": _s(_MONEY),
                    "discount": _s(_MONEY),
                    "tax_rate": _s("Tax rate in percent as a decimal string, e.g. 18"),
                    "tax_amount": _s(_MONEY),
                    "line_total": _s(_MONEY),
                },
            },
        },
        "subtotal": _s(_MONEY),
        "shipping": _s(_MONEY),
        "other_charges": _s(_MONEY),
        "total_discount": _s(_MONEY),
        "taxes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": _s("CGST, SGST, IGST, VAT, GST, CESS or OTHER"),
                    "rate": _s("Rate in percent as a decimal string"),
                    "amount": _s(_MONEY),
                },
            },
        },
        "cgst": _s(_MONEY),
        "sgst": _s(_MONEY),
        "igst": _s(_MONEY),
        "total_tax": _s(_MONEY),
        "grand_total": _s(_MONEY),
        "amount_paid": _s(_MONEY),
        "balance_due": _s(_MONEY),
        "field_confidence": {
            "type": "ARRAY",
            "description": "Your confidence (0 to 1) for each field you extracted",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "field": {"type": "STRING", "description": "Field path, e.g. grand_total"},
                    "confidence": {"type": "NUMBER"},
                },
            },
        },
    },
}


# --- safe parsing -------------------------------------------------------------------------------

_CURRENCY_SYMBOLS = {"₹": "INR", "€": "EUR", "£": "GBP", "¥": "JPY", "RS": "INR", "RS.": "INR"}
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
)
_NUMBER = re.compile(r"-?\d[\d,\s]*(?:\.\d+)?|-?\.\d+")
_MAX_TEXT = 2000
_MAX_LINES = 500


class _Parser:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def text(self, value: Any, path: str, limit: int = _MAX_TEXT) -> str | None:
        if value is None:
            return None
        if isinstance(value, int | float | Decimal):
            value = str(value)
        if not isinstance(value, str):
            self.warnings.append(f"{path}: expected text")
            return None
        cleaned = " ".join(value.split())
        if cleaned.lower() in {"", "null", "none", "n/a", "na", "-"}:
            return None
        return cleaned[:limit]

    def decimal(self, value: Any, path: str) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        raw = str(value).strip()
        if raw.lower() in {"", "null", "none", "n/a", "na", "-"}:
            return None
        negative = raw.startswith("(") and raw.endswith(")")
        compact = re.sub(r"[,\s]", "", raw.strip("()"))
        try:
            try:
                number = Decimal(compact)
            except InvalidOperation:
                # Tolerate surrounding symbols/words ("Rs. 1,234", "18%"), but only when
                # exactly one number is present.
                match = _NUMBER.search(raw)
                if match is None or re.search(r"\d", raw[: match.start()] + raw[match.end() :]):
                    raise
                number = Decimal(re.sub(r"[,\s]", "", match.group()))
        except InvalidOperation:
            self.warnings.append(f"{path}: not a number")
            return None
        if not number.is_finite() or abs(number) >= Decimal("1e13"):
            self.warnings.append(f"{path}: number out of range")
            return None
        return -number if negative and number > 0 else number

    def date(self, value: Any, path: str) -> date | None:
        raw = self.text(value, path, 64)
        if raw is None:
            return None
        for fmt in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
            if 1990 <= parsed.year <= 2100:
                return parsed
        self.warnings.append(f"{path}: unrecognised date")
        return None

    def currency(self, value: Any, path: str) -> str | None:
        raw = self.text(value, path, 16)
        if raw is None:
            return None
        upper = raw.upper()
        if upper in _CURRENCY_SYMBOLS:
            return _CURRENCY_SYMBOLS[upper]
        if re.fullmatch(r"[A-Z]{3}", upper):
            return upper
        self.warnings.append(f"{path}: not an ISO currency code")
        return None

    def obj(self, value: Any, path: str) -> dict:
        if value is None:
            return {}
        if not isinstance(value, dict):
            self.warnings.append(f"{path}: expected an object")
            return {}
        return value

    def items(self, value: Any, path: str) -> list[dict]:
        if value is None:
            return []
        if not isinstance(value, list):
            self.warnings.append(f"{path}: expected a list")
            return []
        if len(value) > _MAX_LINES:
            self.warnings.append(f"{path}: truncated to {_MAX_LINES} entries")
        return [v for v in value[:_MAX_LINES] if isinstance(v, dict)]


_FIELD_PATHS = set(CanonicalInvoice.model_fields) - {"field_confidence", "parse_warnings"}


def parse_model_output(data: Any) -> CanonicalInvoice:
    """Turn raw model JSON into a CanonicalInvoice. Raises ValueError if it isn't an object."""
    if not isinstance(data, dict):
        raise ValueError("Model output is not a JSON object")
    p = _Parser()

    def party(key: str) -> Party:
        o = p.obj(data.get(key), key)
        return Party(
            name=p.text(o.get("name"), f"{key}.name", 300),
            address=p.text(o.get("address"), f"{key}.address"),
            tax_id=p.text(o.get("tax_id"), f"{key}.tax_id", 64),
        )

    bank = p.obj(data.get("bank_details"), "bank_details")
    lines = []
    for i, item in enumerate(p.items(data.get("line_items"), "line_items")):
        path = f"line_items[{i}]"
        line = LineItem(
            description=p.text(item.get("description"), f"{path}.description"),
            sku=p.text(item.get("sku"), f"{path}.sku", 100),
            hsn_sac=p.text(item.get("hsn_sac"), f"{path}.hsn_sac", 20),
            quantity=p.decimal(item.get("quantity"), f"{path}.quantity"),
            unit=p.text(item.get("unit"), f"{path}.unit", 32),
            unit_price=p.decimal(item.get("unit_price"), f"{path}.unit_price"),
            discount=p.decimal(item.get("discount"), f"{path}.discount"),
            tax_rate=p.decimal(item.get("tax_rate"), f"{path}.tax_rate"),
            tax_amount=p.decimal(item.get("tax_amount"), f"{path}.tax_amount"),
            line_total=p.decimal(item.get("line_total"), f"{path}.line_total"),
        )
        if any(v is not None for v in line.model_dump().values()):
            lines.append(line)
    taxes = []
    for i, t in enumerate(p.items(data.get("taxes"), "taxes")):
        tax_type = p.text(t.get("type"), f"taxes[{i}].type", 16)
        tax = TaxLine(
            type=tax_type.upper() if tax_type else None,
            rate=p.decimal(t.get("rate"), f"taxes[{i}].rate"),
            amount=p.decimal(t.get("amount"), f"taxes[{i}].amount"),
        )
        if tax.amount is not None or tax.rate is not None:
            taxes.append(tax)

    confidence: dict[str, float] = {}
    raw_conf = data.get("field_confidence")
    entries = (
        raw_conf.items()
        if isinstance(raw_conf, dict)
        else (
            ((e.get("field"), e.get("confidence")) for e in raw_conf if isinstance(e, dict))
            if isinstance(raw_conf, list)
            else ()
        )
    )
    for field, value in entries:
        if not isinstance(field, str) or field.split(".")[0].split("[")[0] not in _FIELD_PATHS:
            continue
        if isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= 1:
            confidence[field[:100]] = round(float(value), 4)

    m = p.decimal
    invoice = CanonicalInvoice(
        invoice_number=p.text(data.get("invoice_number"), "invoice_number", 100),
        invoice_date=p.date(data.get("invoice_date"), "invoice_date"),
        due_date=p.date(data.get("due_date"), "due_date"),
        po_number=p.text(data.get("po_number"), "po_number", 100),
        reference_number=p.text(data.get("reference_number"), "reference_number", 100),
        currency=p.currency(data.get("currency"), "currency"),
        payment_terms=p.text(data.get("payment_terms"), "payment_terms", 300),
        notes=p.text(data.get("notes"), "notes"),
        supplier=party("supplier"),
        customer=party("customer"),
        bank_details=BankDetails(
            account_name=p.text(bank.get("account_name"), "bank_details.account_name", 300),
            account_number=p.text(bank.get("account_number"), "bank_details.account_number", 64),
            bank_name=p.text(bank.get("bank_name"), "bank_details.bank_name", 300),
            ifsc_or_swift=p.text(bank.get("ifsc_or_swift"), "bank_details.ifsc_or_swift", 32),
            upi_id=p.text(bank.get("upi_id"), "bank_details.upi_id", 100),
        ),
        line_items=lines,
        subtotal=m(data.get("subtotal"), "subtotal"),
        shipping=m(data.get("shipping"), "shipping"),
        other_charges=m(data.get("other_charges"), "other_charges"),
        total_discount=m(data.get("total_discount"), "total_discount"),
        taxes=taxes,
        cgst=m(data.get("cgst"), "cgst"),
        sgst=m(data.get("sgst"), "sgst"),
        igst=m(data.get("igst"), "igst"),
        total_tax=m(data.get("total_tax"), "total_tax"),
        grand_total=m(data.get("grand_total"), "grand_total"),
        amount_paid=m(data.get("amount_paid"), "amount_paid"),
        balance_due=m(data.get("balance_due"), "balance_due"),
        field_confidence=confidence,
    )
    invoice.parse_warnings = p.warnings[:100]
    return invoice
