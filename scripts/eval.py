#!/usr/bin/env python
"""Evaluate extraction against data/synthetic/labels.jsonl (CLAUDE.md §8).

    python scripts/eval.py --split dev --limit 10
    python scripts/eval.py --split test            # touch once, at the end

Metrics: per-field accuracy, "all money fields correct" rate, line-item precision /
recall / F1, routing quality (errors among auto_approved; precision of needs_review),
adversarial handling, cost and latency. Writes eval/results/<split>-<model>.{jsonl,json,md}.
The metric functions are pure and tested; --dry-run uses the labels as a fake model.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from invoice_to_sheet.config import settings  # noqa: E402
from invoice_to_sheet.providers import ProviderError  # noqa: E402
from invoice_to_sheet.schema import InvoiceExtraction  # noqa: E402
from invoice_to_sheet.validation import validate  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIELDS_EXACT = ("vendor_name", "invoice_number", "invoice_date", "due_date", "currency", "document_type")
FIELDS_MONEY = ("subtotal", "discount", "tax_amount", "total")


def norm(s: str) -> str:
    return " ".join(str(s).lower().replace(".", "").replace(",", "").split())


def field_correct(field: str, pred, gold) -> bool:
    if field in FIELDS_MONEY:
        return abs(float(pred) - float(gold)) <= 0.011
    if field == "vendor_name":
        a, b = norm(pred), norm(gold)
        return a == b or (len(b) > 4 and (b in a or a in b))
    return norm(pred) == norm(gold)


def _num_close(a, b) -> bool:
    return abs(float(a) - float(b)) <= 0.011


def line_item_prf(pred: list[dict], gold: list[dict]) -> tuple[float, float, float]:
    """Match by amount, quantity and unit price (±0.01 each) and a loose description overlap; one-to-one.

    Quantity and unit price are part of the match: a line with the right amount but a misread
    quantity is a real error for a bookkeeper and must count as one."""
    used = set()
    tp = 0
    for p in pred:
        for j, g in enumerate(gold):
            if j in used:
                continue
            if (_num_close(p["amount"], g["amount"]) and _num_close(p["quantity"], g["quantity"])
                    and _num_close(p["unit_price"], g["unit_price"]) and _desc_match(p["description"], g["description"])):
                used.add(j)
                tp += 1
                break
    precision = tp / len(pred) if pred else (1.0 if not gold else 0.0)
    recall = tp / len(gold) if gold else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _desc_match(a: str, b: str) -> bool:
    wa, wb = set(norm(a).split()), set(norm(b).split())
    return len(wa & wb) >= max(1, min(len(wa), len(wb)) // 2)


def score_doc(pred: InvoiceExtraction, gold: dict, reasons: list[str]) -> dict:
    fields = {f: field_correct(f, getattr(pred, f), gold[f]) for f in FIELDS_EXACT + FIELDS_MONEY}
    p, r, f1 = line_item_prf([li.model_dump() for li in pred.line_items], gold["line_items"])
    money_ok = all(fields[f] for f in FIELDS_MONEY)
    any_error = not all(fields.values()) or f1 < 0.999
    return {"fields": fields, "money_ok": money_ok, "li_precision": p, "li_recall": r, "li_f1": f1,
            "any_error": any_error, "decision": "auto_approved" if not reasons else "needs_review", "reasons": reasons}


def error_row(gold: dict, error: str, seconds: float) -> dict:
    """A document that produced no extraction: wrong everywhere, decision "error" (never auto-approved)."""
    return {"id": gold["id"], "kind": gold["kind"], "file": gold["file"],
            "fields": {f: False for f in FIELDS_EXACT + FIELDS_MONEY}, "money_ok": False,
            "li_precision": 0.0, "li_recall": 0.0, "li_f1": 0.0, "any_error": True, "decision": "error", "reasons": [error],
            "cost_usd": 0.0, "seconds": seconds, "repaired": False, "pred": None}


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    out: dict = {"n": n}
    for f in FIELDS_EXACT + FIELDS_MONEY:
        out[f"acc_{f}"] = sum(r["fields"][f] for r in rows) / n
    out["money_all_correct"] = sum(r["money_ok"] for r in rows) / n
    out["exact_match"] = sum(not r["any_error"] for r in rows) / n  # every scored field and every line item right
    out["n_errors"] = sum(r["decision"] == "error" for r in rows)
    out["li_f1_mean"] = statistics.mean(r["li_f1"] for r in rows)
    approved = [r for r in rows if r["decision"] == "auto_approved"]
    review = [r for r in rows if r["decision"] == "needs_review"]
    out["approved_share"] = len(approved) / n
    out["errors_among_approved"] = (sum(r["any_error"] for r in approved) / len(approved)) if approved else 0.0
    out["review_precision"] = (sum(r["any_error"] for r in review) / len(review)) if review else 0.0
    for kind in ("clean", "photo", "adversarial"):
        sub = [r for r in rows if r.get("kind") == kind]
        if sub:
            out[f"money_all_correct_{kind}"] = sum(r["money_ok"] for r in sub) / len(sub)
            out[f"n_{kind}"] = len(sub)
    adv = [r for r in rows if r.get("kind") == "adversarial"]
    out["adversarial_handled"] = (sum((not r["any_error"]) or r["decision"] == "needs_review" for r in adv) / len(adv)) if adv else None
    costs = [r["cost_usd"] for r in rows if "cost_usd" in r]
    secs = [r["seconds"] for r in rows if "seconds" in r]
    if costs:
        out["cost_mean"], out["cost_p50"] = statistics.mean(costs), statistics.median(costs)
    if secs:
        out["latency_p50"] = statistics.median(secs)
        out["latency_p95"] = sorted(secs)[max(0, int(round(0.95 * (len(secs) - 1))))]
    out["repaired_share"] = sum(r.get("repaired", False) for r in rows) / n
    return out


def markdown(summary: dict, label: str) -> str:
    def pct(x):
        return "—" if x is None else f"{100*x:.0f}%"
    lines = [f"# Evaluation — {label}", "", f"Documents: {summary['n']}", "", "| Metric | Value |", "|---|---|"]
    for f in FIELDS_EXACT + FIELDS_MONEY:
        lines.append(f"| {f} accuracy | {pct(summary[f'acc_{f}'])} |")
    lines += [
        f"| All money fields correct | {pct(summary['money_all_correct'])} |",
        f"| Document exact match (all fields and line items) | {pct(summary['exact_match'])} |",
        f"| Documents that failed (no extraction) | {summary['n_errors']} |",
        f"| Line-item F1 (mean) | {summary['li_f1_mean']:.2f} |",
        f"| Auto-approved share | {pct(summary['approved_share'])} |",
        f"| Errors among auto-approved | {pct(summary['errors_among_approved'])} |",
        f"| Review-queue precision | {pct(summary['review_precision'])} |",
    ]
    for kind in ("clean", "photo", "adversarial"):
        if f"money_all_correct_{kind}" in summary:
            lines.append(f"| Money fields correct — {kind} (n={summary[f'n_{kind}']}) | {pct(summary[f'money_all_correct_{kind}'])} |")
    if summary.get("adversarial_handled") is not None:
        lines.append(f"| Adversarial documents handled | {pct(summary['adversarial_handled'])} |")
    if "cost_mean" in summary:
        lines.append(f"| Cost per document (mean / p50) | ${summary['cost_mean']:.4f} / ${summary['cost_p50']:.4f} |")
    if "latency_p50" in summary:
        lines.append(f"| Latency p50 / p95 | {summary['latency_p50']:.1f} s / {summary['latency_p95']:.1f} s |")
    lines.append(f"| Repair retries | {pct(summary['repaired_share'])} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "synthetic")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between API calls (free-tier limits)")
    ap.add_argument("--dry-run", action="store_true", help="use the labels as a perfect fake model (no API)")
    ap.add_argument("--out", type=Path, default=ROOT / "eval" / "results")
    a = ap.parse_args(argv)

    labels = [json.loads(l) for l in (a.data / "labels.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.split != "all":
        labels = [l for l in labels if l["split"] == a.split]
    if a.limit:
        labels = labels[: a.limit]

    if a.dry_run:
        model = "dry-run"
        provider = None
    else:
        from invoice_to_sheet.providers import get_provider

        provider, model = get_provider(settings)
        from invoice_to_sheet.pipeline import process_document  # noqa: F401

    rows = []
    label_name = f"{a.split}-{model.replace('/', '_')}"
    a.out.mkdir(parents=True, exist_ok=True)
    jsonl = a.out / f"{label_name}.jsonl"
    jsonl.write_text("", encoding="utf-8")
    for i, gold in enumerate(labels, 1):
        path = a.data / gold["file"]
        t0 = time.perf_counter()
        try:
            if a.dry_run:
                pred = InvoiceExtraction(**{k: gold[k] for k in InvoiceExtraction.model_fields if k in gold},
                                         confidence=0.95, low_confidence_fields=[], notes="")
                cost, repaired = 0.0, False
            else:
                from invoice_to_sheet.extract import extract
                from invoice_to_sheet.prepare import prepare
                from invoice_to_sheet.costs import cost_usd

                doc = prepare(path, settings.max_pages, settings.render_dpi, settings.max_image_side, settings.max_file_mb)
                ex = extract(doc, provider, model)
                pred, repaired = ex.data, ex.repaired
                cost = cost_usd(model, ex.result.input_tokens, ex.result.output_tokens)
        except (ProviderError, ValueError, RuntimeError) as e:
            # one failing document must not abort the run: record it as a failed row and carry on
            row = error_row(gold, f"{type(e).__name__}: {e}", time.perf_counter() - t0)
            rows.append(row)
            with jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"[{i}/{len(labels)}] {gold['id']} {gold['kind']:11s} ERROR {row['reasons'][0][:120]}", flush=True)
            if a.delay and not a.dry_run:
                time.sleep(a.delay)
            continue
        seconds = time.perf_counter() - t0
        reasons = validate(pred, tol_abs=settings.amount_tolerance_abs, tol_rel=settings.amount_tolerance_rel, min_confidence=settings.min_confidence)
        row = {"id": gold["id"], "kind": gold["kind"], "file": gold["file"], **score_doc(pred, gold, reasons),
               "cost_usd": cost, "seconds": seconds, "repaired": repaired, "pred": pred.model_dump()}
        rows.append(row)
        with jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[{i}/{len(labels)}] {gold['id']} {gold['kind']:11s} money_ok={row['money_ok']} li_f1={row['li_f1']:.2f} {row['decision']}", flush=True)
        if a.delay and not a.dry_run:
            time.sleep(a.delay)

    summary = summarize(rows)
    (a.out / f"{label_name}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    md = markdown(summary, f"{a.split} split, model {model}")
    (a.out / f"{label_name}.md").write_text(md, encoding="utf-8")
    print("\n" + md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
