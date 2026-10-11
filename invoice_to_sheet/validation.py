"""Pure-Python checks on an extraction. Each failed check is a human-readable reason."""

from __future__ import annotations

from datetime import date, datetime

from .schema import MONEY_FIELDS, InvoiceExtraction

ISO_CURRENCIES = {
    "USD", "EUR", "GBP", "TRY", "CHF", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF",
    "RON", "BGN", "AED", "SAR", "INR", "CNY", "BRL", "MXN", "ZAR", "NZD", "SGD", "HKD", "KRW", "ILS",
}


def _close(a: float, b: float, tol_abs: float, tol_rel: float) -> bool:
    return abs(a - b) <= max(tol_abs, tol_rel * max(abs(a), abs(b)))


def _has_control_chars(s: str) -> bool:
    """True when a text field carries ASCII control characters (< 0x20 or DEL). Seen when a
    schema-constrained decoder mangles accented letters such as Ü into \\u0000 bytes."""
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in s)


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def validate(
    x: InvoiceExtraction,
    *,
    tol_abs: float = 0.02,
    tol_rel: float = 0.01,
    min_confidence: float = 0.85,
    today: date | None = None,
    existing_keys: set[tuple[str, str]] | None = None,
) -> list[str]:
    """Return the list of reasons to send the document to review (empty = all good)."""
    today = today or date.today()
    reasons: list[str] = []

    if x.document_type == "other":
        reasons.append("document does not look like an invoice or receipt")
    if not x.vendor_name.strip():
        reasons.append("vendor name is missing")
    text_fields = [("vendor name", x.vendor_name), ("invoice number", x.invoice_number)]
    text_fields += [(f"line {i} description", li.description) for i, li in enumerate(x.line_items, 1)]
    for label, value in text_fields:
        if _has_control_chars(value):
            reasons.append(f"{label} contains unreadable characters")
    if x.total <= 0:
        reasons.append(f"total is {x.total:.2f}; expected a positive amount")

    if x.line_items:
        items_sum = sum(li.amount for li in x.line_items)
        if x.subtotal and not _close(items_sum, x.subtotal, tol_abs, tol_rel):
            reasons.append(f"line items sum to {items_sum:.2f} but subtotal says {x.subtotal:.2f}")
        for i, li in enumerate(x.line_items, 1):
            if li.quantity and li.unit_price and not _close(li.quantity * li.unit_price, li.amount, tol_abs, tol_rel):
                reasons.append(f"line {i}: {li.quantity:g} × {li.unit_price:.2f} ≠ {li.amount:.2f}")
    if x.discount < 0:
        reasons.append(f"discount {x.discount:.2f} is negative")
    if x.subtotal and not _close(x.subtotal - x.discount + x.tax_amount, x.total, tol_abs, tol_rel):
        disc = f" − discount {x.discount:.2f}" if x.discount else ""
        reasons.append(f"subtotal {x.subtotal:.2f}{disc} + tax {x.tax_amount:.2f} ≠ total {x.total:.2f}")

    inv, due = _parse_date(x.invoice_date), _parse_date(x.due_date)
    if x.invoice_date and inv is None:
        reasons.append(f"invoice date {x.invoice_date!r} is not a valid ISO date")
    if x.due_date and due is None:
        reasons.append(f"due date {x.due_date!r} is not a valid ISO date")
    if inv and inv > today:
        reasons.append(f"invoice date {inv.isoformat()} is in the future")
    if inv and due and due < inv:
        reasons.append(f"due date {due.isoformat()} is before the invoice date {inv.isoformat()}")
    if not inv:
        reasons.append("invoice date is missing")

    if x.currency.upper() not in ISO_CURRENCIES:
        reasons.append(f"currency {x.currency!r} is not a known ISO code")

    if x.confidence < min_confidence:
        reasons.append(f"model confidence {x.confidence:.2f} is below {min_confidence:.2f}")
    doubtful_money = [f for f in x.low_confidence_fields if f in MONEY_FIELDS]
    if doubtful_money:
        reasons.append("model is unsure about: " + ", ".join(doubtful_money))

    if existing_keys is not None and x.invoice_number:
        key = (x.vendor_name.strip().lower(), x.invoice_number.strip().lower())
        if key in existing_keys:
            reasons.append(f"duplicate: {x.vendor_name} invoice {x.invoice_number} was already recorded")

    return reasons
