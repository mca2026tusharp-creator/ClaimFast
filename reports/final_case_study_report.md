# ClaimFast — Case Study 1

**Submission report — DEVELOPMENT-ONLY results**  
Evaluation bundle: `dev-f9639d694d88-e7b8df00`  
Evaluation date: 2026-11-15

> **DEVELOPMENT-ONLY — NOT HELD-OUT TEST PERFORMANCE.** All model metrics in
> this report come from 20 synthetic development records and one run per
> prompt. The required 60-record held-out evaluation with three runs has
> **not** been completed.

## Executive summary

ClaimFast turns free-form motor-insurance claim messages into a fixed
nine-field JSON record for downstream review. The verified development bundle
contains 100 planned observations (five prompts × 20 records × one run): 99
valid results and one invalid Prompt C result. No planned observations are
missing. On the common 12-record non-example development view, A and all
three B variants tie at 1.000 claim-type and urgency accuracy; C is 0.917 on
both. Few-shot gains over A and a benefit from reasoning are **not
demonstrated** by these results.

This is a development debugging result, not a held-out evaluation, safety
certification, or release decision. A is a reasonable control for a future
test because its development scores tie the smaller few-shot variants while
using fewer mean input tokens; no strategy is approved for production.

## 1. Problem and business context

Claim intake messages may combine incident details, administrative questions,
and missing or conflicting information. The prototype standardizes first-pass
claim classification and extraction for a human-reviewed workflow. It predicts
claim type and urgency and extracts policy number, incident date, location,
vehicle registration, estimated amount, injury status, and police-report
status.

The experiment measures structured prediction on synthetic messages. It does
not measure customer outcomes, processing-time reduction, cost savings, or a
live workflow.

## 2. Dataset and labeling approach

The project has 20 development records and 60 held-out records (80 total).
An offline structural validation found all 80 JSONL records valid, with the
same nine label fields in each. Dataset tag metadata records the required
variety, including typos, missing fields, Hinglish, multi-incident messages,
forwarded chains, ambiguous theft/glass cases, and short/long messages. The
held-out file was not used for prompt tuning; no held-out model metrics are
reported here.

Development label counts are own damage 5, third-party liability 4, theft 3,
total loss 2, glass only 2, and not a claim 4; urgency counts are high 6,
medium 10, and low 4. Labels are first-pass synthetic labels under the
project's operational [labeling guide](../data/labeling_guide.md). Its
interpretations are provisional. No independent second-review evidence for
at least 20 labels was found. The concise
[one-page reviewer guide](one_page_labeling_guide.md) is prepared, and a
[blank 20-record worksheet](development_label_review_worksheet.xlsx) is
available; neither constitutes a completed human review. Conflicts still
need a signed-off adjudication policy.

The common `dev_eval` view removes the union of the development examples used
in the B prompts and contains 12 records. The `all` view contains all 20 and
therefore includes prompt examples. Both views are development-only.

## 3. Prompt strategies and experiment configuration

| Prompt | Strategy |
|---|---|
| A | Zero-shot task instructions and schema; no worked examples. |
| B-k3 | Three nested development examples: DEV-01, DEV-06, DEV-17. |
| B-k5 | B-k3 plus DEV-10 and DEV-14. |
| B-k8 | B-k5 plus DEV-11, DEV-03, and DEV-09. |
| C | Requests a concise evidence summary followed by a `<final_json>` block. |

The library contains A, B-k3, B-k5, B-k8, and C. D is optional and was not
implemented. The rubric asks few-shot examples to cover all classes:
k=3 covers three of six claim types, k=5 covers five, and k=8 covers all six.
Because each example has exactly one claim-type label, covering six classes
requires at least six examples. Thus literal all-class coverage for k=3 and
k=5 is mathematically impossible without changing the requested k or the
single-label task. The current C prompt requests an evidence summary, not the
rubric's step-by-step chain-of-thought format. A separately versioned
[evidence-summary candidate](../prompts/prompt_c_evidence_summary_v1.3_candidate.md)
is provided for review, but it is untested and is not equivalent to hidden
chain-of-thought. The evaluated baseline prompt files were not changed.

All variants used `openai/gpt-oss-120b`, temperature 0, maximum 800 output
tokens, low reasoning effort, hidden reasoning format, and one run. Examples
were selected from development records only. The output must contain exactly
nine schema fields. An unusable response remains an invalid prediction in the
denominator rather than being dropped.

## 4. Development results

### Classification

**Table 1 — Classification on the development split.** Accuracy and macro-F1
are unitless proportions; `all` uses n=20 and `dev_eval` uses n=12.

| Prompt | Claim type, all | Urgency, all | Claim type, dev_eval | Urgency, dev_eval |
|---|---:|---:|---:|---:|
| A | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| B-k3 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| B-k5 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| B-k8 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| C | 0.950 / 0.976 | 0.950 / 0.952 | 0.917 / 0.967 | 0.917 / 0.933 |

**Takeaway:** A and B have identical classification scores in this sample;
C's invalid DEV-20 response lowers its accuracy. These small development
denominators do not establish comparative generalization.

### Confusion matrices

**Table 2 — Claim-type confusion matrix, `dev_eval` (n=12, counts; rows are
gold, columns predicted).** OD=own damage, TPL=third-party liability,
TH=theft, TL=total loss, GL=glass only, NC=not a claim. A, B-k3, B-k5, and
B-k8 have the same matrix shown below.

| Gold \ Predicted | OD | TPL | TH | TL | GL | NC | Invalid |
|---|---:|---:|---:|---:|---:|---:|---:|
| OD | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| TPL | 0 | 2 | 0 | 0 | 0 | 0 | 0 |
| TH | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| TL | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| GL | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| NC | 0 | 0 | 0 | 0 | 0 | 3 | 0 |

**Table 3 — Claim-type confusion matrix, Prompt C, `dev_eval` (n=12,
counts; rows are gold, columns predicted).**

| Gold \ Predicted | OD | TPL | TH | TL | GL | NC | Invalid |
|---|---:|---:|---:|---:|---:|---:|---:|
| OD | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| TPL | 0 | 2 | 0 | 0 | 0 | 0 | 0 |
| TH | 0 | 0 | 2 | 0 | 0 | 0 | 0 |
| TL | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| GL | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| NC | 0 | 0 | 0 | 0 | 0 | 2 | 1 |

**Takeaway:** C's sole confusion in this view is the invalid DEV-20 output,
whose gold class is not a claim. The other four prompts have no class
confusions in this view.

**Table 4 — Urgency confusion matrices, `dev_eval` (n=12, counts; rows are
gold, columns predicted).** A and all B variants share the first matrix.

| A/B prompts: gold \ predicted | High | Medium | Low | Invalid |
|---|---:|---:|---:|---:|
| High | 4 | 0 | 0 | 0 |
| Medium | 0 | 5 | 0 | 0 |
| Low | 0 | 0 | 3 | 0 |

| Prompt C: gold \ predicted | High | Medium | Low | Invalid |
|---|---:|---:|---:|---:|
| High | 4 | 0 | 0 | 0 |
| Medium | 0 | 5 | 0 | 0 |
| Low | 0 | 0 | 2 | 1 |

**Takeaway:** C's invalid result is the only urgency error in `dev_eval`;
the matrices are development-only, not held-out test matrices.

### Per-field exact match and F1

**Table 5 — Field-level exact match / macro-F1 by prompt and view.** Each
cell is a unitless proportion. `A/B-k3/B-k5` are grouped because their
reported field metrics are identical in both views.

| Field | A/B-k3/B-k5 all | B-k8 all | C all | A/B-k3/B-k5 dev_eval | B-k8 dev_eval | C dev_eval |
|---|---:|---:|---:|---:|---:|---:|
| Policy number | 1.000 / 1.000 | 1.000 / 1.000 | 0.950 / 0.994 | 1.000 / 1.000 | 1.000 / 1.000 | 0.917 / 0.982 |
| Incident date | 1.000 / 1.000 | 1.000 / 1.000 | 0.950 / 0.995 | 1.000 / 1.000 | 1.000 / 1.000 | 0.917 / 0.985 |
| Location | 0.950 / 0.875 | 0.950 / 0.875 | 0.900 / 0.869 | 0.917 / 0.778 | 0.917 / 0.778 | 0.833 / 0.765 |
| Vehicle registration | 1.000 / 1.000 | 1.000 / 1.000 | 0.950 / 0.995 | 1.000 / 1.000 | 1.000 / 1.000 | 0.917 / 0.987 |
| Estimated amount | 1.000 / 1.000 | 1.000 / 1.000 | 0.950 / 0.994 | 1.000 / 1.000 | 1.000 / 1.000 | 0.917 / 0.985 |
| Injury reported | 1.000 / 1.000 | 0.950 / 0.656 | 0.950 / 0.989 | 1.000 / 1.000 | 0.917 / 0.647 | 0.917 / 0.978 |
| Police report | 1.000 / 1.000 | 1.000 / 1.000 | 0.950 / 0.988 | 1.000 / 1.000 | 1.000 / 1.000 | 0.917 / 0.980 |

**Takeaway:** Location is the only strict-match mismatch shared by all
prompts; B-k8 also has one injury-field mismatch. Lenient location match is
1.000 for A/B prompts and 0.950 for C in `all`, and 1.000 / 0.917
respectively in `dev_eval`.

### Validity, safety, hallucination, and operations

**Table 6 — Parse/schema validity.** Values are valid responses / expected
responses, reported as proportions; invalid output is retained in the
denominator.

| Prompt | Parse/schema, all (n=20) | Parse/schema, dev_eval (n=12) |
|---|---:|---:|
| A, B-k3, B-k5, B-k8 | 1.000 / 1.000 | 1.000 / 1.000 |
| C | 0.950 / 0.950 | 0.917 / 0.917 |

**Takeaway:** A and B outputs are fully parse/schema valid in this sample;
Prompt C's one invalid response lowers its rates.

For every prompt in both views, high-urgency recall is 1.000 (zero false
negatives), injury-true recall is 1.000 (zero false negatives), and the
measured hallucination rate is 0.000. These are sample measurements, not
safety guarantees. Run-to-run agreement is unavailable (`null`) because each
prompt has one run.

**Table 7 — Recorded all-view mean usage and latency.** Token values are
tokens per observation; latency is seconds per observation. Dollar cost is
not available because price constants are unset.

| Prompt | Input tokens | Output tokens | Reasoning tokens | Latency (s) | Cost / 1,000 |
|---|---:|---:|---:|---:|---|
| A | 1531.9 | 212.85 | 118.95 | 1.890 | Not available |
| B-k3 | 2261.9 | 195.15 | 101.10 | 2.337 | Not available |
| B-k5 | 2585.9 | 197.70 | 103.80 | 1.918 | Not available |
| B-k8 | 3090.9 | 187.70 | 93.80 | 2.425 | Not available |
| C | 1750.9 | 314.10 | 127.00 | 1.911 | Not available |

**Takeaway:** A has the lowest recorded mean input tokens and latency in this
single development run; provider cost cannot be inferred from these numbers.

## 5. Error analysis

The only invalid stored result is `dev|DEV-20|C|1`. It failed the required
exactly-one-`<final_json>`-block contract; parse and schema checks are false.
It remains an invalid prediction for claim type, urgency, and extracted
fields, not a missing observation. No raw response body is reproduced.

DEV-13 has a strict location mismatch for all five prompts but passes the
lenient location rule. B-k8 also has one `injury_reported` exact mismatch on
DEV-12. No other valid classification or extraction mismatches were
identified.

**Table 8 — Actual failing observation counts versus the rubric's 15-case
minimum.** A failure case here means one observation with at least one
recorded mismatch or invalid output; counts are not padded or simulated.

| Prompt | Actual failing observations (n=20) | Invalid stored results | Shortfall to 15 |
|---|---:|---:|---:|
| A | 1 | 0 | 14 |
| B-k3 | 1 | 0 | 14 |
| B-k5 | 1 | 0 | 14 |
| B-k8 | 2 | 0 | 13 |
| C | 2 | 1 | 13 |

The required 15 categorized failures per prompt is **not met**. The
[failure-analysis workbook](development_error_analysis.xlsx) contains only
the recorded cases and the shortfall; the
[narrative error analysis](development_error_analysis.md) explains them.

## 6. Findings and decision questions

- **Does few-shot beat zero-shot?** Not in these data. A, B-k3, and B-k5 tie
  on classification and all reported field metrics; B-k8 has the additional
  injury-field mismatch.
- **Does increasing k from 3 to 5 to 8 improve accuracy?** No classification
  improvement is observed. Increasing to k=8 did not improve scores and
  coincides with the single B-k8 injury mismatch; this sample cannot establish
  a causal prompt effect.
- **Does reasoning help ambiguous or multi-incident messages, and is its
  extra cost justified?** Not established. Prompt C is an evidence-summary
  prompt rather than the specified step-by-step CoT prompt, there is no
  controlled case-level benefit analysis, it has one invalid response, and
  no dollar cost is available.
- **What should serve 5,000 messages/day?** Do not ship any prompt on this
  evidence. Carry A as a simple control into the required held-out and
  load-tested comparison: it ties A/B classification performance while
  having the lowest measured input-token use and latency here. This is a
  candidate for testing, not a production recommendation.

## 7. Proposed production strategy and guardrails

Keep the model behind a controlled intake service and use schema validation
before downstream processing. Reject malformed or unsupported outputs;
route invalid responses, unresolved conflicts, and high-risk cases to a
trained reviewer. Do not convert an invalid response into confident nulls.
Log prompt/model versions and operational metrics with appropriate data
controls. Monitor parse/schema rates, injury and high-urgency false
negatives, identifier hallucinations, latency, throughput, and token use.
Before a 5,000-message/day deployment, complete held-out evaluation,
independent label review, load/capacity testing, and a cost measurement using
approved prices. The schema has no abstention/confidence field, so any
confidence gate or abstention workflow needs a separately approved design.

## 8. Rubric status, limitations, and next steps

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| 20 development + 60 held-out records | COMPLETE (structure only) | Both JSONL files validate; no held-out prediction results are presented. |
| Independent review of at least 20 labels | NOT COMPLETE | A blank [20-record worksheet](development_label_review_worksheet.xlsx) is prepared; no responses or agreement statistic exist. |
| One-page labeling guide | COMPLETE | [Concise guide](one_page_labeling_guide.md) derived from the rubric and project rules; rendered to one A4 page. |
| A and B-k3/B-k5/B-k8 prompt library | PARTIAL | All exist; only k=8 spans all six claim types. |
| Prompt C step-by-step reasoning strategy | PARTIAL | Existing prompt asks for an evidence summary; a separately versioned candidate is untested and does not claim equivalence to CoT. |
| Optional Prompt D | OPTIONAL / NOT IMPLEMENTED | No D prompt file is present. |
| Same held-out set, three runs per prompt | NOT COMPLETE | The 60-record held-out evaluation has not been run. |
| Classification, field, validity, safety and usage metrics | COMPLETE for development only | See this report and the verified development bundle; not test metrics. |
| At least 15 categorized failures per prompt | NOT COMPLETE | Actual development failure counts are 1, 1, 1, 2, and 2. |
| 4–6 page report PDF | COMPLETE | Locally rendered A4 PDF verified at four pages with page numbers and tables within page bounds. |
| Error-analysis sheet | PARTIAL | Workbook contains actual failures only; the rubric's count threshold is unmet. |
| Demo/screenshots | NOT COMPLETE | Checklist exists; no human-recorded demo or screenshots are included. |

Immediate next steps are a second-person review of at least 20 labels, resolving
the documented adjudication policy, and conducting the held-out protocol only
when authorized. Do not tune prompts against the held-out split.

## 9. Artifacts and provenance

The report uses only the verified development evaluation bundle:
[`results.csv`](../outputs/evaluations/dev-f9639d694d88-e7b8df00/results.csv),
[`metrics.json`](../outputs/evaluations/dev-f9639d694d88-e7b8df00/metrics.json),
and [`manifest.json`](../outputs/evaluations/dev-f9639d694d88-e7b8df00/manifest.json).
The manifest records `split: dev`, the plan digest, dataset and prompt hashes,
and the exact isolated results-store hash.

Other submission materials: [README](../README.md), [prompt library](../prompts/README.md),
[detailed labeling guide](../data/labeling_guide.md), [one-page guide](one_page_labeling_guide.md),
[blank reviewer worksheet](development_label_review_worksheet.xlsx),
[development error analysis](development_error_analysis.md),
[failure-analysis workbook](development_error_analysis.xlsx), and [demo checklist](demo_checklist.md).
