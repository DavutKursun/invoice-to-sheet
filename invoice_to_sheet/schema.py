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
    document_type: Literal["invoice", "receipt", "other"]
    vendor_name: str
    vendor_tax_id: str = ""
    invoice_number: str = ""
    invoice_date: str = ""  # ISO date YYYY-MM-DD or ""
    due_date: str = ""
    currency: str = ""  # ISO 4217, e.g. USD, EUR, TRY
    subtotal: float = 0.0
    discount: float = 0.0  # positive number; 0 if none
    tax_amount: float = 0.0
    total: float = 0.0
    line_items: list[LineItem] = Field(default_factory=list)
    payment_terms: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    low_confidence_fields: list[str] = Field(default_factory=list)
    notes: str = ""


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
