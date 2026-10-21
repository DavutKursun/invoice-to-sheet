"""Prepare → extract → validate → route → write. One function per document."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings, settings as default_settings
from .costs import BudgetGuard, format_cost, cost_usd
from .extract import Extraction, extract
from .prepare import PreparedDoc, prepare
from .providers import Provider
from .schema import InvoiceExtraction
from .sheets import Writer
from .validation import validate


@dataclass
class ProcessedDoc:
    file_name: str
    decision: str  # auto_approved | needs_review
    reasons: list[str]
    data: InvoiceExtraction
    model: str
    cost_usd: float
    cost_label: str
    seconds: float
    repaired: bool
    pages: int
    warnings: list[str] = field(default_factory=list)
    processed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    preview_jpeg: bytes | None = None  # first rendered page (JPEG bytes), only with process_document(..., keep_preview=True)

    def row(self) -> dict:
        d = self.data
        return {
            "processed_at": self.processed_at,
            "file_name": self.file_name,
            "decision": self.decision,
            "reasons": "; ".join(self.reasons),
            "document_type": d.document_type,
            "vendor_name": d.vendor_name,
            "vendor_tax_id": d.vendor_tax_id,
            "invoice_number": d.invoice_number,
            "invoice_date": d.invoice_date,
            "due_date": d.due_date,
            "currency": d.currency,
            "subtotal": d.subtotal,
            "discount": d.discount,
            "tax_amount": d.tax_amount,
            "total": d.total,
            "line_items": json.dumps([li.model_dump() for li in d.line_items], ensure_ascii=False),
            "payment_terms": d.payment_terms,
            "confidence": d.confidence,
            "low_confidence_fields": ", ".join(d.low_confidence_fields),
            "notes": d.notes,
            "model": self.model,
            "cost_usd": round(self.cost_usd, 6),
        }


def process_document(
    path: str | Path,
    provider: Provider,
    model: str,
    writer: Writer | None = None,
    cfg: Settings = default_settings,
    guard: BudgetGuard | None = None,
    keep_preview: bool = False,
) -> ProcessedDoc:
    t0 = time.perf_counter()
    doc: PreparedDoc = prepare(path, cfg.max_pages, cfg.render_dpi, cfg.max_image_side, cfg.max_file_mb)
    if guard is not None:
        guard.check(0.01)  # BudgetExceeded (a RuntimeError) with a message safe to show; nothing is called
    ex: Extraction = extract(doc, provider, model)
    usd = cost_usd(model, ex.result.input_tokens, ex.result.output_tokens)
    if guard is not None:
        guard.record(usd)
    existing = writer.existing_keys() if writer is not None else None
    reasons = validate(
        ex.data, tol_abs=cfg.amount_tolerance_abs, tol_rel=cfg.amount_tolerance_rel,
        min_confidence=cfg.min_confidence, existing_keys=existing,
    )
    if doc.truncated:
        reasons.append(f"only the first {cfg.max_pages} of {doc.page_count} pages were read")
    decision = "auto_approved" if not reasons else "needs_review"
    out = ProcessedDoc(
        doc.file_name, decision, reasons, ex.data, model, usd, format_cost(model, usd),
        time.perf_counter() - t0, ex.repaired, doc.page_count, list(doc.warnings),
        preview_jpeg=doc.images[0] if keep_preview and doc.images else None,
    )
    if writer is not None:
        writer.append(out.row(), review=(decision == "needs_review"))
    return out
