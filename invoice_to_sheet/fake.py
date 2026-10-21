"""A stand-in for the LLM (``DOCS_FAKE_EXTRACTOR=1``): fields copied from the dataset labels, no network.

Used by the headless smoke test of the Space and by UI tests. Importable without any SDK.
"""

from __future__ import annotations

import json
from pathlib import Path

from .prepare import PreparedDoc
from .providers import ExtractResult
from .schema import InvoiceExtraction

ROOT = Path(__file__).resolve().parents[1]
LABELS_PATH = ROOT / "data" / "synthetic" / "labels.jsonl"

# for files that are not in the sample dataset: arithmetic consistent, so it validates
GENERIC_RECORD: dict = {
    "document_type": "invoice", "vendor_name": "Example Vendor GmbH", "vendor_tax_id": "DE123456789", "invoice_number": "FAKE-1",
    "invoice_date": "2026-03-02", "due_date": "2026-04-01", "currency": "EUR", "subtotal": 100.0, "discount": 0.0, "tax_amount": 19.0,
    "total": 119.0,
    "line_items": [{"description": "HEPA filter AF-200", "quantity": 2.0, "unit_price": 25.0, "amount": 50.0},
                   {"description": "USB-C dock, 8-port", "quantity": 1.0, "unit_price": 50.0, "amount": 50.0}],
    "payment_terms": "Net 30", "confidence": 0.9, "low_confidence_fields": [], "notes": "fake extractor: file not in the sample dataset",
}


class FakeExtractor:
    """Satisfies ``providers.Provider``: looks the file up by basename in ``labels.jsonl``, generic record otherwise."""

    name = "fake"

    def __init__(self, labels_path: str | Path = LABELS_PATH) -> None:
        self.labels_path = Path(labels_path)
        self._records: dict[str, dict] | None = None  # basename → label record, loaded on first use

    def _labels(self) -> dict[str, dict]:
        if self._records is None:
            records: dict[str, dict] = {}
            try:
                lines = self.labels_path.read_text(encoding="utf-8").splitlines()
            except OSError:  # missing file: only the generic record
                lines = []
            for line in lines:
                if line.strip():
                    rec = json.loads(line)
                    if rec.get("file"):
                        records[Path(rec["file"]).name] = rec
            self._records = records
        return self._records

    def extraction(self, file_name: str) -> InvoiceExtraction:
        rec = self._labels().get(Path(file_name).name)
        if rec is None:
            return InvoiceExtraction(**GENERIC_RECORD)
        fields = {k: rec[k] for k in InvoiceExtraction.model_fields if k in rec}
        fields.update(confidence=0.95, low_confidence_fields=[], notes="fake extractor: fields copied from the dataset labels")
        return InvoiceExtraction(**fields)

    def extract(self, doc: PreparedDoc, system: str, instruction: str, model: str, max_tokens: int = 2000) -> ExtractResult:
        # a realistic usage, so a priced model id shows real-looking dollars and the budget counters move
        return ExtractResult(self.extraction(doc.file_name).model_dump_json(), 1500, 200, model, 0.05)
