# Eval Results

**Pass rate:** 69/69 = 100%  (target ≥ 80%)

## Failure Breakdown

| Code | Count | Meaning |
|---|---|---|
| `wrong_section` | 0 | Returned chunk's section does not match expected |
| `wrong_page` | 0 | Page outside expected range |
| `hallucinated_register` | 0 | Register tool returned record with empty bit_fields |
| `missing_citation` | 0 | Returned chunk has no citation field |
| `false_refusal` | 0 | Tool returned refusal for a query that had sufficient matching content |

## Failed Questions

| Tool | Question | Reason |
|---|---|---|
