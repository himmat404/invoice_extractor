import copy
from datetime import date

from app.services.ai.providers import FAKE_INVOICE
from app.services.ai.schema import parse_model_output
from app.services.validation import ValidationConfig, check_gstin, validate

CFG = ValidationConfig(today=date(2026, 10, 2))


def run(**changes):
    data = copy.deepcopy(FAKE_INVOICE)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return validate(parse_model_output(data), CFG)


def codes(report):
    return [i.code for i in report.issues]


def test_clean_invoice_passes():
    report = run()
    assert report.status == "passed", report.issues
    assert report.computed["expected_grand_total"] == "2950.00"
    assert report.computed["lines_total"] == "2500.00"


def test_missing_required_fields_need_review():
    report = run(invoice_number=None, supplier={"name": None})
    assert report.status == "needs_review"
    fields = {i.field for i in report.issues if i.code == "missing_required_field"}
    assert fields == {"invoice_number", "supplier.name"}


def test_nothing_extracted_fails():
    report = validate(parse_model_output({}), CFG)
    assert report.status == "failed"
    assert codes(report) == ["no_invoice_data"]


def test_grand_total_mismatch_is_an_error():
    report = run(grand_total="3100.00")
    assert report.status == "needs_review"
    issue = next(i for i in report.issues if i.code == "grand_total_mismatch")
    assert issue.details == {"expected": "2950.00", "found": "3100.00"}


def test_rounding_adjustment_is_info_only():
    report = run(grand_total="2950.40", subtotal="2500.40", line_items=None)
    assert report.status == "passed"
    report = run(grand_total="2951.00")
    assert report.status == "passed"
    assert codes(report) == ["rounded_total"]


def test_tolerates_small_rounding_differences():
    assert run(grand_total="2950.03").status == "passed"


def test_line_level_checks():
    lines = [
        {
            "description": "x",
            "quantity": "2",
            "unit_price": "100",
            "line_total": "260",
            "tax_rate": "18",
            "tax_amount": "50",
        },
        {"description": "y", "quantity": "0", "unit_price": "5", "tax_rate": "180"},
    ]
    report = run(line_items=lines, subtotal=None, taxes=None, cgst=None, sgst=None, total_tax=None)
    found = codes(report)
    assert "line_total_mismatch" in found
    assert "line_tax_mismatch" in found
    assert "non_positive_quantity" in found
    assert "invalid_tax_rate" in found
    assert report.status == "needs_review"


def test_tax_inclusive_line_totals_without_subtotal():
    lines = [
        {
            "description": "x",
            "quantity": "10",
            "unit_price": "250",
            "tax_amount": "450",
            "line_total": "2950",
        }
    ]
    report = run(line_items=lines, subtotal=None)
    assert "grand_total_mismatch" not in codes(report), report.issues


def test_subtotal_and_tax_total_mismatch():
    report = run(subtotal="2400.00", total_tax="500.00")
    assert {"subtotal_mismatch", "tax_total_mismatch"} <= set(codes(report))


def test_discount_is_accounted_for():
    report = run(total_discount="100.00", grand_total="2850.00")
    assert "grand_total_mismatch" not in codes(report)


def test_gstin_checks():
    assert check_gstin("27AAPFU0939F1ZV") is None
    assert check_gstin("27AAPFU0939F1ZA") == "gstin_checksum"
    assert check_gstin("00AAPFU0939F1ZV") == "gstin_state_code"
    assert check_gstin("GB123456789") == "gstin_format"
    report = run(supplier={"name": "Acme", "tax_id": "27AAPFU0939F1ZA"})
    assert "gstin_checksum" in codes(report)
    assert report.status == "warning"


def test_gst_type_consistency():
    # same state (27/27) but IGST charged
    report = run(
        taxes=[{"type": "IGST", "rate": "18", "amount": "450"}], cgst=None, sgst=None, igst="450.00"
    )
    assert "gst_type_mismatch" in codes(report)
    report = run(cgst="225.00", sgst="200.00")
    assert "cgst_sgst_unequal" in codes(report)


def test_dates_and_currency():
    report = run(invoice_date="2027-01-01", due_date="2026-12-01")
    assert {"future_invoice_date", "due_before_invoice_date"} <= set(codes(report))
    assert "unknown_currency" in codes(run(currency="XYZ"))
    assert "missing_currency" in codes(run(currency=None))


def test_balance_due():
    assert "balance_mismatch" in codes(run(amount_paid="1000", balance_due="100"))
    assert "balance_mismatch" not in codes(run(amount_paid="1000", balance_due="1950"))


def test_parse_warnings_surface():
    report = run(due_date="whenever")
    assert "unreadable_value" in codes(report)
    assert report.status == "warning"


def test_negative_total_warns():
    report = run(
        grand_total="-2950.00",
        subtotal="-2500.00",
        total_tax="-450.00",
        taxes=None,
        cgst=None,
        sgst=None,
        line_items=None,
    )
    assert "negative_total" in codes(report)
