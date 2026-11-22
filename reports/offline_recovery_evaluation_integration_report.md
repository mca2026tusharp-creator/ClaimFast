# Consolidated Offline Recovery and Evaluation Integration Report

## Outcome

**VERIFIED:** An offline recovery orchestration boundary now accepts only an
explicit observation-key allowlist, requires an injected approval-verification
callback, appends the recovery start before invoking an injected provider, and
never automatically repeats a provider call. It does not write into the
original `ResultsStore`.

**VERIFIED:** The narrow B2-D15 metrics defect is fixed: an `error`-marked
record is invalid for scoring even if its parse/schema flags and parsed object
otherwise look valid. A diagnostic-only evaluation helper preserves resolver
diagnostics and selection provenance, and flags differing parsed predictions
without changing the original-selection policy.

**BLOCKED:** Full recovery-aware metrics evaluation and versioned evaluation
artifacts were not integrated. The approved protocol requires an immutable,
verifiable authoritative experiment plan, but no approved plan artifact or
integrity-verification mechanism was identified in the inspected source and
Phase B reports. Expected run coverage, evaluation versions, and safe
publication therefore remain unresolved; none was invented here.

## Files inspected

- Source: `src/recovery_ledger.py`, `src/results_store.py`,
  `src/run_experiment.py`, `src/experiment_runtime.py`, `src/evaluate.py`,
  `src/metrics.py`, `src/config.py`, `src/prompt_helpers.py`, and
  `src/response_validation.py`.
- Tests: `tests/test_recovery_ledger.py`, `tests/test_results_store.py`, and
  `tests/test_metrics.py`.
- Documentation: `RECOVERY_LEDGER.md`,
  `reports/phase_b_recovery_integration_design.md`,
  `reports/phase_b1_persistence_evaluation_audit.md`,
  `reports/phase_b2_recovery_protocol_specification.md`,
  `reports/phase_c_offline_contract_tests_report.md`,
  `reports/phase_c1_test_reconciliation_and_failure_event_audit.md`,
  `reports/phase_c2_terminal_failure_event_contract_design.md`,
  `reports/phase_c3_terminal_failure_event_implementation_report.md`, and
  `reports/phase_c5_audit_gap_closure_report.md`.
- The path for the Phase C.4 audit report was checked but no such file was
  present.

No experiment output, dataset, or credential contents were read. The
development dataset and protected files listed below were hashed only.

## Files changed

- `src/recovery_integration.py` — added the injectable, at-most-once recovery
  orchestration boundary.
- `src/recovery_evaluation.py` — added a diagnostic-only snapshot builder.
- `src/metrics.py` — included `error is None` in the usable-result predicate.
- `tests/test_recovery_integration.py` — added synthetic fake-provider and
  temporary-ledger tests.
- `tests/test_recovery_evaluation.py` — added discrepancy and selection
  provenance tests.
- `tests/test_metrics.py` — added an error-marked scoring regression test.
- `RECOVERY_LEDGER.md` — documented the procedural authorization boundary,
  offline integration, and the evaluation-plan blocker.
- `reports/offline_recovery_evaluation_integration_report.md` — this report.

`src/recovery_ledger.py`, `src/evaluate.py`, `src/run_experiment.py`, and
`src/experiment_runtime.py` were not modified.

## Implemented behavior

### Recovery orchestration

`recover_observation()` in `src/recovery_integration.py`:

1. Requires the key to appear in the caller-supplied approved-key collection,
   an existing matching original result, an injected provider, and an
   approval-verification callback.
2. Rejects a successful original.
3. Calls the approval workflow before `start_recovery()`. The callback must
   return exactly `True`; a non-empty provenance string alone is not treated
   as approval.
4. Persists the start through the existing ledger before invoking the provider.
5. Calls the provider once. The dedicated
   `UnsuccessfulProviderResponse` signal and a returned response that fails
   existing parse/schema validation create `recovery_response_failed`.
   Other exceptions propagate without a terminal event, leaving the started
   attempt unresolved and uncertain. No retry loop or retry fallback exists.
6. Completes only a result that satisfies the existing parser/schema checks;
   the ledger also validates its successful-result predicate.
7. Leaves `ResultsStore` unchanged and stores no recovery result there.

The callback is an interface to a responsible workflow, **not** an
authentication mechanism. This code cannot authenticate the human, verify the
approval reference against an external record, or prove that the callback
implements those checks. The authorized operator/workflow must verify the
designated human approval, durable observation-specific reference, and
authoritative plan before invoking this boundary.

### Recovery diagnostics

`build_recovery_diagnostic_snapshot()` in `src/recovery_evaluation.py` uses
`build_scoreable_results()` as its scoring boundary and retains its per-key
diagnostics and scoreable records. It also exposes complete selection events,
including selection provenance, and flags successful original/recovery pairs
whose `parsed_json` differs. The existing resolver keeps the successful
original scoreable and does not select the recovery. This discrepancy check
compares parsed predictions, not raw response text or usage metadata.

This helper does **not** compute metrics, establish expected run coverage,
write evaluation artifacts, or authenticate selection provenance. It is not a
complete evaluator integration.

### Metrics

`src/metrics.py` now treats an error-marked record as unusable for scoring,
consistent with B2-D15. Parse and schema telemetry still reflect their
respective flags; the regression test checks scoreability, not those distinct
telemetry facts.

## Validation

The focused command was:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_recovery_integration.py tests/test_recovery_evaluation.py tests/test_recovery_ledger.py tests/test_metrics.py
```

Final output:

```text
........................................................................ [ 79%]
...................                                                      [100%]
91 passed in 1.36s
```

The new tests exercise approval allowlisting and rejection, start-before-
provider ordering, successful completion, observed unsuccessful responses,
unusable returned output, ambiguous timeout uncertainty/no retry, original
store immutability, successful-original ineligibility, discrepancy reporting,
selection provenance, and error-marked scoring. Existing focused ledger tests
continue to cover selection, conflict, and uncertainty behavior.

No runner or evaluator command was run. No provider, API, or network call was
made.

## Authoritative plan and remaining evaluation blockers

**NOT VERIFIED / BLOCKED:** The Phase B.1 and B.2 documents say that an
authoritative immutable plan and its integrity-verification mechanism have
not been identified or approved. The current evaluator derives run numbers
from stored results and writes fixed `metrics.json` and `results.csv` paths.
Because no approved plan artifact was verified, this task did not modify or
run the evaluator and did not calculate expected run coverage from recovered
results.

Consequently, the following requirements remain unimplemented and unverified:

- Recovery-aware metrics calculation using authoritative plan coverage.
- Versioned evaluation artifacts with source identity and selection
  provenance.
- Atomic publication or failure-safe preservation of a prior evaluation.
- Tests that a recovery cannot expand expected run coverage or that failed
  evaluation output cannot replace a prior version.

The implemented diagnostic helper does flag D10-style parsed-output
discrepancies, but no evaluation artifact publishes those diagnostics yet.
The helper's output must not be used as final evaluation until the authoritative
plan and its verification process are approved. No production or live-recovery
readiness is claimed.

## Protected-file hashes

SHA-256 digests were recorded before implementation and recalculated after
implementation. Each listed digest matched. Only hashes were emitted; file
contents were not displayed or inspected.

| Protected file | Before | After | Result |
|---|---|---|---|
| `data/dev.jsonl` | `414b201b52eeddec6b4c5d7368d9df932bcb1ce0c6c7e6f46895d448c09f38f9` | `414b201b52eeddec6b4c5d7368d9df932bcb1ce0c6c7e6f46895d448c09f38f9` | Match |
| `prompts/prompt_a_zero_shot.md` | `12f5c5dda3ab81dfee81de2fbd58342f4a11cd1bb5e3057c0ca541d0fcb92788` | `12f5c5dda3ab81dfee81de2fbd58342f4a11cd1bb5e3057c0ca541d0fcb92788` | Match |
| `prompts/prompt_b_few_shot_k3.md` | `7d55395cf0647736f5e5f4b4318bce2a99883fb7acb585e9e4039fee8a153506` | `7d55395cf0647736f5e5f4b4318bce2a99883fb7acb585e9e4039fee8a153506` | Match |
| `prompts/prompt_b_few_shot_k5.md` | `0fac7f778eef916b6e717809ea1f79f421609abf36b791ff1f4ebe32f50ab470` | `0fac7f778eef916b6e717809ea1f79f421609abf36b791ff1f4ebe32f50ab470` | Match |
| `prompts/prompt_b_few_shot_k8.md` | `54238bfaf399c91e99b001c5e6d45a3ea609c07c1e790f68042f3413df197fd5` | `54238bfaf399c91e99b001c5e6d45a3ea609c07c1e790f68042f3413df197fd5` | Match |
| `prompts/prompt_c_reasoning.md` | `161ea901d55b84ff761f01b83199341bd2db91d9422a1238c857a9d0e33c0b8d` | `161ea901d55b84ff761f01b83199341bd2db91d9422a1238c857a9d0e33c0b8d` | Match |
| `outputs/raw_responses.json` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` | Match |
| `outputs/retry_log.jsonl` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` | Match |
| `outputs/metrics.json` | `c3dfe6fc77a56bc5a3fcefb5bc6042e0006ef2648b6c21a52deccf4001a1780e` | `c3dfe6fc77a56bc5a3fcefb5bc6042e0006ef2648b6c21a52deccf4001a1780e` | Match |
| `outputs/results.csv` | `c33c10d28ed32e6f71c45a0b3f117508e9ed2c490bfdd7ca6277c9e2a7adcf7e` | `c33c10d28ed32e6f71c45a0b3f117508e9ed2c490bfdd7ca6277c9e2a7adcf7e` | Match |

`outputs/experiments/groq_gpt-oss-120b/attempts.jsonl` was absent at the
requested path during baseline hashing; no hash was available for that path.
It was not created or modified.

## Safety and verification boundaries

- **VERIFIED:** Tests use synthetic records and temporary result/ledger paths.
- **VERIFIED:** No live provider, API, network, runner, or evaluator against
  project data was used.
- **VERIFIED:** No `.env`, credentials, held-out data, staged held-out batch,
  protected output contents, default recovery ledger, or experiment log
  contents were inspected.
- **NOT VERIFIED:** Any external human-approval workflow, evidence-reference
  authentication, or authoritative-plan verification.
- **INFERRED:** The offline fake-provider tests validate orchestration order
  and ledger state only; they do not establish remote-provider behavior or
  production safety.
