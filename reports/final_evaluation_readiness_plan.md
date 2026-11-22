# Final Evaluation Readiness — Implementation Plan

## Scope and current verdict

This report reflects the implemented plan-bound development workflow in the
current working tree. It documents the authoritative plan, plan verification,
exact coverage enforcement, and immutable evaluation bundles introduced for the
local development workflow only. No provider client, runner execution, live
experiment, held-out data, or historical result migration was performed.

**Current evaluation readiness: plan-bound development workflow implemented and
offline-validated; live-provider evaluation remains blocked.** The approved
development plan is explicit and verified against the current dataset and
prompt files. Historical results remain excluded from plan-bound evaluation
unless an owner-approved provenance gate is added for them.

## Actual implementation status

The following files are present and modified in the working tree and are part
of the current implementation:

- `plans/authoritative_experiment_plan.json`
- `src/config.py`
- `src/experiment_plan.py`
- `src/run_experiment.py`
- `src/prompt_helpers.py`
- `src/evaluate.py`
- `tests/test_experiment_plan.py`
- `tests/test_evaluate.py`

These files implement the minimal plan-bound development evaluation workflow,
with private, immutable `outputs/evaluations/<id>/` bundle publication and
offline plan validation. The project still does not treat the historical 53
results as authoritative for plan-bound evaluation.

## Current-state findings

- The authoritative development plan in `plans/authoritative_experiment_plan.json`
  matches the current development dataset and prompt files. The plan includes the
  dataset SHA-256, prompt SHA-256 values, exact run grid `[1]`, matching model
  settings from `src/config.py`, and an approved digest pin that matches the
  configuration value in `src/config.py`.
- `src/experiment_plan.py` validates the canonical plan and rejects dataset,
prompt, model-setting, and digest mismatches before any evaluation proceeds.
- `src/run_experiment.py` checks the current plan before continuing and rejects
  any requested result key outside the approved schedule.
- `src/evaluate.py` enforces exact expected keys and coverage, then writes a new
  evaluation bundle to `outputs/evaluations/<id>/` rather than overwriting the
  fixed legacy output paths.

## Remaining owner decisions

- Whether the in-repo pinned digest is the accepted trust boundary for future
  approval decisions, or whether a separate externally signed approval is still
  required.
- Whether the historical development ResultsStore is ever re-used under an
  explicit, owner-approved plan and provenance process; absent that decision,
  it remains unverified and excluded.
- Whether a future live development experiment should proceed only after the
  owner authorizes the approved plan and a real runner/provider flow.

## Current-state findings

- **Configuration is not a plan.** [`src/config.py` line 19](../src/config.py#L19)
  records a model, lines [22](../src/config.py#L22),
  [24](../src/config.py#L24), and [31](../src/config.py#L31) record generation
  settings and a pilot run default. The runner accepts split, prompt, and run
  arguments at [`src/run_experiment.py` line 51](../src/run_experiment.py#L51),
  [line 53](../src/run_experiment.py#L53), and
  [line 58](../src/run_experiment.py#L58); these effective choices are not a
  durable, authoritative schedule.
- **Existing metadata is incomplete for evaluation identity.**
  [`src/prompt_helpers.py` line 77](../src/prompt_helpers.py#L77) hashes prompt
  files and records model/generation fields at
  [line 84](../src/prompt_helpers.py#L84) through
  [line 91](../src/prompt_helpers.py#L91). It does not bind the result store
  to a dataset snapshot, selected split/prompt/run schedule, or immutable plan.
  `created_utc` is descriptive, not an integrity check.
- **Result keys do not establish expected coverage.** The identity is
  `split|record_id|prompt_id|run` at
  [`src/results_store.py` line 28](../src/results_store.py#L28). Dataset IDs
  are currently assigned from line order at
  [`src/experiment_runtime.py` line 29](../src/experiment_runtime.py#L29)
  and [line 40](../src/experiment_runtime.py#L40). Neither identifies the
  dataset bytes used for a historical run.
- **The evaluator infers coverage from observed records.**
  [`src/evaluate.py` line 57](../src/evaluate.py#L57) defines
  `expected_run_numbers()`; [line 68](../src/evaluate.py#L68) uses the maximum
  run found in the selected split and applies that run range to each prompt
  ([line 174](../src/evaluate.py#L174)). It loads the store without plan
  verification at [line 172](../src/evaluate.py#L172), then scores per prompt
  at [line 202](../src/evaluate.py#L202).
- **Existing output publication is destructive and non-transactional.**
  [`src/evaluate.py` line 223](../src/evaluate.py#L223) opens fixed
  `metrics.json` in write mode; [line 226](../src/evaluate.py#L226) does the
  same for `results.csv`, sequentially.
- **The metric and recovery boundaries are reusable.**
  [`src/metrics.py` line 44](../src/metrics.py#L44) treats
  `error is not None` as unusable at [line 50](../src/metrics.py#L50), and
  `score_predictions()` aligns results against its supplied run set.
  [`src/recovery_evaluation.py` line 18](../src/recovery_evaluation.py#L18)
  calls `build_scoreable_results()` at [line 23](../src/recovery_evaluation.py#L23)
  and retains selection events and discrepancy diagnostics. The ledger’s
  builder is the supported scoreability boundary at
  [`src/recovery_ledger.py` line 917](../src/recovery_ledger.py#L917).
- **The reports confirm the blocker.** The authoritative-plan fields and
  missing approval mechanism are explicit at
  [`reports/phase_b2_recovery_protocol_specification.md` line 49](./phase_b2_recovery_protocol_specification.md#L49)
  and [line 54](./phase_b2_recovery_protocol_specification.md#L54).
  B2-D12 requires exact planned keys, coverage, prompt/config identity, and a
  verifiable plan hash at [line 479](./phase_b2_recovery_protocol_specification.md#L479);
  B2-D13 requires versioned outputs at
  [line 480](./phase_b2_recovery_protocol_specification.md#L480).
  The prior integration report also marks full recovery-aware evaluation
  blocked at
  [`reports/offline_recovery_evaluation_integration_report.md` line 17](./offline_recovery_evaluation_integration_report.md#L17).

**Conclusion on existing artifacts:** none qualifies as authoritative. In
particular, the current result rows cannot be used to infer missing planned
keys or run counts. A plan created now also cannot, by itself, prove which
dataset snapshot produced historical rows; legacy results without trustworthy
provenance must not be labeled plan-verified retroactively.

## Proposed minimal implementation

1. **Create and approve one canonical plan artifact.** Add
   `plans/authoritative_experiment_plan.json` with a schema version and:
   - Exact expected observation keys (`split`, `record_id`, `prompt_id`, and
     positive integer `run`), with unique keys and an explicit rectangular
     run set for every planned split/prompt. The rectangular constraint matches
     the current scorer’s per-prompt run-grid interface.
   - For each planned split, the SHA-256 of the exact dataset file bytes and
     the expected record IDs. Dataset bytes must be hashed and parsed from the
     same read during execution.
   - The selected prompt IDs, filenames and SHA-256 hashes; model and effective
     generation settings; and the planned split/prompt/run coverage.
   - A plan version and evaluation-policy/schema version. No credentials,
     payloads, gold labels, or response contents belong in the plan.

2. **Verify plan identity from a trusted pin.** Add a shared
   `src/experiment_plan.py` loader/validator and `src/config.py` constants for
   the plan path and expected plan SHA-256. Hash canonical UTF-8 JSON (sorted
   keys, fixed separators, no NaN, reject duplicate keys); compare it with the
   separately pinned digest before using plan fields. Validate schema, unique
   keys, rectangular coverage, and internal consistency. Hash the current
   dataset and prompt files and compare with the approved plan; compare
   effective model/generation settings and ResultsStore prompt/config metadata
   with the same plan. A plan file containing its own digest is not a trust
   anchor: an approved, reviewed change must update the separately pinned
   digest. No signature system is needed if the reviewed source pin is the
   accepted trust boundary.

3. **Bind new runner results to that plan.** Update
   `src/run_experiment.py` to verify the plan before any provider-client
   construction, reject any requested key outside its planned key set, and
   persist the verified plan digest and dataset snapshot digest in
   `ResultsStore.meta`. Evaluation must reject legacy stores missing these
   fields or having mismatched settings; do not backfill provenance and treat
   it as historical proof.

4. **Make evaluation plan-driven and recovery-aware.** Update
   `src/evaluate.py` to verify the selected planned split, load/hash gold data
   against the plan, and use only plan-derived expected run numbers and
   observation keys. Missing planned predictions remain missing/invalid in
   `score_predictions()`; extra or malformed result keys and metadata
   mismatches fail closed. Never infer expected coverage from ResultsStore or
   recovery rows.

   Work on a copy of the loaded original records. Accept recovery events only
   from an explicitly supplied existing ledger path; no default ledger is
   created or searched. Feed originals and events to
   `build_recovery_diagnostic_snapshot()`, and feed metrics only the
   snapshot’s `scoreable_results.records`. Do not construct a parallel
   recovery-selection path. Preserve the adapter’s selection provenance,
   uncertainty, conflict diagnostics, and original/recovery discrepancy data.
   A planned key with no original and no ledger event is reported as missing
   without fabricating a ResultsStore record. A ledger event for an absent
   original is a contract error and blocks publication under the current
   resolver.

5. **Publish immutable evaluation bundles.** Write each requested split’s
   bundle under a new path such as
   `outputs/evaluations/<evaluation-id>/`, containing `metrics.json`,
   `results.csv`, `recovery_diagnostics.json`, and `manifest.json`. Derive the
   evaluation ID from the canonical plan digest, canonical semantic snapshot
   hashes of the loaded ResultsStore and ordered ledger events, the selected
   split, and the evaluation-policy version. Include those identities,
   selection provenance, and output-file hashes in the manifest. Build and
   validate all files in a private staging directory, then atomically publish
   the completed directory to a previously unused ID; never overwrite an
   existing version or the legacy fixed output paths. Treat an incomplete
   staging bundle or an existing unverified target as failure, not success.
   The manifest is the final completion record; serialize publishers.

The recovery diagnostic adapter and metric predicate already cover the
required scoreability rules; no new scoring abstraction or recovery-result
mutation is proposed. Update the recovery documentation to describe the
plan-bound evaluator and versioned output contract.

## Required invariants and failure behavior

- Plan digest, schema, dataset snapshot, prompt hashes, generation settings,
  and ResultsStore plan metadata must all verify before scoring.
- The exact expected grid comes only from the verified plan. Duplicates,
  unplanned rows, unexpected ledger keys, invalid plan keys, or non-rectangular
  coverage stop evaluation before publication.
- Only `build_scoreable_results()` output reaches `score_predictions()`.
  Unselected, unsuccessful, ambiguous, and conflicted recoveries remain
  unscoreable; selection provenance and conflict/uncertainty diagnostics are
  retained. A successful original remains scoreable under D10; error-marked
  outputs remain unusable under D15.
- Evaluation never saves or rewrites ResultsStore or the recovery ledger.
  Missing planned results are counted from the plan, not filled with fabricated
  records.
- No version is authoritative until every bundle file and its manifest are
  complete and validated. Any failure leaves existing versions and legacy
  `metrics.json`/`results.csv` untouched.

## Exact proposed implementation file list

1. `plans/authoritative_experiment_plan.json` — new approved plan artifact.
2. `src/config.py` — plan path, pinned digest, and evaluation-output root.
3. `src/experiment_plan.py` — new shared canonicalization, hash verification,
   schema/coverage validation, and plan lookup.
4. `src/run_experiment.py` — enforce planned keys and persist plan/dataset
   identity in ResultsStore metadata before any provider client is constructed.
5. `src/evaluate.py` — plan-based coverage, verified inputs, recovery-aware
   scoring, diagnostics, manifests, and no-overwrite publication.
6. `RECOVERY_LEDGER.md` — document the evaluator’s scoreability boundary and
   versioned output behavior.
7. `tests/test_experiment_plan.py` — new synthetic plan/digest/schema tests.
8. `tests/test_runner.py` — test plan enforcement and persisted identity using
   fakes only.
9. `tests/test_evaluate.py` — new isolated evaluator/output-publication tests.

No changes are proposed to `src/results_store.py`, `src/metrics.py`, or
`src/recovery_evaluation.py`; their existing APIs and regression coverage are
sufficient for this design.

## Focused verification plan

Do not invoke the real runner, evaluator CLI, provider, or project datasets.
Use temporary paths and synthetic plan/data/results/ledger records only.

- `tests/test_experiment_plan.py`: canonical digest accepts the pinned plan;
  edited bytes, bad schema, duplicate keys, inconsistent grids, changed
  dataset/prompt hashes, and mismatched settings are rejected.
- `tests/test_runner.py`: only planned keys can be scheduled; plan identity is
  persisted; a mismatch stops before fake provider invocation; partial calls
  remain missing relative to full planned coverage.
- `tests/test_evaluate.py`: expected/missing counts follow the plan even when
  observed run numbers differ; extra rows and mismatched metadata fail closed;
  selected recovery is scoreable only through the builder; unselected,
  unsuccessful, ambiguous, and conflicted results are excluded; D10
  discrepancy and D15 invalidity are preserved; input snapshots remain
  unchanged; output bundles contain matching hashes; write/publish failures
  do not replace prior versions or legacy files.
- Re-run existing focused tests in `tests/test_recovery_evaluation.py`,
  `tests/test_metrics.py`, `tests/test_results_store.py`, and
  `tests/test_runner.py` after implementation; do not use real or held-out
  records for these cases.

## Remaining decisions and prerequisites

- An authorized owner must define and approve the actual expected keys,
  planned run grid, split scope, prompt/config identities, and dataset
  snapshots. None was established or computed in this read-only inspection.
- Confirm that a reviewed in-repository digest pin is the trusted plan-change
  authority; otherwise select an independently verified signature mechanism
  before implementation.
- Decide whether historical ResultsStore rows have trustworthy contemporaneous
  provenance tying them to the proposed plan. If not, they remain unverified
  and must not be promoted to authoritative evaluation inputs.
- Implementation remains a separate authorization from this design. The
  protocol records that distinction at
  [`reports/phase_b2_recovery_protocol_specification.md` line 17](./phase_b2_recovery_protocol_specification.md#L17).
