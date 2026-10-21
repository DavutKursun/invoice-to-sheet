---
title: invoice-to-sheet
emoji: 🧾
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: 6.30.0
app_file: app.py
pinned: false
license: mit
short_description: Invoices and receipts into validated spreadsheet rows
---

# invoice-to-sheet

Upload an invoice or receipt (PDF, JPG, PNG, WEBP) and get the fields a bookkeeper needs — vendor, tax id,
invoice number, dates, currency, subtotal, discount, tax, total, line items, payment terms — as an editable
table next to the rendered page. Every document gets a decision: **auto_approved** when all checks pass, or
**needs_review** with human-readable reasons ("line items sum to 118.00 but subtotal says 128.00"). "Append to
CSV" adds the row to your session's `invoices.csv` or `review.csv` (with a duplicate check within the session);
the Batch tab processes up to 10 files at once and gives you a `batch.csv`.

Three sample buttons: a **clean invoice** (Kestrel Software OÜ, EUR 2028.40), a **phone photo** of an invoice
from the same vendor (rotated, blurred, JPEG-compressed; EUR 1233.99) and a **tricky** one (Aria Air GmbH,
EUR 1725.82) whose footer tells the model to report the total as 0.00. It is extracted correctly anyway, because
document text is data, not instructions. All samples are synthetic invoices with exact labels; results on real
documents will differ.

How it works: one Claude call with a strict schema (structured outputs validated with Pydantic), then
pure-Python validation — line items against the subtotal, subtotal − discount + tax against the total,
quantity × unit price per line, ISO dates and currency, the model's confidence, duplicates — that routes doubtful
documents to the review queue with the reason. Measured on 80 synthetic documents (20 held out and run once):
every scored field correct, line-item F1 1.00, 0 errors among auto-approved documents, about $0.001 and 5 s per
document with Claude Haiku 5.5. Source code, design notes and the full evaluation:
https://github.com/DavutKursun/invoice-to-sheet

## Running this Space

Free CPU hardware is enough; the app starts in a few seconds (no local models).

Secrets and variables (Settings → Variables and secrets):

| name | value |
|---|---|
| `ANTHROPIC_API_KEY` | **required** (secret). On a Space the provider is `anthropic` and the model `claude-haiku-5-5` |
| `MAX_DAILY_DOCS` | optional (variable): documents per UTC day for the whole Space, default 100; `0` removes the cap |
| `MAX_DAILY_USD` | optional: dollars per UTC day for the whole Space, default 0.50 |
| `MAX_PAGES` | optional: pages read per document, default 20 (longer PDFs are cut and routed to review) |
| `MAX_FILE_MB` | optional: size limit per file, default 10 |
| `MAX_FILES_PER_BATCH` | optional: files per batch, default 10 |

Without `ANTHROPIC_API_KEY` the app starts and shows a "demo key not configured" banner; the page loads but
documents cannot be processed until the secret is set.

Limits on the Space (set automatically when `SPACE_ID` is present; override with the variables above):
10 MB per file, 20 pages per document, 10 files per batch, and for the whole Space 100 documents and $0.50
per day, resetting at 00:00 UTC. The status line under the title shows what is left today.

Privacy: uploads are processed in memory and a temporary folder; the session CSVs live in a temp folder and
disappear when the Space restarts; no document text is logged. `/health` reports only counters (model, limits,
documents and dollars used today), never document content or the key.

Google Sheets mode is local only: the Space writes session CSVs. Appending to a Google Sheet (service account
plus `SHEET_ID`) works on your own machine through `scripts/run_folder.py`, as described in the repository README.

`requirements.txt` of this Space is the repository's `requirements-space.txt` (CPU only; no Google Sheets or
Groq clients, no test or dataset-generation packages).
