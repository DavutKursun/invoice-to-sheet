"""FakeExtractor: the dataset labels for known sample files, a generic record otherwise; no SDK, no network."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from invoice_to_sheet.config import Settings
from invoice_to_sheet.costs import BudgetGuard
from invoice_to_sheet.extract import extract
from invoice_to_sheet.fake import GENERIC_RECORD, LABELS_PATH, FakeExtractor
from invoice_to_sheet.pipeline import process_document
from invoice_to_sheet.prepare import PreparedDoc, prepare
from invoice_to_sheet.schema import InvoiceExtraction
from invoice_to_sheet.validation import validate

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data" / "synthetic" / "pdf" / "inv010.pdf"  # dev split: Kestrel Software OÜ, EUR, total 2028.40
PHOTO = ROOT / "data" / "synthetic" / "photo" / "inv002.jpg"  # dev split: Kestrel Software OÜ, EUR, total 1233.99


def test_dataset_file_is_looked_up_by_basename():
    fake = FakeExtractor()
    assert fake.name == "fake" and LABELS_PATH.exists()
    doc = PreparedDoc("/somewhere/else/inv010.pdf", "pdf", [b""], 1, False)  # only the basename matters
    res = fake.extract(doc, "sys", "Extract.", "claude-haiku-5-5")
    assert (res.input_tokens, res.output_tokens, res.model, res.elapsed_seconds) == (1500, 200, "claude-haiku-5-5", 0.05)
    data = InvoiceExtraction.model_validate_json(res.raw_json)
    assert data.vendor_name == "Kestrel Software OÜ" and data.invoice_number == "F-34175" and data.currency == "EUR"
    assert (data.subtotal, data.discount, data.tax_amount, data.total) == (1662.62, 0.0, 365.78, 2028.4) and len(data.line_items) == 6
    assert data.confidence == 0.95 and data.low_confidence_fields == []
    assert data.notes == "fake extractor: fields copied from the dataset labels"
    assert validate(data) == []


def test_pipeline_with_the_fake_on_the_sample_documents(tmp_path: Path):
    cfg = Settings(output_dir=tmp_path, max_pages=4)
    guard = BudgetGuard(tmp_path / "b.json", max_daily_usd=0.5, max_daily_docs=100)
    fake = FakeExtractor()
    clean = process_document(CLEAN, fake, "claude-haiku-5-5", None, cfg, guard, keep_preview=True)
    assert clean.decision == "auto_approved" and clean.reasons == [] and clean.data.total == 2028.4 and clean.pages == 1
    assert clean.cost_label == "$0.0003" and clean.cost_usd == pytest.approx(0.00025)  # real-looking dollars for a priced model id
    assert clean.preview_jpeg is not None and clean.preview_jpeg[:2] == b"\xff\xd8"
    photo = process_document(PHOTO, fake, "claude-haiku-5-5", None, cfg, guard)
    assert photo.decision == "auto_approved" and photo.data.vendor_name == "Kestrel Software OÜ" and photo.data.total == 1233.99
    assert guard.read().calls == 2 and guard.remaining_docs() == 98 and guard.read().usd == pytest.approx(0.0005)


def test_unknown_file_gets_a_generic_record_that_validates(sample_png):
    fake = FakeExtractor()
    res = fake.extract(prepare(sample_png), "sys", "Extract.", "m")
    data = InvoiceExtraction.model_validate_json(res.raw_json)
    assert data.vendor_name == "Example Vendor GmbH" and data.invoice_number == "FAKE-1" and data.currency == "EUR"
    assert (data.document_type, data.invoice_date, data.due_date) == ("invoice", "2026-03-02", "2026-04-01")
    assert len(data.line_items) == 2 and sum(li.amount for li in data.line_items) == pytest.approx(data.subtotal)
    assert data.discount == 0 and data.tax_amount == pytest.approx(0.19 * data.subtotal)
    assert data.total == pytest.approx(data.subtotal + data.tax_amount)
    assert data.confidence == 0.9 and data.notes == "fake extractor: file not in the sample dataset"
    assert validate(data) == []
    assert data == InvoiceExtraction(**GENERIC_RECORD)
    assert fake.extraction("x.pdf") is not fake.extraction("x.pdf")  # a fresh copy each time, never the shared record


def test_missing_labels_file_means_only_the_generic_record(tmp_path: Path):
    fake = FakeExtractor(tmp_path / "nope.jsonl")
    data = fake.extraction("inv010.pdf")
    assert data.invoice_number == "FAKE-1" and "not in the sample dataset" in data.notes


def test_extract_accepts_the_fake_as_a_provider(sample_pdf):
    ex = extract(prepare(sample_pdf), FakeExtractor(), "m")
    assert not ex.repaired and ex.data.invoice_number == "FAKE-1" and ex.result.model == "m" and ex.result.schema_enforced is False


def test_fake_imports_without_any_sdk_or_gradio():
    code = ("import sys; import invoice_to_sheet.fake as f; "
            "loaded = [m for m in ('anthropic', 'openai', 'gradio', 'gspread') if m in sys.modules]; "
            "assert not loaded, loaded; print(f.FakeExtractor.name)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "fake"
