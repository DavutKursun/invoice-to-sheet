from __future__ import annotations

from datetime import date

from invoice_to_sheet.schema import LineItem
from invoice_to_sheet.validation import validate

TODAY = date(2026, 10, 11)


def test_good_document_has_no_reasons(good_extraction):
    assert validate(good_extraction, today=TODAY) == []


def test_sum_mismatch(good_extraction):
    good_extraction.line_items[0].amount = 60.0
    reasons = validate(good_extraction, today=TODAY)
    assert any("line items sum" in r for r in reasons)
    assert any("line 1" in r for r in reasons)


def test_total_mismatch_and_tolerance(good_extraction):
    good_extraction.total = 119.01
    assert validate(good_extraction, today=TODAY) == []  # within 0.02
    good_extraction.total = 125.0
    assert any("≠ total" in r for r in validate(good_extraction, today=TODAY))


def test_dates(good_extraction):
    good_extraction.due_date = "2026-02-01"
    assert any("before the invoice date" in r for r in validate(good_extraction, today=TODAY))
    good_extraction.due_date = "01/02/2026"
    assert any("not a valid ISO date" in r for r in validate(good_extraction, today=TODAY))
    good_extraction.invoice_date = "2027-01-01"
    good_extraction.due_date = ""
    assert any("in the future" in r for r in validate(good_extraction, today=TODAY))


def test_currency_confidence_and_doubtful_money(good_extraction):
    good_extraction.currency = "EURO"
    good_extraction.confidence = 0.5
    good_extraction.low_confidence_fields = ["total", "vendor_name"]
    reasons = validate(good_extraction, today=TODAY)
    assert any("ISO code" in r for r in reasons)
    assert any("confidence 0.50" in r for r in reasons)
    assert any("unsure about: total" in r for r in reasons)


def test_duplicate(good_extraction):
    keys = {("aria air gmbh", "inv-1001")}
    assert any("duplicate" in r for r in validate(good_extraction, today=TODAY, existing_keys=keys))
    assert validate(good_extraction, today=TODAY, existing_keys=set()) == []


def test_other_document_type_and_zero_total(good_extraction):
    good_extraction.document_type = "other"
    good_extraction.total = 0
    good_extraction.subtotal = 0
    reasons = validate(good_extraction, today=TODAY)
    assert any("does not look like" in r for r in reasons) and any("positive amount" in r for r in reasons)


def test_priced_line_needs_quantity_and_unit_price(good_extraction):
    good_extraction.line_items[0].quantity = 0.0  # amount 50 stays, so the sums still add up
    assert validate(good_extraction, today=TODAY) == ["line 1: quantity or unit price missing"]
    good_extraction.line_items[0].quantity = 2153.71  # inv007: quantity and unit price fused
    good_extraction.line_items[0].unit_price = 0.0
    assert validate(good_extraction, today=TODAY) == ["line 1: quantity or unit price missing"]
    good_extraction.line_items[0].quantity, good_extraction.line_items[0].unit_price = 2.0, 25.0
    good_extraction.line_items.append(LineItem(description="Free sample", quantity=0, unit_price=0, amount=0))
    assert validate(good_extraction, today=TODAY) == []  # a zero-amount line may have no factors


def test_control_characters_send_to_review(good_extraction):
    good_extraction.vendor_name = "Kestrel Software O\x00\x00"  # what a constrained decoder made of "OÜ"
    assert validate(good_extraction, today=TODAY) == ["vendor name contains unreadable characters"]
    good_extraction.vendor_name = "Kestrel Software OÜ"
    good_extraction.invoice_number = "F-1\x7f"
    good_extraction.line_items[1].description = "Delta Yap\x10"
    reasons = validate(good_extraction, today=TODAY)
    assert reasons == ["invoice number contains unreadable characters", "line 2 description contains unreadable characters"]
    good_extraction.invoice_number = "F-1"
    good_extraction.line_items[1].description = "Delta Yapı"
    assert validate(good_extraction, today=TODAY) == []


def test_discount_is_respected(good_extraction):
    good_extraction.discount = 10.0
    good_extraction.total = 109.0
    assert validate(good_extraction, today=TODAY) == []
    good_extraction.discount = -1.0
    assert any("negative" in r for r in validate(good_extraction, today=TODAY))
