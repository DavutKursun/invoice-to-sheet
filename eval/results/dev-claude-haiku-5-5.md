# Evaluation — dev split, model claude-haiku-5-5

Documents: 60

| Metric | Value |
|---|---|
| vendor_name accuracy | 100% |
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
| Document exact match (all fields and line items) | 100% |
| Documents that failed (no extraction) | 0 |
| Line-item F1 (mean) | 1.00 |
| Auto-approved share | 98% |
| Errors among auto-approved | 0% |
| Review-queue precision | 0% |
| Money fields correct — clean (n=37) | 100% |
| Money fields correct — photo (n=20) | 100% |
| Money fields correct — adversarial (n=3) | 100% |
| Adversarial documents handled | 100% |
| Cost per document (mean / p50) | $0.0008 / $0.0008 |
| Latency p50 / p95 | 4.7 s / 5.8 s |
| Repair retries | 0% |
