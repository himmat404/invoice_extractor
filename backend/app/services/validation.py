"""Deterministic invoice validation (spec 4.6). Never relies on the AI's own claims.

``validate()`` is a pure function: canonical data + config in, report out. Issue ``severity``:
- ``error``: the invoice needs the customer's review before it should be trusted;
- ``warning``: worth checking, but the data may well be right;
- ``info``: noted for transparency (e.g. a rounding line).
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.models import ValidationStatus
from app.services.ai.schema import CanonicalInvoice

RULES_VERSION = "1.0"

ISO_CURRENCIES = set(
    "AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN "
    "BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD "
    "FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY "
    "KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP "
    "MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR "
    "RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT "
    "TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW "
    "ZWL".split()
)

DEFAULT_REQUIRED = ["invoice_number", "invoice_date", "supplier.name", "grand_total"]

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_GST_STATE_CODES = {f"{n:02d}" for n in range(1, 39)} | {"97", "99"}


@dataclass
class ValidationConfig:
    required_fields: list[str] = field(default_factory=lambda: list(DEFAULT_REQUIRED))
    amount_tolerance: Decimal = Decimal("0.05")
    relative_tolerance: Decimal = Decimal("0.0001")
    rounding_allowance: Decimal = Decimal("1.00")
    today: date | None = None


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    field: str | None = None
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "field": self.field,
        }
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class ValidationReport:
    status: ValidationStatus
    issues: list[Issue]
    computed: dict[str, Any]

    def issues_as_dicts(self) -> list[dict[str, Any]]:
        return [i.as_dict() for i in self.issues]


def gstin_checksum_ok(gstin: str) -> bool:
    total = 0
    for i, ch in enumerate(gstin[:14]):
        value = _GSTIN_CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += value // 36 + value % 36
    return gstin[14] == _GSTIN_CHARS[(36 - total % 36) % 36]


def check_gstin(value: str) -> str | None:
    """Return an issue code for an invalid GSTIN, or None when it is well-formed."""
    v = value.replace(" ", "").upper()
    if not _GSTIN_RE.match(v):
        return "gstin_format"
    if v[:2] not in _GST_STATE_CODES:
        return "gstin_state_code"
    if not gstin_checksum_ok(v):
        return "gstin_checksum"
    return None


def _get(data: CanonicalInvoice, path: str) -> Any:
    obj: Any = data
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(Decimal("0.01")))


class _Validator:
    def __init__(self, data: CanonicalInvoice, cfg: ValidationConfig) -> None:
        self.d = data
        self.cfg = cfg
        self.issues: list[Issue] = []
        self.computed: dict[str, Any] = {}

    def add(self, code, severity, message, field=None, **details) -> None:
        self.issues.append(Issue(code, severity, message, field, details or None))

    def close(self, a: Decimal, b: Decimal) -> bool:
        tolerance = max(self.cfg.amount_tolerance, abs(b) * self.cfg.relative_tolerance)
        return abs(a - b) <= tolerance

    # -- checks ---------------------------------------------------------------------------------

    def required(self) -> None:
        for path in self.cfg.required_fields:
            value = _get(self.d, path)
            if value is None or value == "":
                self.add(
                    "missing_required_field",
                    "error",
                    f"{path.replace('.', ' ').replace('_', ' ').capitalize()} is missing.",
                    path,
                )

    def dates(self) -> None:
        today = self.cfg.today or date.today()
        if self.d.invoice_date and self.d.invoice_date > today + timedelta(days=1):
            self.add(
                "future_invoice_date",
                "warning",
                "The invoice date is in the future.",
                "invoice_date",
            )
        if self.d.invoice_date and self.d.due_date and self.d.due_date < self.d.invoice_date:
            self.add(
                "due_before_invoice_date",
                "warning",
                "The due date is before the invoice date.",
                "due_date",
            )

    def currency(self) -> None:
        if self.d.currency is None:
            self.add(
                "missing_currency",
                "warning",
                "No currency was found. Confirm the currency before exporting.",
                "currency",
            )
        elif self.d.currency not in ISO_CURRENCIES:
            self.add(
                "unknown_currency",
                "warning",
                f"{self.d.currency} isn't a recognised currency code.",
                "currency",
            )

    def tax_ids(self) -> None:
        indian = self.d.currency == "INR" or any(v for v in (self.d.cgst, self.d.sgst, self.d.igst))
        for party in ("supplier", "customer"):
            tax_id = _get(self.d, f"{party}.tax_id")
            if not tax_id:
                continue
            compact = tax_id.replace(" ", "").upper()
            looks_like_gstin = len(compact) == 15 and compact[:2].isdigit()
            if not (indian or looks_like_gstin):
                continue
            problem = check_gstin(compact)
            if problem:
                msg = {
                    "gstin_format": "doesn't look like a valid GSTIN",
                    "gstin_state_code": "has an unknown state code",
                    "gstin_checksum": "fails the GSTIN check digit",
                }[problem]
                self.add(problem, "warning", f"The {party} tax ID {msg}.", f"{party}.tax_id")

    def gst_type(self) -> None:
        intra = bool(self.d.cgst or self.d.sgst)
        inter = bool(self.d.igst)
        if intra and inter:
            self.add(
                "mixed_gst_types",
                "warning",
                "Both CGST/SGST and IGST are present; usually only one applies.",
                "igst",
            )
        if self.d.cgst and self.d.sgst and not self.close(self.d.cgst, self.d.sgst):
            self.add("cgst_sgst_unequal", "warning", "CGST and SGST amounts usually match.", "sgst")
        sup, cus = self.d.supplier.tax_id, self.d.customer.tax_id
        if (
            sup
            and cus
            and len(sup) >= 2
            and len(cus) >= 2
            and sup[:2].isdigit()
            and cus[:2].isdigit()
        ):
            same_state = sup[:2] == cus[:2]
            if same_state and inter and not intra:
                self.add(
                    "gst_type_mismatch",
                    "warning",
                    "Supplier and customer are in the same state but IGST was charged.",
                    "igst",
                )
            if not same_state and intra and not inter:
                self.add(
                    "gst_type_mismatch",
                    "warning",
                    "Supplier and customer are in different states but CGST/SGST was charged.",
                    "cgst",
                )

    def lines(self) -> tuple[Decimal | None, Decimal | None]:
        """Check each line; return (sum of line totals, sum of line taxes)."""
        totals: list[Decimal] = []
        taxes: list[Decimal] = []
        for i, line in enumerate(self.d.line_items):
            path = f"line_items[{i}]"
            if line.quantity is not None and line.quantity <= 0:
                self.add(
                    "non_positive_quantity",
                    "warning",
                    f"Line {i + 1} has a zero or negative quantity.",
                    f"{path}.quantity",
                )
            if line.tax_rate is not None and not (0 <= line.tax_rate <= 100):
                self.add(
                    "invalid_tax_rate",
                    "error",
                    f"Line {i + 1} has an impossible tax rate.",
                    f"{path}.tax_rate",
                )
            base = None
            if line.quantity is not None and line.unit_price is not None:
                base = line.quantity * line.unit_price - (line.discount or 0)
            if base is not None and line.line_total is not None:
                with_tax = base + (line.tax_amount or 0)
                if not (self.close(line.line_total, base) or self.close(line.line_total, with_tax)):
                    self.add(
                        "line_total_mismatch",
                        "warning",
                        f"Line {i + 1}: quantity × unit price doesn't match the line total.",
                        f"{path}.line_total",
                        expected=_money(base),
                        found=_money(line.line_total),
                    )
            if base is not None and line.tax_rate is not None and line.tax_amount is not None:
                expected_tax = base * line.tax_rate / 100
                if not self.close(line.tax_amount, expected_tax):
                    self.add(
                        "line_tax_mismatch",
                        "warning",
                        f"Line {i + 1}: tax amount doesn't match the tax rate.",
                        f"{path}.tax_amount",
                        expected=_money(expected_tax),
                        found=_money(line.tax_amount),
                    )
            if line.line_total is not None:
                totals.append(line.line_total)
            elif base is not None:
                totals.append(base)
            if line.tax_amount is not None:
                taxes.append(line.tax_amount)
        lines_total = sum(totals, Decimal(0)) if totals else None
        lines_tax = sum(taxes, Decimal(0)) if taxes else None
        self.computed["lines_total"] = _money(lines_total)
        self.computed["lines_tax"] = _money(lines_tax)
        return lines_total, lines_tax

    def subtotal(self, lines_total: Decimal | None, lines_tax: Decimal | None) -> None:
        if self.d.subtotal is None or lines_total is None:
            return
        candidates = [lines_total]
        if lines_tax is not None:
            candidates.append(lines_total - lines_tax)
        if not any(self.close(self.d.subtotal, c) for c in candidates):
            self.add(
                "subtotal_mismatch",
                "warning",
                "The line items don't add up to the subtotal.",
                "subtotal",
                expected=_money(lines_total),
                found=_money(self.d.subtotal),
            )

    def tax_total(self) -> Decimal | None:
        listed = [t.amount for t in self.d.taxes if t.amount is not None]
        split = [v for v in (self.d.cgst, self.d.sgst, self.d.igst) if v is not None]
        taxes_sum = sum(listed, Decimal(0)) if listed else None
        if taxes_sum is None and split:
            taxes_sum = sum(split, Decimal(0))
        if (
            self.d.total_tax is not None
            and taxes_sum is not None
            and not self.close(self.d.total_tax, taxes_sum)
        ):
            self.add(
                "tax_total_mismatch",
                "warning",
                "The individual taxes don't add up to the total tax.",
                "total_tax",
                expected=_money(taxes_sum),
                found=_money(self.d.total_tax),
            )
        by_type = {t.type: t.amount for t in self.d.taxes if t.type and t.amount is not None}
        for name in ("cgst", "sgst", "igst"):
            value = getattr(self.d, name)
            listed_value = by_type.get(name.upper())
            if (
                value is not None
                and listed_value is not None
                and not self.close(value, listed_value)
            ):
                self.add(
                    "tax_line_mismatch",
                    "warning",
                    f"{name.upper()} differs between the tax summary and tax lines.",
                    name,
                )
        tax = self.d.total_tax if self.d.total_tax is not None else taxes_sum
        self.computed["tax_total"] = _money(tax)
        return tax

    def grand_total(
        self, lines_total: Decimal | None, lines_tax: Decimal | None, tax: Decimal | None
    ) -> None:
        d = self.d
        if d.grand_total is None:
            return
        if d.grand_total < 0:
            self.add(
                "negative_total",
                "warning",
                "The grand total is negative (is this a credit note?).",
                "grand_total",
            )
        base = d.subtotal
        tax_in_base = False
        if base is None and lines_total is not None:
            base = lines_total
            # Line totals may already include tax.
            tax_in_base = lines_tax is not None and tax is not None and self.close(lines_tax, tax)
        if base is None:
            return
        extras = (d.shipping or 0) + (d.other_charges or 0)
        tax_value = tax or 0
        discount = d.total_discount or 0
        candidates = {
            base + extras + tax_value - discount,
            base + extras + tax_value,  # subtotal already net of discount
        }
        if tax_in_base:
            candidates |= {base + extras - discount, base + extras}
        expected = base + extras + (0 if tax_in_base else tax_value) - discount
        self.computed["expected_grand_total"] = _money(expected)
        if any(self.close(d.grand_total, c) for c in candidates):
            return
        diff = min(abs(d.grand_total - c) for c in candidates)
        if diff <= self.cfg.rounding_allowance and d.grand_total == d.grand_total.to_integral():
            self.add(
                "rounded_total",
                "info",
                "The grand total includes a rounding adjustment.",
                "grand_total",
                difference=_money(diff),
            )
            return
        self.add(
            "grand_total_mismatch",
            "error",
            "Subtotal, taxes, charges and discounts don't add up to the grand total.",
            "grand_total",
            expected=_money(expected),
            found=_money(d.grand_total),
        )

    def balance(self) -> None:
        d = self.d
        if d.grand_total is None or d.amount_paid is None or d.balance_due is None:
            return
        if not self.close(d.balance_due, d.grand_total - d.amount_paid):
            self.add(
                "balance_mismatch",
                "warning",
                "Balance due doesn't equal the total minus the amount paid.",
                "balance_due",
            )

    def parse_warnings(self) -> None:
        for warning in self.d.parse_warnings[:20]:
            path = warning.split(":", 1)[0]
            self.add(
                "unreadable_value",
                "warning",
                f"A value for {path} couldn't be read and was left blank.",
                path,
            )


def validate(data: CanonicalInvoice, cfg: ValidationConfig | None = None) -> ValidationReport:
    v = _Validator(data, cfg or ValidationConfig())
    nothing_found = (
        data.invoice_number is None
        and data.grand_total is None
        and not data.line_items
        and data.supplier.name is None
    )
    if nothing_found:
        v.add(
            "no_invoice_data",
            "error",
            "No invoice details could be found. Is this document an invoice?",
        )
        return ValidationReport(ValidationStatus.FAILED, v.issues, v.computed)
    v.required()
    v.dates()
    v.currency()
    v.tax_ids()
    v.gst_type()
    lines_total, lines_tax = v.lines()
    v.subtotal(lines_total, lines_tax)
    tax = v.tax_total()
    v.grand_total(lines_total, lines_tax, tax)
    v.balance()
    v.parse_warnings()
    severities = {i.severity for i in v.issues}
    if "error" in severities:
        status = ValidationStatus.NEEDS_REVIEW
    elif "warning" in severities:
        status = ValidationStatus.WARNING
    else:
        status = ValidationStatus.PASSED
    return ValidationReport(status, v.issues, v.computed)


def config_from_settings(db) -> ValidationConfig:
    from app.services.settings_store import get_setting

    return ValidationConfig(
        required_fields=list(get_setting(db, "validation.required_fields", DEFAULT_REQUIRED)),
        amount_tolerance=Decimal(str(get_setting(db, "validation.amount_tolerance", "0.05"))),
        relative_tolerance=Decimal(str(get_setting(db, "validation.relative_tolerance", "0.0001"))),
        rounding_allowance=Decimal(str(get_setting(db, "validation.rounding_allowance", "1.00"))),
    )
