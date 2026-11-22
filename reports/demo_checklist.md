# ClaimFast Case Study 1 demo checklist

## Opening statement

- [ ] State that the displayed metrics are **development-only** results from
  20 synthetic records.
- [ ] State that the 60-record held-out evaluation with three runs has not
  been completed.
- [ ] Do not call development metrics test or held-out results.
- [ ] Do not claim that 15 failures per prompt were demonstrated.

## Suggested demo sequence

Target duration: **3 minutes**. Use only the already-generated local
artifacts.

1. **0:00–0:30 — Problem and status.** Introduce the claim-intake task and
   nine-field output. State prominently that all metrics are DEVELOPMENT-ONLY
   and that the 60-record held-out evaluation with three runs is incomplete.
2. **0:30–1:00 — Prompt set.** Show A, B-k3, B-k5, B-k8, and C in the prompt
   library. Mention that D is optional/not implemented, k=3/k=5 do not cover
   all claim classes, and C is an evidence-summary prompt rather than the
   rubric's step-by-step reasoning prompt.
3. **1:00–1:45 — Evaluation evidence.** Open the current bundle's manifest
   (`split: dev` and input hashes), then show `results.csv` and the report's
   confusion matrices, field metrics, validity, safety, and usage tables.
   Explicitly state that cost is unavailable and run agreement cannot be
   measured from one run.
4. **1:45–2:30 — Errors.** Show the failure-analysis workbook and narrative:
   DEV-20/C is invalid because it violated the `<final_json>` block
   requirement; DEV-13 has a strict location mismatch accepted by lenient
   matching; B-k8 has a DEV-12 injury-field mismatch. Do not display raw
   response bodies.
5. **2:30–3:00 — Recommendation and gaps.** Do not recommend production
   release. Identify A only as a control candidate for further evaluation,
   then state the outstanding second review, held-out protocol, failure-count
   requirement, and human-recorded demo.

## Deliverable navigation

- Report: [PDF](final_case_study_report.pdf),
  [HTML](final_case_study_report.html), and
  [Markdown](final_case_study_report.md).
- Evaluation bundle: [results.csv](../outputs/evaluations/dev-f9639d694d88-e7b8df00/results.csv),
  [metrics.json](../outputs/evaluations/dev-f9639d694d88-e7b8df00/metrics.json),
  and [manifest.json](../outputs/evaluations/dev-f9639d694d88-e7b8df00/manifest.json).
- Errors: [narrative analysis](development_error_analysis.md) and
  [actual-case workbook](development_error_analysis.xlsx).
- Method and data: [prompt library](../prompts/README.md),
  [one-page guide](one_page_labeling_guide.md),
  [detailed labeling guide](../data/labeling_guide.md),
  [blank review worksheet](development_label_review_worksheet.xlsx),
  and
  [project README](../README.md).
- Prompt C candidate: [v1.3 evidence summary](../prompts/prompt_c_evidence_summary_v1.3_candidate.md);
  it is untested and must not be presented as evaluation output.

## Screenshot checklist

Screenshots are **not included yet** and no demo has been recorded. Capture
only existing local artifacts; do not rerun an experiment for the demo.

- [ ] Evaluation table showing `dev` and both views.
- [ ] Report page showing DEVELOPMENT-ONLY classification and confusion
  matrices.
- [ ] Manifest showing the dev split and provenance hashes.
- [ ] Failure-analysis workbook summary and actual recorded cases.
- [ ] One-page labeling guide and prompt-version note showing the C candidate
  is untested.
- [ ] Error-analysis section identifying DEV-20/C as invalid, without
  displaying raw response content.
- [ ] README section linking the submission artifacts and stating the
  evaluation status.

## Safety and evidence checks

- [ ] Keep `.env`, API keys, credentials, and raw response bodies out of
  screenshots and presentation material.
- [ ] Do not open or display the held-out/test dataset.
- [ ] Do not make API/network calls or execute the experiment during this
  offline demo.
- [ ] Keep the existing evaluation bundle and experiment store unchanged.
- [ ] Attribute every metric to the development split and its denominator;
  note one run means run-to-run agreement is unavailable.
- [ ] Identify unavailable dollar cost as unreported because price constants
  are unset; do not estimate a cost.

## Evidence still required before claiming rubric completion

- [ ] A second reviewer completes the blank worksheet for DEV-01 through
  DEV-20 independently, before seeing provisional labels.
- [ ] After that blind pass, record field-level disagreements, adjudications,
  reviewer identity/date, and the agreement method and result. The current
  worksheet contains no reviewer responses.
- [ ] Authorized held-out evaluation on the frozen 60 records, with the same
  set and three runs per prompt; retain actual outputs, usage, latency, and
  metrics. Do not tune against this split.
- [ ] At least 15 real, categorized failures per prompt with cause and
  proposed fix. The current development counts do not satisfy this threshold;
  never pad the worksheet or error analysis.
- [ ] A human-recorded three-minute demo or genuine screenshots showing one
  development message through A, one B variant, and C using actual outputs.
  Do not fabricate or imply this evidence already exists.
