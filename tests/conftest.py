from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invoice_to_sheet.schema import InvoiceExtraction, LineItem  # noqa: E402


@pytest.fixture
def good_extraction() -> InvoiceExtraction:
    return InvoiceExtraction(
        document_type="invoice", vendor_name="Aria Air GmbH", vendor_tax_id="DE812345678", invoice_number="INV-1001",
        invoice_date="2026-03-02", due_date="2026-04-01", currency="EUR", subtotal=100.0, discount=0.0, tax_amount=19.0, total=119.0,
        line_items=[LineItem(description="Filter", quantity=2, unit_price=25.0, amount=50.0),
                    LineItem(description="Dock", quantity=1, unit_price=50.0, amount=50.0)],
        payment_terms="Net 30", confidence=0.95, low_confidence_fields=[], notes="",
    )


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    p = tmp_path / "inv.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for i in range(3):
        c.drawString(72, 750, f"INVOICE page {i + 1}")
        c.showPage()
    c.save()
    return p


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    from PIL import Image

    p = tmp_path / "receipt.png"
    Image.new("RGB", (3000, 2000), "white").save(p)
    return p
