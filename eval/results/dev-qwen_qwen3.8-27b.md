# Evaluation — dev split, model qwen/qwen3.8-27b

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
| Line-item F1 (mean) | 1.00 |
| Auto-approved share | 90% |
| Errors among auto-approved | 39% |
| Review-queue precision | 0% |
| Money fields correct — clean (n=12) | 100% |
| Money fields correct — photo (n=6) | 100% |
| Money fields correct — adversarial (n=2) | 100% |
| Adversarial documents handled | 50% |
| Cost per document (mean / p50) | $0.0000 / $0.0000 |
| Latency p50 / p95 | 20.5 s / 21.2 s |
| Repair retries | 5% |
