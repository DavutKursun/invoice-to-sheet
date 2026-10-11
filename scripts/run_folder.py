#!/usr/bin/env python
"""Process every invoice in a folder and append rows to the CSV (or Google Sheet).

    python scripts/run_folder.py data/synthetic/pdf --limit 3
    python scripts/run_folder.py my_invoices/ --json-out results.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from invoice_to_sheet.config import settings  # noqa: E402
from invoice_to_sheet.costs import BudgetGuard  # noqa: E402
from invoice_to_sheet.pipeline import process_document  # noqa: E402
from invoice_to_sheet.prepare import SUPPORTED  # noqa: E402
from invoice_to_sheet.providers import ProviderError, get_provider  # noqa: E402
from invoice_to_sheet.sheets import get_writer  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json-out", type=Path, default=None, help="append one JSON line per document")
    ap.add_argument("--no-write", action="store_true", help="do not append to the sheet/CSV")
    a = ap.parse_args(argv)

    files = sorted(p for p in a.folder.iterdir() if p.suffix.lower() in SUPPORTED)
    if a.limit:
        files = files[: a.limit]
    if not files:
        print(f"no supported files in {a.folder}", file=sys.stderr)
        return 1
    try:
        provider, model = get_provider(settings)
    except ProviderError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    writer = None if a.no_write else get_writer(settings)
    guard = BudgetGuard(settings.budget_file, settings.max_daily_usd)

    approved = review = 0
    for f in files:
        try:
            out = process_document(f, provider, model, writer, settings, guard)
        except ProviderError as e:
            print(f"{f.name}: API error: {e}", file=sys.stderr)
            return 4
        except (ValueError, RuntimeError) as e:
            print(f"{f.name}: {e}", file=sys.stderr)
            continue
        approved += out.decision == "auto_approved"
        review += out.decision == "needs_review"
        d = out.data
        print(f"{f.name}: {out.decision:14s} {d.vendor_name[:28]:28s} {d.invoice_number:12s} {d.total:>10.2f} {d.currency}  "
              f"{out.cost_label} · {out.seconds:.1f}s{'  (repaired)' if out.repaired else ''}")
        for r in out.reasons:
            print(f"    - {r}")
        if a.json_out:
            with a.json_out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"file": f.name, **out.row(), "data": d.model_dump()}, ensure_ascii=False) + "\n")
    print(f"\n{approved} auto-approved, {review} to review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
