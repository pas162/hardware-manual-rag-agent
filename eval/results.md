# Eval Results

**Pass rate:** 65/69 = 94%  (target ≥ 80%)

## Failure Breakdown

| Code | Count | Meaning |
|---|---|---|
| `wrong_section` | 1 | Returned chunk's section does not match expected |
| `wrong_page` | 3 | Page outside expected range |
| `hallucinated_register` | 0 | Register tool returned record with empty bit_fields |
| `missing_citation` | 0 | Returned chunk has no citation field |
| `false_refusal` | 0 | Tool returned refusal for a query that had sufficient matching content |

## Failed Questions

| Tool | Question | Reason |
|---|---|---|
| `search_um` | When the GTSECR.SPCE or GTSECR.SPCD bit is set to 1, what happens to the PCEN bit in the channels selected by the GTSECSR register? | `wrong_page` |
| `search_um` | Under what condition can bits in the pipe control registers be changed? | `wrong_section` |
| `search_um` | What is the function of the CTSUTSOD bit field in the CTSUERRS register, and what are its two possible states? | `wrong_page` |
| `search_um` | What is the base address of SSIE0 for the SSISCR Status Control Register? | `wrong_page` |
