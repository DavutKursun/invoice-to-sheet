from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evalmod", ROOT / "scripts" / "eval.py")
evalmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evalmod)


def test_field_correct_rules():
    assert evalmod.field_correct("total", 119.004, 119.0)
    assert not evalmod.field_correct("total", 119.02, 119.0)
    assert evalmod.field_correct("vendor_name", "ARIA AIR GMBH", "Aria Air GmbH")
    assert evalmod.field_correct("vendor_name", "Aria Air", "Aria Air GmbH")
    assert not evalmod.field_correct("invoice_number", "INV-1", "INV-2")


def test_line_item_prf():
    gold = [{"description": "HEPA filter AF-200", "quantity": 2, "unit_price": 25.0, "amount": 50.0},
            {"description": "USB-C dock", "quantity": 1, "unit_price": 70.0, "amount": 70.0}]
    pred = [{"description": "HEPA filter", "quantity": 2, "unit_price": 25.0, "amount": 50.0},
            {"description": "Dock USB-C", "quantity": 1, "unit_price": 70.0, "amount": 70.0},
            {"description": "extra", "quantity": 1, "unit_price": 1.0, "amount": 1.0}]
    p, r, f1 = evalmod.line_item_prf(pred, gold)
    assert r == 1.0 and p == 2 / 3 and 0.79 < f1 < 0.81
    assert evalmod.line_item_prf([], []) == (1.0, 1.0, 1.0)


def test_line_item_prf_requires_quantity_and_unit_price():
    gold = [{"description": "Training workshop, half day", "quantity": 5, "unit_price": 710.58, "amount": 3552.9}]
    pred = [{"description": "Training workshop, half day", "quantity": 1, "unit_price": 710.58, "amount": 3552.9}]
    assert evalmod.line_item_prf(pred, gold) == (0.0, 0.0, 0.0)  # right amount, misread quantity: an error
    pred[0]["quantity"] = 5.004
    assert evalmod.line_item_prf(pred, gold) == (1.0, 1.0, 1.0)
    pred[0]["unit_price"] = 710.0
    assert evalmod.line_item_prf(pred, gold) == (0.0, 0.0, 0.0)


def test_summarize_and_markdown():
    rows = [
        {"kind": "clean", "fields": {f: True for f in evalmod.FIELDS_EXACT + evalmod.FIELDS_MONEY}, "money_ok": True, "li_f1": 1.0,
         "any_error": False, "decision": "auto_approved", "cost_usd": 0.0, "seconds": 1.0, "repaired": False},
        {"kind": "photo", "fields": {**{f: True for f in evalmod.FIELDS_EXACT + evalmod.FIELDS_MONEY}, "total": False}, "money_ok": False,
         "li_f1": 0.5, "any_error": True, "decision": "needs_review", "cost_usd": 0.0, "seconds": 3.0, "repaired": True},
        {"kind": "adversarial", "fields": {f: True for f in evalmod.FIELDS_EXACT + evalmod.FIELDS_MONEY}, "money_ok": True, "li_f1": 1.0,
         "any_error": False, "decision": "auto_approved", "cost_usd": 0.0, "seconds": 2.0, "repaired": False},
    ]
    s = evalmod.summarize(rows)
    assert s["n"] == 3 and s["money_all_correct"] == 2 / 3 and s["errors_among_approved"] == 0.0
    assert s["review_precision"] == 1.0 and s["adversarial_handled"] == 1.0 and s["latency_p50"] == 2.0
    md = evalmod.markdown(s, "x")
    assert "Review-queue precision | 100%" in md and "photo (n=1)" in md


def test_dry_run_end_to_end(tmp_path: Path):
    data = ROOT / "data" / "synthetic"
    if not (data / "labels.jsonl").exists():
        import pytest

        pytest.skip("synthetic data not generated")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "eval.py"), "--dry-run", "--split", "test", "--out", str(tmp_path)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    summary = json.loads((tmp_path / "test-dry-run.json").read_text())
    assert summary["n"] == 20 and summary["money_all_correct"] == 1.0


def test_error_row_and_summary_with_a_failed_document():
    err = evalmod.error_row({"id": "inv999", "kind": "clean", "file": "pdf/inv999.pdf"}, "ProviderError: HTTP 500", 1.5)
    assert err["decision"] == "error" and err["any_error"] and not err["money_ok"] and err["li_f1"] == 0.0 and err["pred"] is None
    good = {"kind": "clean", "fields": {f: True for f in evalmod.FIELDS_EXACT + evalmod.FIELDS_MONEY}, "money_ok": True, "li_f1": 1.0,
            "any_error": False, "decision": "auto_approved", "cost_usd": 0.001, "seconds": 1.0, "repaired": False}
    s = evalmod.summarize([good, err])
    assert s["n"] == 2 and s["n_errors"] == 1 and s["exact_match"] == 0.5 and s["money_all_correct"] == 0.5
    assert s["approved_share"] == 0.5 and s["errors_among_approved"] == 0.0  # a failed document is never auto-approved
    md = evalmod.markdown(s, "x")
    assert "Document exact match (all fields and line items) | 50%" in md and "Documents that failed (no extraction) | 1" in md


def test_main_continues_after_a_failing_document(tmp_path: Path, monkeypatch):
    import pytest
    from types import SimpleNamespace
    import invoice_to_sheet.extract as extract_mod
    import invoice_to_sheet.providers as providers
    from invoice_to_sheet.schema import InvoiceExtraction

    data = ROOT / "data" / "synthetic"
    if not (data / "labels.jsonl").exists():
        pytest.skip("synthetic data not generated")
    labels = {json.loads(l)["id"]: json.loads(l) for l in (data / "labels.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}

    def fake_extract(doc, provider, model, max_tokens=2000):
        gold = labels[Path(doc.file_name).stem]
        if gold["id"] == "inv003":
            raise ValueError("Extraction did not validate after one repair attempt")
        pred = InvoiceExtraction(**{k: gold[k] for k in InvoiceExtraction.model_fields if k in gold}, confidence=0.95, low_confidence_fields=[], notes="")
        return SimpleNamespace(data=pred, repaired=False, result=SimpleNamespace(input_tokens=10, output_tokens=5))

    monkeypatch.setattr(providers, "get_provider", lambda cfg: (object(), "fake-model"))
    monkeypatch.setattr(extract_mod, "extract", fake_extract)
    assert evalmod.main(["--split", "dev", "--limit", "3", "--out", str(tmp_path)]) == 0
    rows = [json.loads(l) for l in (tmp_path / "dev-fake-model.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [r["decision"] for r in rows] == ["auto_approved", "error", "auto_approved"]
    assert rows[1]["reasons"] == ["ValueError: Extraction did not validate after one repair attempt"]
    summary = json.loads((tmp_path / "dev-fake-model.json").read_text(encoding="utf-8"))
    assert summary["n"] == 3 and summary["n_errors"] == 1 and summary["exact_match"] == pytest.approx(2 / 3)
