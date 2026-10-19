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

## Evaluation (Milestone 4, 2026-10-19)

`scripts/eval.py` scores every document against `data/synthetic/labels.jsonl`: exact match for text
fields (vendor name normalised), ±0.01 for amounts, line items matched one-to-one on amount, quantity,
unit price and a fuzzy description. A document that fails to extract is recorded as a row that is wrong
everywhere. Full tables: `eval/results/dev-claude-haiku-5-5.md` and `eval/results/test-claude-haiku-5-5.md`.
The test split was run once, after all tuning.

| Metric (Claude Haiku 5.5, `claude-haiku-5-5`) | Dev (60) | Test (20) |
|---|---|---|
| Accuracy on each of the 10 scored fields | 100% | 100% |
| Document exact match | 100% | 100% |
| Line-item F1 | 1.00 | 1.00 |
| Auto-approved / errors among them | 59 / 0 | 20 / 0 |
| Needs review | 1 | 0 |
| Adversarial documents (3, dev only): injection followed | 0 | – |
| Cost per document / total (list price, reported usage) | $0.0008 / $0.049 | $0.0009 / $0.018 |
| Latency p50 / p95 | 4.7 s / 5.8 s | 4.9 s / 5.5 s |
| Repair retries | 0 | 0 |

Groq free tier (`qwen/qwen3.8-27b`, first 20 dev documents, 2026-10-11, json_schema mode, re-scored with
the current scoring): money fields 100%, line-item F1 0.91, vendor names 65% because of the
control-character problem below, about 20 s per document. Not re-run after the switch to JSON mode.

## Findings (Milestone 4)

- **Claude structured outputs reject optional-heavy schemas.** `messages.parse(output_format=InvoiceExtraction)`
  returned 400 "Schema is too complex." The SDK strips the `ge`/`le` constraints fine; the cause was the
  Python defaults on the model, which left only 2 of 16 properties in `required`. With every field
  required (what the spec asks for) the schema compiles. `tests/test_schema.py` asserts this on
  `anthropic.transform_schema(InvoiceExtraction)` without network.
- **Prompt rules can contradict each other, and models differ in how literally they follow them.**
  Rule 2 ("absent value → 0 and list the field in low_confidence_fields") and rule 4 ("discount 0 if none")
  led Claude Haiku to flag `discount` on 37 of 60 dev documents, and validation treated every doubtful
  money field as a reason: 38 reviews, 0 real errors. The Groq model never did this. Fixed on both sides,
  the prompt now says an absent discount is normal, and validation ignores a doubtful `discount` when it is
  0. Reviews on the same split went from 38 to 1.
- **Groq's strict json_schema decoder mangles accented characters.** Ü, ı and Ş came back as control
  characters in 7 of 20 vendor names (`O\u0000\u0000` for "OÜ"); plain JSON mode kept them. JSON mode +
  Pydantic is now the default for OpenAI-compatible providers, and text fields with control characters go
  to review.
- **The synthetic receipt layout had defects of its own.** Long descriptions overprinted the quantity digit,
  and EUR/TRY unit prices touched it ("2" + "153,71 €" was read as quantity 2153.71). Both were dataset
  bugs, fixed in the generator. The arithmetic checks had caught the misreads anyway, and a new rule sends
  priced lines with a zero quantity or unit price to review.
- **Injected instructions were not followed.** On the three adversarial dev documents neither model renamed
  the vendor, zeroed the total or raised its confidence. Claude named the injection in `notes` on two of
  them and sent one to review at confidence 0.70. The test split contains no adversarial documents.
- **Latency and cost.** Claude Haiku 5.5: p50 under 5 s, p95 under 6 s, $0.0008–0.0009 per document, so the
  whole dev split costs five cents. The Groq free tier is free but queued at about 20 s per call on the day
  it was measured, which matters for the "fields in under 20 s on a phone" demo criterion.
- **One failing document must not abort an evaluation.** The eval loop had no per-document error handling;
  with the schema bug it would have died on document one. Failures are now rows with decision `error`.

## Known limitations

- No OCR fallback for handwriting; multi-invoice PDFs are read as one document.
- The duplicate check only sees what is already in the CSV/sheet.
- Free-tier providers have rate and daily limits; `scripts/eval.py --delay` paces calls.
