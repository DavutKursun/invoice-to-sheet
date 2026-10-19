from __future__ import annotations

import pytest

from invoice_to_sheet.schema import InvoiceExtraction, json_schema

ALL_FIELDS = [
    "document_type", "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date", "currency",
    "subtotal", "discount", "tax_amount", "total", "line_items", "payment_terms", "confidence", "low_confidence_fields", "notes",
]


def test_all_16_fields_are_required():
    assert list(InvoiceExtraction.model_fields) == ALL_FIELDS
    assert all(f.is_required() for f in InvoiceExtraction.model_fields.values())
    assert json_schema()["required"] == ALL_FIELDS
    with pytest.raises(Exception):
        InvoiceExtraction(document_type="invoice", vendor_name="x")  # nothing is filled in silently


def test_claude_wire_schema_lists_every_field_as_required():
    """The schema the Anthropic SDK sends for messages.parse(output_format=InvoiceExtraction). With Python
    defaults only two fields were required and the API answered 400 "Schema is too complex"."""
    anthropic = pytest.importorskip("anthropic")
    wire = anthropic.transform_schema(InvoiceExtraction)
    assert sorted(wire["required"]) == sorted(ALL_FIELDS)
    assert wire["additionalProperties"] is False
    assert sorted(wire["$defs"]["LineItem"]["required"]) == ["amount", "description", "quantity", "unit_price"]
