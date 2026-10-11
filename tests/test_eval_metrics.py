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
    gold = [{"description": "HEPA filter AF-200", "amount": 50.0}, {"description": "USB-C dock", "amount": 70.0}]
    pred = [{"description": "HEPA filter", "amount": 50.0}, {"description": "Dock USB-C", "amount": 70.0}, {"description": "extra", "amount": 1.0}]
    p, r, f1 = evalmod.line_item_prf(pred, gold)
    assert r == 1.0 and p == 2 / 3 and 0.79 < f1 < 0.81
    assert evalmod.line_item_prf([], []) == (1.0, 1.0, 1.0)


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
