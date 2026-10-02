import json
from datetime import date
from decimal import Decimal

import pytest

from app.services.ai.providers import FAKE_INVOICE
from app.services.ai.schema import CanonicalInvoice, parse_model_output


def test_parses_full_invoice():
    inv = parse_model_output(json.loads(json.dumps(FAKE_INVOICE)))
    assert inv.invoice_number == "INV-1001"
    assert inv.invoice_date == date(2026, 9, 15)
    assert inv.grand_total == Decimal("2950.00")
    assert inv.line_items[0].quantity == Decimal("10")
    assert [t.type for t in inv.taxes] == ["CGST", "SGST"]
    assert inv.field_confidence == {"grand_total": 0.97, "invoice_number": 0.99}
    assert inv.parse_warnings == []


def test_missing_fields_stay_null():
    inv = parse_model_output({"invoice_number": "A-1"})
    assert inv.invoice_number == "A-1"
    assert inv.grand_total is None and inv.invoice_date is None
    assert inv.supplier.name is None and inv.line_items == []
    assert inv.field_confidence == {}  # unavailable, not invented


def test_bad_values_become_null_with_warnings():
    inv = parse_model_output(
        {
            "invoice_number": {"nested": True},
            "invoice_date": "sometime last week",
            "currency": "Rupees",
            "grand_total": "about a thousand",
            "subtotal": "1e20",
            "supplier": "Acme",
            "line_items": "lots",
        }
    )
    assert inv.invoice_number is None
    assert inv.invoice_date is None
    assert inv.currency is None
    assert inv.grand_total is None and inv.subtotal is None
    assert len(inv.parse_warnings) == 7


def test_normalizes_common_formats():
    inv = parse_model_output(
        {
            "invoice_date": "15/09/2026",
            "due_date": "Oct 15, 2026",
            "currency": "₹",
            "grand_total": "₹ 1,23,456.78",
            "total_discount": "(100.00)",
            "notes": "  multi   space\n text ",
            "po_number": "N/A",
        }
    )
    assert inv.invoice_date == date(2026, 9, 15)
    assert inv.due_date == date(2026, 10, 15)
    assert inv.currency == "INR"
    assert inv.grand_total == Decimal("123456.78")
    assert inv.total_discount == Decimal("-100.00")
    assert inv.notes == "multi space text"
    assert inv.po_number is None


def test_confidence_is_validated():
    inv = parse_model_output(
        {
            "field_confidence": [
                {"field": "grand_total", "confidence": 0.9},
                {"field": "line_items[0].quantity", "confidence": 0.5},
                {"field": "made_up_field", "confidence": 0.9},
                {"field": "invoice_number", "confidence": 1.7},
                {"field": "currency", "confidence": True},
            ]
        }
    )
    assert inv.field_confidence == {"grand_total": 0.9, "line_items[0].quantity": 0.5}


def test_empty_line_items_dropped_and_huge_lists_truncated():
    inv = parse_model_output({"line_items": [{}, {"description": None}, {"description": "x"}]})
    assert len(inv.line_items) == 1
    inv = parse_model_output({"line_items": [{"description": "x"}] * 600})
    assert len(inv.line_items) == 500
    assert "truncated" in inv.parse_warnings[0]


def test_non_object_output_rejected():
    with pytest.raises(ValueError):
        parse_model_output(["not", "an", "object"])


def test_money_serializes_as_strings():
    dumped = CanonicalInvoice(grand_total=Decimal("10.50")).model_dump(mode="json")
    assert dumped["grand_total"] == "10.50"
