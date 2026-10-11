# Evaluation — dev split (first 20), model qwen/qwen3.8-27b — run 2026-10-11 with prompt 2026-10-11.2 and json_schema; re-scored from the saved predictions after the line-item scoring (quantity + unit price) and control-character check changes

Documents: 20

| Metric | Value |
|---|---|
| vendor_name accuracy | 65% |
| invoice_number accuracy | 100% |
| invoice_date accuracy | 100% |
| due_date accuracy | 100% |
| currency accuracy | 100% |
| document_type accuracy | 100% |
| subtotal accuracy | 100% |
| discount accuracy | 100% |
| tax_amount accuracy | 100% |
| total accuracy | 100% |
| All money fields correct | 100% |
| Line-item F1 (mean) | 0.91 |
| Auto-approved share | 55% |
| Errors among auto-approved | 18% |
| Review-queue precision | 100% |
| Money fields correct — clean (n=12) | 100% |
| Money fields correct — photo (n=6) | 100% |
| Money fields correct — adversarial (n=2) | 100% |
| Adversarial documents handled | 100% |
| Cost per document (mean / p50) | $0.0000 / $0.0000 |
| Latency p50 / p95 | 20.5 s / 21.2 s |
| Repair retries | 5% |
