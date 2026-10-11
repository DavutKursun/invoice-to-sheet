# invoice-to-sheet — design notes

## Pipeline

```
file ─► prepare (PDF → page images via pypdfium2; images resized ≤ 1568 px, JPEG)
     ─► extract (vision model, JSON schema / JSON mode, Pydantic validation, one repair retry)
     ─► validate (pure Python: sums, discount, tax, dates, currency, confidence, duplicates)
     ─► route (auto_approved | needs_review + reasons)
     ─► write (CSV: output/invoices.csv + output/review.csv, or Google Sheets)
```

## Decisions

- **Provider-agnostic.** Default is a free OpenAI-compatible vision model (Groq). Groq does
  not accept PDFs, so pages are rendered to images (first `MAX_PAGES`, default 4).
  Claude is an optional provider with native PDF input and `messages.parse` structured
  outputs; the README will compare both when a key is available.
- **JSON mode by default, server-side schema optional.** The OpenAI-compatible provider asks
  for `response_format=json_object`, spells the JSON schema out in the prompt, validates with
  Pydantic, makes one repair call with the validation errors, then gives up loudly. The eval
  reports the repair rate. `USE_JSON_SCHEMA=1` tries `response_format=json_schema` (strict)
  first; that was the default until the first dev run on Groq (2026-10-11), where the strict
  decoder turned Ü, ı and Ş into control characters in 7 of 20 vendor names (`O\u0000\u0000`
  for "OÜ") while plain JSON mode copied them correctly. Groq's `json_validate_failed` is a
  bad generation, not a missing feature: the call is redone in JSON mode and the schema path
  stays on for the next document; only a rejected request format switches it off.
- **All schema fields required** ("" / 0 when absent): simpler grammars, fewer surprises.
  `discount` was added after the dry-run showed that 30% of perfect extractions would be
  sent to review by the `subtotal + tax = total` check on discounted invoices.
- **Validation is the product.** The model may be wrong; the arithmetic checks catch most
  money errors, and the review queue turns the rest into a 10-second human task instead of
  a silent wrong row. Text fields with control characters (what a mangled Ü looks like) are
  sent to review as "contains unreadable characters".
- **Labels for free.** The synthetic generator writes the exact ground truth, so field
  accuracy is measured, not guessed. Degraded "phone photo" copies and injected
  instructions test robustness.

## Evaluation

_Milestone 4: dev-split tuning (prompt, tolerances, confidence threshold), one test-split
run, Groq vs Claude if available, batch mode cost. Tables go here and in the README._

## Known limitations

- No OCR fallback for handwriting; multi-invoice PDFs are read as one document.
- The duplicate check only sees what is already in the CSV/sheet.
- Free-tier providers have rate and daily limits; `scripts/eval.py --delay` paces calls.
