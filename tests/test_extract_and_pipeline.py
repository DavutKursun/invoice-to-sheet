from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from invoice_to_sheet.config import Settings
from invoice_to_sheet.costs import BudgetExceeded, BudgetGuard, cost_usd, format_cost
from invoice_to_sheet.extract import extract
from invoice_to_sheet.pipeline import process_document
from invoice_to_sheet.prepare import prepare
from invoice_to_sheet.providers import ExtractResult, ProviderError, parse_json_loose
from invoice_to_sheet.sheets import COLUMNS, CsvWriter


class FakeProvider:
    name = "fake"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def extract(self, doc, system, instruction, model, max_tokens=2000):
        self.calls.append(instruction)
        return ExtractResult(self.outputs.pop(0), 1500, 200, model, 0.1)


def good_json(**over):
    d = {"document_type": "invoice", "vendor_name": "Aria Air GmbH", "vendor_tax_id": "", "invoice_number": "INV-7",
         "invoice_date": "2026-03-02", "due_date": "2026-04-01", "currency": "EUR", "subtotal": 100.0, "discount": 0.0, "tax_amount": 19.0,
         "total": 119.0, "line_items": [{"description": "Filter", "quantity": 4, "unit_price": 25.0, "amount": 100.0}],
         "payment_terms": "Net 30", "confidence": 0.9, "low_confidence_fields": [], "notes": ""}
    d.update(over)
    return json.dumps(d)


def test_parse_json_loose_handles_fences_and_prose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Here you go: {"a": 1} thanks') == {"a": 1}
    with pytest.raises(json.JSONDecodeError):
        parse_json_loose("no json here")


def test_extract_validates_first_try(sample_pdf):
    p = FakeProvider([good_json()])
    ex = extract(prepare(sample_pdf), p, "m")
    assert ex.data.total == 119.0 and not ex.repaired and len(p.calls) == 1


def test_extract_repairs_once(sample_pdf):
    p = FakeProvider(['{"vendor_name": "x", "total": "abc"', good_json()])
    ex = extract(prepare(sample_pdf), p, "m")
    assert ex.repaired and len(p.calls) == 2 and "did not validate" in p.calls[1]
    assert ex.result.input_tokens == 3000


def test_extract_gives_up_after_repair(sample_pdf):
    p = FakeProvider(["nope", "still nope"])
    with pytest.raises(ValueError):
        extract(prepare(sample_pdf), p, "m")


def test_pipeline_routes_and_writes_csv(sample_pdf, tmp_path: Path):
    cfg = Settings(output_dir=tmp_path / "out", max_pages=4)
    writer = CsvWriter(cfg.output_dir)
    p = FakeProvider([good_json(), good_json(total=200.0), good_json()])
    out1 = process_document(sample_pdf, p, "claude-haiku-5-5", writer, cfg)
    assert out1.decision == "auto_approved" and out1.cost_label.startswith("$")
    out2 = process_document(sample_pdf, p, "claude-haiku-5-5", writer, cfg)
    assert out2.decision == "needs_review" and any("≠ total" in r for r in out2.reasons)
    out3 = process_document(sample_pdf, p, "claude-haiku-5-5", writer, cfg)  # same vendor + number again
    assert any("duplicate" in r for r in out3.reasons)
    assert (cfg.output_dir / "invoices.csv").exists() and (cfg.output_dir / "review.csv").exists()
    assert len((cfg.output_dir / "review.csv").read_text().splitlines()) == 3  # header + 2


def test_pipeline_truncation_reason(sample_pdf, tmp_path: Path):
    cfg = Settings(output_dir=tmp_path, max_pages=1)
    out = process_document(sample_pdf, FakeProvider([good_json()]), "m", None, cfg)
    assert out.decision == "needs_review" and any("first 1 of 3 pages" in r for r in out.reasons)


def test_budget_guard_blocks_with_a_friendly_message(sample_pdf, tmp_path: Path):
    cfg = Settings(output_dir=tmp_path)
    guard = BudgetGuard(tmp_path / "b.json", max_daily_usd=0.01)
    guard.record(0.009)
    provider = FakeProvider([good_json()])
    with pytest.raises(BudgetExceeded) as info:  # a RuntimeError subclass, so callers that catch RuntimeError keep working
        process_document(sample_pdf, provider, "m", None, cfg, guard)
    assert str(info.value) == "Daily demo budget reached ($0.01 of $0.01 spent today); resets at 00:00 UTC."
    assert isinstance(info.value, RuntimeError) and provider.calls == [] and guard.read().calls == 1  # nothing called, nothing recorded


def test_document_cap_stops_the_pipeline(sample_pdf, tmp_path: Path):
    cfg = Settings(output_dir=tmp_path)
    guard = BudgetGuard(tmp_path / "b.json", max_daily_usd=5.0, max_daily_docs=2)
    provider = FakeProvider([good_json(), good_json(), good_json()])
    for _ in range(2):
        process_document(sample_pdf, provider, "m", None, cfg, guard)
    with pytest.raises(BudgetExceeded, match=r"^Daily demo budget reached \(2 of 2 documents today\); resets at 00:00 UTC\.$"):
        process_document(sample_pdf, provider, "m", None, cfg, guard)
    assert len(provider.calls) == 2 and guard.remaining_docs() == 0


def test_pipeline_keeps_the_first_page_preview_only_when_asked(sample_pdf, tmp_path: Path):
    from PIL import Image

    cfg = Settings(output_dir=tmp_path, max_pages=4)
    plain = process_document(sample_pdf, FakeProvider([good_json()]), "m", None, cfg)
    assert plain.preview_jpeg is None
    out = process_document(sample_pdf, FakeProvider([good_json()]), "m", None, cfg, keep_preview=True)
    assert out.preview_jpeg is not None and out.preview_jpeg[:2] == b"\xff\xd8"  # JPEG magic
    with Image.open(io.BytesIO(out.preview_jpeg)) as img:
        assert img.format == "JPEG" and max(img.size) <= cfg.max_image_side and img.size[1] > img.size[0]  # A4 portrait
    assert out.preview_jpeg == prepare(sample_pdf, cfg.max_pages, cfg.render_dpi, cfg.max_image_side).images[0]  # the first page
    assert list(out.row()) == COLUMNS and "preview_jpeg" not in out.row()  # 22 columns; the preview never goes into the sheet


def test_costs():
    assert cost_usd("claude-sonnet-5-5", 1000, 100) == pytest.approx((1000 * 2 + 100 * 10) / 1e6)
    assert cost_usd("meta-llama/llama-4-scout", 1000, 100) == 0.0
    assert format_cost("meta-llama/llama-4-scout", 0.0) == "free tier"
    assert format_cost("claude-haiku-5-5", 0.00012) == "$0.0001"


def test_provider_error_message():
    assert "ProviderError" in repr(ProviderError("x")) or str(ProviderError("x")) == "x"
