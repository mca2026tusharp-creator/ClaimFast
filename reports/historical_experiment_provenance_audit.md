# Historical Experiment Provenance Audit

## Executive verdict

NOT VERIFIED.

There is partial contemporaneous provenance for the historical development run, but not enough to support a newly approved authoritative plan-bound evaluation of the historical ResultsStore without additional evidence. The strongest contemporaneous evidence is the ResultsStore metadata (`meta`) for model and prompt hashes, plus the result-key schedule implied by the stored record IDs. The missing pieces are the exact dataset snapshot hash, the authoritative plan covering split/prompt/run coverage, and a recorded execution manifest tying the historical results to that plan.

## Scope and boundaries

This audit used only read-only metadata inspection and source/report review. It did not inspect raw response bodies, retry logs, held-out datasets, or any protected output content beyond the `meta` object and aggregate record-key information required to assess coverage.

## Evidence inspected

- [`src/config.py`](../src/config.py#L18-L33): model and generation settings declared in source.
- [`src/prompt_helpers.py`](../src/prompt_helpers.py#L13-L25): prompt file map and prompt hashing strategy.
- [`src/prompt_helpers.py`](../src/prompt_helpers.py#L77-L92): ResultsStore metadata builder (`model`, `temperature`, `max_tokens`, `reasoning_effort`, `reasoning_format`, `prompt_sha256`, `created_utc`).
- [`src/results_store.py`](../src/results_store.py#L7-L33): record identity as `split|record_id|prompt_id|run`.
- [`src/run_experiment.py`](../src/run_experiment.py#L68-L144): runner loads dataset, prompt templates, and compares metadata for mismatches before scheduling calls.
- [`src/experiment_runtime.py`](../src/experiment_runtime.py#L29-L42): dataset record identity generation from file line numbers.
- [`reports/phase_b2_recovery_protocol_specification.md`](./phase_b2_recovery_protocol_specification.md#L49-L55): recommended authoritative-plan contents.
- [`reports/phase_b2_recovery_protocol_specification.md`](./phase_b2_recovery_protocol_specification.md#L479-L482): B2-D12 and B2-D13 require approved immutable plan + versioned outputs.
- [`reports/offline_recovery_evaluation_integration_report.md`](./offline_recovery_evaluation_integration_report.md#L17-L22): current recovery-aware evaluation remains blocked without an authoritative plan.

For the live historical ResultsStore, I inspected only the `meta` object and the aggregate record-key structure from `outputs/raw_responses.json` as permitted:

- `meta` keys observed: `created_utc`, `max_tokens`, `model`, `prompt_sha256`, `reasoning_effort`, `reasoning_format`, `temperature`.
- Record-key count observed: 53 records.
- First keys observed: `dev|DEV-01|A|1`, `dev|DEV-01|B-k3|1`, `dev|DEV-01|B-k5|1`, `dev|DEV-01|B-k8|1`, `dev|DEV-01|C|1`.

No raw response bodies, reasoning content, individual prediction payloads, retry-log entries, or dataset contents were inspected.

## Provenance matrix

### 1. Dataset snapshot provenance

Status: NOT VERIFIED.

Direct evidence:
- The runner reads `data/{split}.jsonl` at [`src/experiment_runtime.py`](../src/experiment_runtime.py#L29-L42) and [`src/run_experiment.py`](../src/run_experiment.py#L68-L70).
- The ResultsStore meta contains no `dataset_sha256`, no `dataset_path`, no `dataset_manifest`, and no plan identifier; the observed keys are limited to prompt/model/config fields as listed above.
- The audit reports explicitly state that the authoritative plan and dataset-snapshot identity remain unapproved or unresolved in the current policy docs: [`reports/phase_b2_recovery_protocol_specification.md`](./phase_b2_recovery_protocol_specification.md#L49-L55), [`reports/phase_b2_recovery_protocol_specification.md`](./phase_b2_recovery_protocol_specification.md#L479-L482), and [`reports/offline_recovery_evaluation_integration_report.md`](./offline_recovery_evaluation_integration_report.md#L17-L22).

Inference:
- Because no dataset hash or snapshot manifest is contemporaneously recorded in `ResultsStore.meta`, the exact development dataset bytes used by the historical run cannot be proven from the surviving project artifacts alone.
- A future plan that uses the historical results must establish a separate, approved dataset identity before those results can be treated as plan-bound.

### 2. Prompt provenance

Status: PARTIALLY VERIFIED.

Direct evidence:
- `build_results_meta()` records `prompt_sha256` for each prompt ID at [`src/prompt_helpers.py`](../src/prompt_helpers.py#L77-L92).
- The map of prompt IDs to files is fixed in [`src/prompt_helpers.py`](../src/prompt_helpers.py#L13-L25).
- The current ResultsStore `meta` includes `prompt_sha256` and the observed keys are present in a sustained artifact.

Limitations:
- Prompt hashes prove file identity at the time of metadata generation; they do not prove which prompt set was selected for the historical run unless the corresponding `meta` field is contemporaneously tied to that run and preserved without modification.
- The current meta object is from the historical ResultsStore, but without a versioned execution manifest, it is not enough to prove the final historical evaluation plan and all prompt/config selections across the entire run history. It proves only that these metadata fields existed in the stored snapshot and match the current source files as loaded by the project.

### 3. Model and effective generation settings

Status: PARTIALLY VERIFIED.

Direct evidence:
- The source declares `MODEL_NAME = "openai/gpt-oss-120b"`, `TEMPERATURE = 0`, `MAX_TOKENS = 800`, `REASONING_EFFORT = "low"`, and `REASONING_FORMAT = "hidden"` in [`src/config.py`](../src/config.py#L18-L33).
- `build_results_meta()` records those values in the ResultsStore metadata at [`src/prompt_helpers.py`](../src/prompt_helpers.py#L77-L92).
- The current `meta` object includes `model`, `temperature`, `max_tokens`, `reasoning_effort`, and `reasoning_format`.

Limitations:
- These values show the intended configuration for the run, but they are not an authoritative execution manifest. The runner also supports override parameters and the report notes a pilot default with runtime overrides at [`src/run_experiment.py`](../src/run_experiment.py#L51-L58).
- The code does not record a verifiable execution hash over the complete command line, dataset revision, or a separate run manifest. The `created_utc` timestamp is a descriptive timestamp, not a source-of-truth plan or run ledger.

### 4. Planned split/prompt/run coverage

Status: NOT VERIFIED.

Direct evidence:
- The runner computes the key space as `split|record_id|prompt_id|run` and schedules pending items by iterating the dataset records, prompt IDs, and run range at [`src/run_experiment.py`](../src/run_experiment.py#L125-L144).
- Range logic uses the run count selected at runtime in this source: `for run in range(1, args.runs + 1)` at [`src/run_experiment.py`](../src/run_experiment.py#L132-L142).
- The historical `ResultsStore` record keys imply one schedule pattern, but they do not prove that this was the originally approved plan rather than a later, modified run.

Limitations:
- The current code and reports explicitly say the authoritative plan was never identified or approved: [`reports/phase_b2_recovery_protocol_specification.md`](./phase_b2_recovery_protocol_specification.md#L49-L55), [`reports/phase_b2_recovery_protocol_specification.md`](./phase_b2_recovery_protocol_specification.md#L479-L482).
- `run_experiment.py` constructs planned keys dynamically from recorded data and selected arguments; it does not store a canonical plan snapshot or a stable digest. Therefore expected coverage cannot be treated as authoritative from the surviving artifacts alone.

### 5. Result identity and mapping to original dataset / plan

Status: PARTIALLY VERIFIED.

Direct evidence:
- `result_key()` defines the key format and the mapping is exact at [`src/results_store.py`](../src/results_store.py#L28-L33).
- `ResultsStore.load()` validates that each record’s fields map back to the key at [`src/results_store.py`](../src/results_store.py#L46-L65).
- The record key pattern is consistent with dataset line-number-derived IDs at [`src/experiment_runtime.py`](../src/experiment_runtime.py#L29-L42).

Limitations:
- Key format does not prove the original data snapshot or the plan schedule. It only proves the internal identity of the stored records themselves.
- Without a contemporaneous dataset hash and plan digest, a record key cannot be demonstrated to correspond to the exact original run, only to a dataset and prompt configuration that are consistent with the current project sources.

### 6. Execution history and any contradictions

Status: NOT VERIFIED / CONTRADICTED IN PART.

Direct evidence:
- The runner’s metadata refresh logic compares prompt hashes and settings to detect mismatch before continuing, at [`src/run_experiment.py`](../src/run_experiment.py#L24-L47) and [`src/run_experiment.py`](../src/run_experiment.py#L68-L95).
- The project docs say the official run/evaluation path is blocked without an immutable plan and versioned outputs: [`reports/phase_b2_recovery_protocol_specification.md`](./phase_b2_recovery_protocol_specification.md#L479-L482), [`reports/offline_recovery_evaluation_integration_report.md`](./offline_recovery_evaluation_integration_report.md#L17-L22).

Contradictions / gaps:
- The source code only records prompt hashes and model/config metadata, not a canonical, immutable plan, dataset snapshot, or prior evaluation version.
- There is no persisted execution manifest that binds a historical ResultsStore to the exact dataset file and command-line schedule.
- The project treats the evaluation path as blocked without an authoritative plan, which implies historical results are not automatically eligible for plan-bound scoring.

## Verdict by field

- Dataset snapshot used by historical experiment: NOT VERIFIED.
- Prompt file hashes recorded contemporaneously: PARTIALLY VERIFIED (exists in ResultsStore `meta` and matches source map).
- Model and generation settings recorded and tied to results: PARTIALLY VERIFIED (present in ResultsStore `meta`, but not proof of the original execution manifest).
- Planned split/prompt/run coverage recoverable from contemporaneous evidence: NOT VERIFIED.
- Historical record keys map unambiguously to original dataset and plan: NOT VERIFIED.
- Inconsistencies between reports, configuration, ResultsStore metadata, and execution records: PARTIALLY VERIFIED / CONTRADICTED IN PART. The record metadata is coherent with the current source, but it does not satisfy the policy requirement for an immutable plan and dataset snapshot.

## Can any historical result legitimately support a plan-bound evaluation?

No, not as currently evidenced.

The surviving historical ResultsStore has some provenance, but it is insufficient to prove the exact original dataset snapshot, the approved run schedule, or the immutable plan that the policy requires. A new plan could safely include historical results only after a separately approved plan artifact and dataset-manifest binding are created and verified. Without that, the historical data remain unverified and cannot be safely treated as authoritative inputs.

## Smallest safe next step

Create and approve a single authoritative experiment-plan artifact that records, at minimum:

- the exact dataset snapshot identity (hash or immutable artifact reference);
- the exact prompt file identities and SHA-256 values;
- the model and effective generation settings;
- the split/prompt/run matrix and expected observation keys;
- a plan version and a digest pinned outside the generated results.

Only after this plan is approved and verified should any historical ResultsStore be treated as eligible for plan-bound evaluation. Until then, the safe conclusion is: historical results remain NOT VERIFIED.
