# ClaimFast development error analysis

> **Development-only.** This analysis uses the verified
> `dev-f9639d694d88-e7b8df00` bundle and the synthetic development split only.
> It is not a held-out test analysis. The 60-record held-out evaluation with
> three runs has not been completed.

## Scope and denominator

The evaluation covers 20 development records for each of five prompts, one
run each: 100 expected observations total. All 100 keys are present; there
are no missing keys. Ninety-nine stored results are valid and one is invalid.
The evaluator retains invalid results in the expected denominator and maps
them to an invalid-prediction value rather than silently dropping them.

The `dev_eval` view contains 12 records after excluding the union of the
few-shot examples. It includes DEV-13, DEV-12, and DEV-20.

## Observed discrepancies

| Prompt(s) | Observation | Result |
|---|---|---|
| A, B-k3, B-k5, B-k8, C | DEV-13, `location` | Strict exact match fails; the lenient location rule accepts it. |
| B-k8 | DEV-12, `injury_reported` | One field-level exact mismatch. Claim type and urgency remain correct. |
| C | DEV-20, whole observation | Invalid response; classification and all seven extraction fields are scored as invalid. |

No claim-type or urgency mismatches were identified for A, B-k3, B-k5, or
B-k8. For C, DEV-20 is the only claim-type and urgency mismatch; it is the
invalid response. No valid C observation had a classification mismatch.

### Invalid Prompt C response

The stored key is `dev|DEV-20|C|1`. Its error is:

> Prompt C response must contain exactly one `<final_json>` block.

The record has `parse_ok = false` and `schema_ok = false`. It is present in
the results store, so the evaluator reports zero missing predictions. It
counts as wrong for classification and all extracted fields. It is not
interpreted as a null-filled valid JSON object. The error analysis does not
reproduce the raw response body.

This one failure reduces Prompt C parse/schema validity to 19/20 (0.950) in
the all view and 11/12 (0.917) in `dev_eval`. Its location score also includes
the DEV-13 strict mismatch; DEV-20 remains a miss under both strict and
lenient scoring because it is invalid.

### Other field discrepancies

- **DEV-13 location:** The same strict mismatch occurs for all five prompts.
  Lenient matching accepts it, so it is a normalization/strictness
  discrepancy in this metric, not a lenient-location failure. The reported
  metrics do not justify a broader claim about location extraction.
- **DEV-12 injury under B-k8:** One exact field mismatch is present. The
  evaluation reports zero false negatives for explicitly true injury labels
  and 1.000 injury-true recall; do not infer a safety-critical miss from this
  mismatch.
- **Other valid outputs:** No additional per-record mismatches were observed
  in the development store comparison.

The evaluation reports zero hallucinations for all prompts in both views.
That measurement is limited to this small synthetic sample and is not a
production guarantee.

## Failure-count requirement and interpretation

The observation-level failure count includes each observation with at least
one recorded mismatch or an invalid response. A repeated DEV-13 mismatch is a
separate observation for each prompt; the workbook lists each actual
prompt/record pair.

| Prompt | Failing observations (of 20) | Invalid stored results | Shortfall to 15 |
|---|---:|---:|---:|
| A | 1 | 0 | 14 |
| B-k3 | 1 | 0 | 14 |
| B-k5 | 1 | 0 | 14 |
| B-k8 | 2 | 0 | 13 |
| C | 2 | 1 | 13 |

Observed invalid stored-result counts alone are A: 0, B-k3: 0, B-k5: 0,
B-k8: 0, and C: 1. The artifacts provide no evidence of 15 failures per
prompt, and this result set is not a failure-injection study. Accordingly,
the 15-failures-per-prompt requirement is **not met**. The
[failure-analysis workbook](development_error_analysis.xlsx) contains these
actual cases and the per-prompt shortfall; it does not add simulated cases.

All findings above are development-only. They must not be presented as
held-out test performance. Human review of the synthetic first-pass labels
and the documented ambiguity policy remains outstanding.

## Sources

- [Evaluation results table](../outputs/evaluations/dev-f9639d694d88-e7b8df00/results.csv)
- [Detailed evaluation metrics](../outputs/evaluations/dev-f9639d694d88-e7b8df00/metrics.json)
- [Evaluation manifest](../outputs/evaluations/dev-f9639d694d88-e7b8df00/manifest.json)
- [Project labeling guide](../data/labeling_guide.md)
