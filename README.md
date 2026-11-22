# ClaimFast

ClaimFast is Case Study 1 of an Applied LLM Engineering course. It classifies
insurance claim messages into a fixed nine-field JSON structure using Groq's
`openai/gpt-oss-120b` model.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `GROQ_API_KEY` in the local `.env` file. Do not commit that file.

## Run

Preview a bounded development run without contacting the API. The run-count
default is `RUNS_PER_PROMPT` in `src/config.py` (currently one); `--runs`
overrides it:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m src.run_experiment \
  --split dev --prompts A --runs 1 --max-calls 1 --dry-run
```

Before a live run, set `DAILY_TOKEN_LIMIT` in `src/config.py` from the Groq
console. If you explicitly need to run without this limit, pass
`--no-daily-limit`. The runner asks for confirmation unless `--yes` is supplied:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m src.run_experiment \
  --split dev --prompts A --max-calls 1
```

Use `--split dev` with `--prompts` containing one or more of `A`, `B-k3`,
`B-k5`, `B-k8`, `C`, `--runs N`, and optionally `--max-calls N`. The held-out
split is restricted to the complete five-prompt, three-run grid described
below. Completed result keys are skipped on resume. `--dry-run` makes no API
request or results write; the held-out dry run also avoids loading test
records and result stores. `--yes` skips confirmation.

The current plan-bound development run saves results to
`outputs/development_runs/dev-f9639d694d88/results.json` using a temporary
file and atomic replacement. Its retry log is
`outputs/development_runs/dev-f9639d694d88/retry_log.jsonl`. The legacy
root-level `outputs/raw_responses.json` and `outputs/retry_log.jsonl` are not
the source for the current Case Study 1 evaluation. HTTP 429 responses have
at most five retries per observation, including across restarts. Numeric and
HTTP-date `Retry-After` values are honored. If the header is absent, waits
are 5, 10, 20, 40, and 40 seconds. An unparseable supplied value saves
progress and stops the run with a non-zero exit status. Other errors are
recorded and not retried. New retry-log entries capture sanitized 429
diagnostics, including parsed Retry-After data, allowlisted rate-limit
remaining/reset headers, and a provider request ID; older entries remain
readable. The request delay is two seconds.

The daily limit defaults to `None`, so a live run refuses to start until it is
set from the Groq console, unless `--no-daily-limit` is explicit. Before each
request, the runner checks today's usage in UTC plus an estimate of the next
call: average observed total tokens, or rendered prompt length divided by four
plus `MAX_TOKENS` when no complete usage history is available. Missing stored
usage or timestamps are estimated conservatively.

`--refresh-meta` prints old and new prompt hashes and settings and refreshes
metadata only when the selected results store has no records. It requires
confirmation (or `--yes`); `--refresh-meta --dry-run` previews without
writing.

## Evaluate

The verified evaluation below uses the isolated development results store and
the `dev` split only:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m src.evaluate \
  --split dev \
  --results outputs/development_runs/dev-f9639d694d88/results.json
```

The evaluator's default results path is the legacy store; keep the explicit
`--results` argument for this run.

Each evaluation writes a new bundle under `outputs/evaluations/` containing
`results.csv`, `metrics.json`, and `manifest.json`; existing bundles are
preserved. The current verified bundle is
[`dev-f9639d694d88-e7b8df00`](outputs/evaluations/dev-f9639d694d88-e7b8df00/).
Its headline table is [results.csv](outputs/evaluations/dev-f9639d694d88-e7b8df00/results.csv),
its detail is [metrics.json](outputs/evaluations/dev-f9639d694d88-e7b8df00/metrics.json),
and its provenance is [manifest.json](outputs/evaluations/dev-f9639d694d88-e7b8df00/manifest.json).

**Evaluation status:** current metrics are DEVELOPMENT-ONLY. The
60-record held-out evaluation with three runs has not been completed; do not
describe these development results as test or held-out results. The observed
development outputs do not demonstrate 15 failures per prompt. Do not run
`--split test` as part of this submission snapshot.

## Held-out evaluation infrastructure

A separate pinned plan is defined in
[`plans/heldout_test_plan_v1.json`](plans/heldout_test_plan_v1.json). It
covers `TEST-01` through `TEST-60`, the five existing prompts, runs 1–3, and
900 unique observation keys. Its dataset SHA-256 is
`6ed1d877c461a3e4d6252a52f882f515337ef95a3fc9816426de2f62921f13a4`;
its plan digest is
`16121fec106ce4b819f3093bedaba2d00eb83153cb71964759e6252864319b21`.
The runner verifies the snapshot hash and line count without parsing test
records during preflight. During inference it extracts only `original_message`;
labels are loaded by the evaluator after inference.

Held-out results and rate-limit retry state use
`outputs/heldout_runs/heldout-test-16121fec106c/`, separate from development
and legacy stores. A plan-only dry run verifies the complete key grid and
these isolated paths without loading records, reading result stores, or
creating a provider client:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m src.run_experiment \
  --split test --prompts A B-k3 B-k5 B-k8 C --runs 3 --dry-run
```

Held-out evaluation requires the isolated store explicitly:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m src.evaluate \
  --split test \
  --plan plans/heldout_test_plan_v1.json \
  --results outputs/heldout_runs/heldout-test-16121fec106c/results.json
```

This documents infrastructure only; no held-out experiment or evaluation has
been run. Human approval is still required before any model requests.

The dev report has an `all` view and a common `dev_eval` view that excludes
the union of few-shot examples: `DEV-01`, `DEV-03`, `DEV-06`, `DEV-09`,
`DEV-10`, `DEV-11`, `DEV-14`, and `DEV-17`. IDs follow the runner's line
mapping: `DEV-01` is the first development record; test IDs are
`TEST-01` through `TEST-60`. The evaluator checks stored keys against the
authoritative plan and stops on missing or unexpected keys. An existing
failed or invalid result remains in the denominator and scores as invalid.
Expected run numbers come from the plan. Location lenient matching uses
case-folding, punctuation removal, containment, or Jaccard token overlap of
at least 0.5. Cost is left blank until both Groq per-million prices are set
in `src/config.py`.

## Case Study 1 submission artifacts

- Final report: [PDF](reports/final_case_study_report.pdf),
  [HTML](reports/final_case_study_report.html), and
  [Markdown](reports/final_case_study_report.md).
- Actual development error cases: [workbook](reports/development_error_analysis.xlsx)
  and [narrative](reports/development_error_analysis.md).
- [One-page labeling guide](reports/one_page_labeling_guide.md) and
  [blank 20-record independent-review worksheet](reports/development_label_review_worksheet.xlsx).
- [Untested Prompt C evidence-summary candidate](prompts/prompt_c_evidence_summary_v1.3_candidate.md);
  it does not replace the evaluated prompt or claim CoT equivalence.
- [Three-minute demo and screenshot checklist](reports/demo_checklist.md).
- Current evidence: [evaluation CSV](outputs/evaluations/dev-f9639d694d88-e7b8df00/results.csv),
  [detailed metrics](outputs/evaluations/dev-f9639d694d88-e7b8df00/metrics.json),
  and [manifest](outputs/evaluations/dev-f9639d694d88-e7b8df00/manifest.json).
- Method materials: [prompt library](prompts/README.md) and
  [synthetic-data labeling guide](data/labeling_guide.md).

**Submission status:** every model metric above is DEVELOPMENT-ONLY. The
60-record held-out evaluation with three runs and independent review of at
least 20 labels are incomplete; the review workbook is blank. The one-page
guide is complete. Literal all-class example coverage is impossible for k=3
and k=5 with single-label examples; k=8 covers all six classes. The
development outputs do not meet the rubric's 15 categorized failures per
prompt. No screenshots or human-recorded demo are included. Do not present
this snapshot as full rubric compliance or production readiness.

## Main files

- `src/run_experiment.py`: CLI, planning, confirmation, and invocation cap.
- `src/evaluate.py`: offline scoring CLI for a selected split.
- `src/metrics.py`: pure scoring functions; it does not read or write files.
- `src/experiment_runtime.py`: request loop, pacing, retry handling, and
  optional daily token limit.
- `src/results_store.py`: result schema, resume lookup, atomic save, and
  migration from completed legacy observations.
- `src/prompt_helpers.py`: prompt loading/rendering, Prompt C extraction, and
  prompt-hash metadata.
- `src/response_validation.py`: shared nine-field JSON validation.
- `src/config.py` and `src/groq_client.py`: project settings and Groq client.
- `scripts/`: standalone Groq smoke-test scripts, separate from the runner.
- `prompts/`: five evaluated baseline variants and a separately versioned,
  untested Prompt C candidate.
- `data/dev.jsonl`: development records. `data/test.jsonl` is reserved for
  evaluation and must not be used for development runs.
- Frozen test split: data/test.jsonl is frozen; never used for prompt tuning.
- [Current isolated development run](outputs/development_runs/dev-f9639d694d88/):
  results and retry log.
- [Evaluation bundles](outputs/evaluations/): each run writes a new bundle
  with headline CSV, detailed metrics, and input/output manifest.
- [Final Case Study 1 report](reports/final_case_study_report.md),
  [PDF](reports/final_case_study_report.pdf),
  [development error analysis](reports/development_error_analysis.md),
  [failure workbook](reports/development_error_analysis.xlsx), and
  [demo checklist](reports/demo_checklist.md): submission materials.

## Viva practice

1. **How does resume work?**  
   Each record is keyed by split, record ID, prompt ID, and run number. Existing
   keys are skipped when the results file is loaded.
2. **Why save atomically?**  
   The store writes and flushes a temporary file before `os.replace`, reducing
   the chance that interruption leaves a partially written results file.
3. **How are failed requests handled?**  
   HTTP 429 is retried up to five times with a provider wait or a bounded
   fallback schedule. Other errors are saved and the runner continues without
   an automatic retry.

## Metrics viva practice

1. **Why score `dev_eval` separately from all development records?**  
   It excludes every record used as a few-shot example in any B prompt, so
   each prompt is compared on the same unseen development records.
2. **How are missing or invalid predictions treated?**  
   The evaluator stops if the store is missing a plan-required key. A stored
   invalid result remains in the denominator as wrong; it is never silently
   dropped.
3. **How does the evaluator detect hallucinations and lenient location matches?**  
   A non-null extraction against a null gold value is a hallucination; policy
   and vehicle identifiers must also occur in the normalized message. Location
   leniency uses case-folded text, stripped punctuation, containment, or at
   least 0.5 Jaccard token overlap.
