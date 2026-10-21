# invoice-to-sheet

[![Hugging Face Space](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-live%20demo-blue)](https://huggingface.co/spaces/DavutKursun/invoice-to-sheet)
[![ci](https://github.com/DavutKursun/invoice-to-sheet/actions/workflows/ci.yml/badge.svg)](https://github.com/DavutKursun/invoice-to-sheet/actions/workflows/ci.yml)

**Live demo:** https://huggingface.co/spaces/DavutKursun/invoice-to-sheet · **Design notes and evaluation:** [docs/DESIGN.md](docs/DESIGN.md)

![demo: phone photo → fields and decision → append to CSV → batch](docs/img/demo.gif)

Drop an invoice or receipt (PDF, JPG, PNG) and get the fields a bookkeeper needs as a spreadsheet row.
One Claude call with a strict schema extracts vendor, number, dates, currency, amounts and line items.
Plain-Python validation checks the arithmetic, the dates and the model's own confidence before anything is written.
Clean documents are appended automatically; doubtful ones go to a review queue with the reason in plain words.

## Results

Measured with `scripts/eval.py` on 80 synthetic documents with exact labels (60 dev / 20 test; the test split was run once, after all tuning). Model: Claude Haiku 5.5 (`claude-haiku-5-5`), cost at list price from the reported usage.

| Metric | Dev (60 docs) | Test (20 docs) |
|---|---|---|
| Every scored field correct (10 fields) | 100% | 100% |
| Document exact match (all fields and every line item) | 100% | 100% |
| Auto-approved | 59 (98%) | 20 (100%) |
| Errors among auto-approved documents | 0 | 0 |
| Injected instructions followed (3 adversarial docs, dev split only) | 0 of 3 | – |
| Cost per document | $0.0008 | $0.0009 |
| Latency p50 (one extraction call) | 4.7 s | 4.9 s |

Live Space (free CPU, Claude Haiku 5.5, measured 2026-10-21 through the Gradio API): a sample document comes back in 6–12 s end to end, of which 4.7–7.9 s is the extraction call; Append to CSV 1.5 s; a batch of two uploads 12.6 s; $0.0008–0.0010 per document.

## How routing works

Every document gets `auto_approved` or `needs_review`. The checks are pure Python, not another model call: line items add up to the subtotal, subtotal − discount + tax equals the total, quantity × unit price per line, ISO dates in order and not in the future, a known currency, model confidence ≥ 0.85 with no doubtful money field, no unreadable characters, no duplicate of a row already in the sheet. Each failed check becomes a reason the reviewer can read ("line 2: 1 × 710.58 ≠ 3552.90"). Nothing enters the sheet without passing.

The demo writes per-session CSVs (`invoices.csv`, `review.csv`, a `batch.csv` for the batch tab). Google Sheets mode (a service account plus `SHEET_ID`) is local, through `scripts/run_folder.py`.

## Limits, privacy, caveats

- Demo limits: 10 MB per file, 20 pages, 10 files per batch, 100 documents and $0.50 per UTC day for the whole Space.
- Documents are processed in memory and not stored: uploads and session CSVs live in a temp folder, are pruned after an hour and vanish on restart; no document text is logged.
- The numbers above are on synthetic data (six layouts, four currencies, discounts, phone-photo degradation, injected instructions; see [data/README.md](data/README.md)). A perfect score shows the pipeline works, not that real invoices will be perfect.
- Not handled: handwritten invoices, multi-invoice bundles, scripts other than Latin and Turkish, accounting-system integrations (QuickBooks, Xero).

Next steps: a Message Batches script for whole folders at half price, a Sonnet vs Haiku comparison, and a hand-labelled set of real invoices.

## Run it yourself

```bash
git clone https://github.com/DavutKursun/invoice-to-sheet && cd invoice-to-sheet
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env              # ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic, or a Groq key + a vision model id
python app.py                     # the web app on http://127.0.0.1:7860
python scripts/run_folder.py my_invoices/       # CLI → output/invoices.csv, output/review.csv (or Google Sheets)
python scripts/eval.py --split dev --delay 2    # accuracy report in eval/results/
```

Tests without a key or network: `pip install -r requirements-dev.txt && pytest`. MIT license.
