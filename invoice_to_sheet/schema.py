"""The extraction schema. All fields required; "" / 0 when absent (CLAUDE.md §4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    amount: float


class InvoiceExtraction(BaseModel):
    """All 16 fields are required ("" / 0 / [] when absent). No Python defaults: a default makes the field
    optional in the JSON schema, and 14 optional properties made the Claude structured-outputs compiler
    reject the schema with "Schema is too complex" (2026-10-19)."""

    document_type: Literal["invoice", "receipt", "other"]
    vendor_name: str
    vendor_tax_id: str  # "" if absent
    invoice_number: str  # "" if absent
    invoice_date: str  # ISO date YYYY-MM-DD or ""
    due_date: str  # ISO date or ""
    currency: str  # ISO 4217, e.g. USD, EUR, TRY
    subtotal: float
    discount: float  # positive number; 0 if none
    tax_amount: float
    total: float
    line_items: list[LineItem]
    payment_terms: str  # "" if absent
    confidence: float = Field(ge=0.0, le=1.0)
    low_confidence_fields: list[str]
    notes: str


MONEY_FIELDS = ("subtotal", "discount", "tax_amount", "total")
KEY_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "currency", *MONEY_FIELDS)


def json_schema() -> dict:
    """Strict JSON schema for providers that support response_format=json_schema."""
    schema = InvoiceExtraction.model_json_schema()
    _strict(schema)
    for d in schema.get("$defs", {}).values():
        _strict(d)
    return schema


def _strict(obj: dict) -> None:
    if obj.get("type") == "object":
        obj["additionalProperties"] = False
        obj["required"] = list(obj.get("properties", {}).keys())
        for p in obj["properties"].values():
            p.pop("default", None)
