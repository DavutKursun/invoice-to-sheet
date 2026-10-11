"""Writers: CSV (default, always available) and Google Sheets (when configured).

Both keep two tables: "Invoices" (approved) and "Review" (needs a human), with the
same columns, and expose ``existing_keys()`` for the duplicate check.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Protocol

COLUMNS = [
    "processed_at", "file_name", "decision", "reasons", "document_type", "vendor_name", "vendor_tax_id",
    "invoice_number", "invoice_date", "due_date", "currency", "subtotal", "discount", "tax_amount", "total",
    "line_items", "payment_terms", "confidence", "low_confidence_fields", "notes", "model", "cost_usd",
]


class Writer(Protocol):
    def append(self, row: dict, review: bool) -> None: ...

    def existing_keys(self) -> set[tuple[str, str]]: ...


class CsvWriter:
    def __init__(self, output_dir: str | Path) -> None:
        self.dir = Path(output_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.invoices = self.dir / "invoices.csv"
        self.review = self.dir / "review.csv"

    def _append(self, path: Path, row: dict) -> None:
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)

    def append(self, row: dict, review: bool) -> None:
        self._append(self.review if review else self.invoices, row)

    def existing_keys(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for path in (self.invoices, self.review):
            if not path.exists():
                continue
            with path.open(newline="", encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("invoice_number"):
                        keys.add((r["vendor_name"].strip().lower(), r["invoice_number"].strip().lower()))
        return keys


class GoogleSheetsWriter:
    """Appends rows to worksheets "Invoices" and "Review" of a Google Sheet.

    Needs a service-account JSON (base64 in GOOGLE_SERVICE_ACCOUNT_JSON) and SHEET_ID;
    the sheet must be shared with the service-account e-mail. Tested manually (M3).
    """

    def __init__(self, sheet_id: str, service_account_json_b64: str) -> None:
        import base64
        import json

        import gspread
        from google.oauth2.service_account import Credentials

        info = json.loads(base64.b64decode(service_account_json_b64))
        creds = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self._sheet = gspread.authorize(creds).open_by_key(sheet_id)
        self._ws = {name: self._worksheet(name) for name in ("Invoices", "Review")}

    def _worksheet(self, name: str):
        try:
            ws = self._sheet.worksheet(name)
        except Exception:
            ws = self._sheet.add_worksheet(title=name, rows=1000, cols=len(COLUMNS))
        if not ws.row_values(1):
            ws.append_row(COLUMNS)
        return ws

    def append(self, row: dict, review: bool) -> None:
        self._ws["Review" if review else "Invoices"].append_row([str(row.get(c, "")) for c in COLUMNS], value_input_option="USER_ENTERED")

    def existing_keys(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for ws in self._ws.values():
            for r in ws.get_all_records():
                if r.get("invoice_number"):
                    keys.add((str(r["vendor_name"]).strip().lower(), str(r["invoice_number"]).strip().lower()))
        return keys


def get_writer(cfg) -> Writer:
    if cfg.sheet_id and cfg.google_service_account_json_b64:
        return GoogleSheetsWriter(cfg.sheet_id, cfg.google_service_account_json_b64)
    return CsvWriter(cfg.output_dir)
