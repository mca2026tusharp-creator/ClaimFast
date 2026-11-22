# Phase C — Offline Recovery Contract Tests Report

**Date:** 2026-11-14

## Scope and authorization

Phase C was explicitly authorized for offline contract testing and narrowly
scoped corrections to `src/recovery_ledger.py` when a defect was demonstrated.
All test records used synthetic fixtures and pytest temporary directories.
No provider adapter, runner/evaluator integration, or live recovery was
performed. Phase D, E, and F remain unauthorized.

The approved Phase B.3 policy was used as the contract. In particular,
successful recovery results require `parse_ok is True`, `schema_ok is True`,
dictionary `parsed_json`, `error is None`, and matching observation identity;
selection and provider uncertainty remain separate.

## Files read and modified

**Exact project files read:**

- `src/recovery_ledger.py`
- `tests/test_recovery_ledger.py`
- `RECOVERY_LEDGER.md`
- `reports/phase_b2_recovery_protocol_specification.md`

**Exact files modified:**

- `src/recovery_ledger.py`
- `tests/test_recovery_ledger.py`
- `reports/phase_c_offline_contract_tests_report.md` (this report)

No other project files were modified.

## Contract verification

### A. Identity and validation

| Requirement | Status | Evidence |
|---|---|---|
| Enforce an exact four-part observation identity and positive canonical run number. | **VERIFIED** | `_validate_observation_key()` validates the four components and run syntax ([implementation](../src/recovery_ledger.py#L111-L121)). Parameterized tests reject missing/extra components, empty components, zero, leading zero, and nonnumeric runs ([tests](../tests/test_recovery_ledger.py#L218-L227)). |
| Require a recovery result to match the started observation key. | **VERIFIED** | `_validate_result_record()` compares the derived result key with the expected key ([implementation](../src/recovery_ledger.py#L159-L172)); a mismatched result is rejected without an append ([test](../tests/test_recovery_ledger.py#L229-L238)). |
| Fail closed for malformed records and events. | **VERIFIED** | Exact event/result schemas and field types are validated ([implementation](../src/recovery_ledger.py#L159-L225)). Tests cover invalid event schema, malformed result fields, and malformed/incomplete JSONL ([tests](../tests/test_recovery_ledger.py#L197-L215), [tests](../tests/test_recovery_ledger.py#L408-L431), [tests](../tests/test_recovery_ledger.py#L635-L641)). |
| Reject invalid and unknown event references. | **VERIFIED** | The reducer requires a corresponding start, matching observation key, and valid event order ([implementation](../src/recovery_ledger.py#L229-L310)). Tests cover unknown originals, missing starts, unknown/incomplete selections, and key mismatches ([tests](../tests/test_recovery_ledger.py#L251-L261), [tests](../tests/test_recovery_ledger.py#L463-L481), [tests](../tests/test_recovery_ledger.py#L549-L633)). |
| Exclude missing originals under the current resolver contract without fabricating a ResultsStore record. | **VERIFIED** | `resolve_results()` rejects recovery events whose key is absent from `original_records` ([implementation](../src/recovery_ledger.py#L733-L759)). The test confirms the failure and that the supplied originals remain unchanged and no key is added ([test](../tests/test_recovery_ledger.py#L251-L261)). |

### B. Recovery lifecycle and success

| Requirement | Status | Evidence |
|---|---|---|
| A completion cannot be accepted before a start. | **VERIFIED** | Completion requires a known start in the reducer ([implementation](../src/recovery_ledger.py#L267-L310)); tested by [test_completion_without_start_is_rejected](../tests/test_recovery_ledger.py#L549-L553). |
| A completed recovery must satisfy the approved successful-result predicate. | **DEFECT FOUND — corrected** | Before the correction, the new regression cases showed that completed records with false parse/schema flags, non-dictionary `parsed_json`, or non-null `error` were accepted. `_validate_event()` now rejects these results using `_is_successful_result()` ([implementation](../src/recovery_ledger.py#L199-L225), [predicate](../src/recovery_ledger.py#L724-L731)). The four cases now pass and verify that rejection leaves the start intact and uncertain ([test](../tests/test_recovery_ledger.py#L381-L405)). |
| Enforce `parse_ok`, `schema_ok`, dictionary `parsed_json`, and null `error`. | **VERIFIED** | The successful-result predicate checks all four conditions ([implementation](../src/recovery_ledger.py#L724-L731)); the parameterized completion test exercises each failure independently ([test](../tests/test_recovery_ledger.py#L381-L405)). |
| Keep an incomplete recovery uncertain and block another start until adjudication. | **VERIFIED** | `_reduce_events()` blocks a second active start for the same key ([implementation](../src/recovery_ledger.py#L229-L263)). The test checks the rejected append and then exercises adjudication and a distinct subsequent recovery ([test](../tests/test_recovery_ledger.py#L295-L337)). |
| Adjudication must not erase the earlier uncertainty; a further attempt needs a new recovery identity/start. | **VERIFIED** | Uncertainty is derived from every recovery without a completion, including an adjudicated start ([implementation](../src/recovery_ledger.py#L748-L759)). The lifecycle test verifies the earlier ID remains in diagnostics after the second recovery is selected ([test](../tests/test_recovery_ledger.py#L295-L337)). |

### C. Authorization and adjudication

| Requirement | Status | Evidence |
|---|---|---|
| Require valid actor/reference provenance fields. | **VERIFIED** | Provenance must contain exactly safe, non-empty `actor` and `reference` identifiers ([implementation](../src/recovery_ledger.py#L146-L157)); tests cover empty and malformed values for starts and adjudications ([test](../tests/test_recovery_ledger.py#L434-L460)). |
| Require the adjudication decision to be the supported literal. | **VERIFIED** | Event validation accepts only `authorize_further_attempt` ([implementation](../src/recovery_ledger.py#L199-L225)); an invalid decision is rejected without appending ([test](../tests/test_recovery_ledger.py#L582-L599)). |
| Authenticate that the actor is the designated human authority and that the reference resolves to a recorded approval/evidence item. | **NOT VERIFIED** | The ledger validates actor/reference string shape and stores the supplied values ([implementation](../src/recovery_ledger.py#L146-L157), [adjudication API](../src/recovery_ledger.py#L603-L627)); it has no configured authority identity, authentication hook, or evidence-reference lookup. Phase C did not invent one. |
| Do not infer that the earlier request was unprocessed or unbilled; keep uncertainty visible after adjudication. | **VERIFIED** | The resolver reports uncertainty for incomplete recoveries independently of selection ([implementation](../src/recovery_ledger.py#L748-L759)); the lifecycle test verifies the earlier recovery remains in `provider_outcome_uncertainty` after adjudication and selection ([test](../tests/test_recovery_ledger.py#L295-L337)). No evidence-sufficiency algorithm was added. |

### D. Selection and scoreability

| Requirement | Status | Evidence |
|---|---|---|
| A successful original remains selected. | **VERIFIED** | Resolver prioritizes a successful original ([implementation](../src/recovery_ledger.py#L767-L788)); tested with and without an incomplete recovery ([tests](../tests/test_recovery_ledger.py#L339-L379)). |
| An unsuccessful original plus one successful recovery is not scoreable without explicit selection. | **VERIFIED** | The resolver returns `recovery_selection_required` until a selection exists ([implementation](../src/recovery_ledger.py#L788-L808)); tested in [original-failure exclusion](../tests/test_recovery_ledger.py#L264-L278) and [selection-required case](../tests/test_recovery_ledger.py#L280-L293). |
| Explicit selection makes one successful recovery eligible when other conditions pass. | **VERIFIED** | Selection state and selected recovery are resolved separately ([implementation](../src/recovery_ledger.py#L788-L805)); tested by [test_one_successful_recovery_requires_and_honors_explicit_selection](../tests/test_recovery_ledger.py#L280-L293). |
| Selection does not clear provider uncertainty. | **VERIFIED** | Uncertainty is computed independently of selection ([implementation](../src/recovery_ledger.py#L748-L759)); verified in [test_uncertain_prior_request_and_evaluation_selection_are_independent](../tests/test_recovery_ledger.py#L295-L337). |
| Multiple successful recoveries produce a conflict and no scoreable recovery, even when a selection exists. | **VERIFIED** | The conflict branch precedes selection ([implementation](../src/recovery_ledger.py#L767-L780)); tested by [test_multiple_successes_conflict_even_with_explicit_selection](../tests/test_recovery_ledger.py#L524-L547). |
| A recovery cannot replace a successful original. | **VERIFIED** | The resolver returns the successful original ([implementation](../src/recovery_ledger.py#L781-L788)); tested by [test_successful_original_is_preserved_and_selection_cannot_override_it](../tests/test_recovery_ledger.py#L339-L355). |
| Diagnostics preserve selection, conflict, and uncertainty information. | **VERIFIED** | `RecoveryResolution` carries these separate fields ([implementation](../src/recovery_ledger.py#L79-L96)); covered by the uncertainty, successful-original, and conflict tests cited above. |

### E. Persistence and concurrency

| Requirement | Status | Evidence |
|---|---|---|
| Append/reload and fail-closed malformed input behavior. | **VERIFIED** | JSONL is validated on read and appends use the locked transaction ([implementation](../src/recovery_ledger.py#L498-L548), [implementation](../src/recovery_ledger.py#L652-L719)). Reload is tested in [test_started_completed_and_explicit_selection_survive_reload](../tests/test_recovery_ledger.py#L179-L195); rejected appends preserve prior bytes in the result/provenance/decision tests cited above. |
| Invalid append does not leave a valid-looking partial event. | **VERIFIED** | Validation occurs against the latest ledger before writing ([implementation](../src/recovery_ledger.py#L652-L719)); regression tests compare the ledger bytes before and after rejection ([tests](../tests/test_recovery_ledger.py#L229-L238), [tests](../tests/test_recovery_ledger.py#L381-L460), [tests](../tests/test_recovery_ledger.py#L582-L599)). |
| OS locking and concurrent mutation behavior. | **VERIFIED** | Shared/exclusive `flock` usage is implemented in `_locked()` ([implementation](../src/recovery_ledger.py#L449-L496)). Tests cover shared reads, exclusive lock blocking, public mutation serialization, and competing same-key starts ([tests](../tests/test_recovery_ledger.py#L686-L703), [tests](../tests/test_recovery_ledger.py#L852-L954)). |
| Canonical path identity, replacement protection, symlink/hard-link defenses, deletion/recreation, and absent-ledger initialization. | **VERIFIED** | Construction and descriptor checks are in [implementation](../src/recovery_ledger.py#L358-L448). Existing tests cover canonical spellings, immutable path properties, symlink aliases/substitution, hard links, replacement, deletion/recreation, and absent-ledger creation ([tests](../tests/test_recovery_ledger.py#L705-L850)). |

## Tests added or strengthened

- Added malformed four-part-key and result-identity mismatch cases to verify
  key validation is exact and rejected results are not appended.
- Strengthened unknown-original coverage to verify the caller's original
  mapping is unchanged and no placeholder key is created.
- Strengthened the uncertainty lifecycle test to verify an unadjudicated
  start blocks a second start without changing the ledger.
- Added parameterized completion-predicate and malformed-result cases.
  These exposed the completion defect before the implementation correction.
- Added actor/reference validation and invalid-adjudication-decision cases.
  These verify ledger-level provenance shape and decision validation, not
  human identity authentication.

## Implementation correction and compatibility

`_validate_event()` now rejects a `recovery_completed` event unless the result
passes the approved successful-result predicate. The check runs before append
and also applies when loading/revalidating event history. This was required
because the pre-fix implementation accepted unsuccessful completed results,
then treated them as terminal rather than uncertain.

This tightens compatibility: any pre-existing ledger containing a
`recovery_completed` event with `parse_ok` false, `schema_ok` false,
non-dictionary `parsed_json`, or non-null `error` will now fail validation on
load. The default recovery ledger was not inspected, so whether such events
exist is **NOT VERIFIED**. No migration or event rewrite was performed. A
future need to record a known failed response as terminal requires a separately
reviewed event contract; this change does not invent one.

## Test execution

Baseline before adding regressions:

```text
37 passed in 0.51s
```

With the new completion-predicate regressions and before the code fix, the
four cases failed because invalid results were accepted; the remaining 53
cases passed. After the correction and all test additions, the required
focused command completed successfully:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_recovery_ledger.py
58 passed in 0.50s
```

No unrelated test suite was run.

## Protected hash comparison

Only SHA-256 digests were computed for the protected files; their contents
were not opened or printed.

| File | Previously recorded / before | After | Result |
|---|---|---|---|
| `outputs/raw_responses.json` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` | Match |
| `outputs/retry_log.jsonl` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` | Match |

The default recovery ledger and lock file were not created, modified, or
inspected; all ledger tests used temporary paths.

## Safety, limitations, and next steps

- No credentials, `.env`, datasets, held-out data, experiment-output contents,
  or retry-log contents were accessed. No provider/API calls were made.
- Only `src/recovery_ledger.py` was imported and exercised by the authorized
  focused pytest module. The runner, evaluator, and live experiment were not
  run.
- The ledger cannot authenticate a designated human authority or verify that
  an evidence reference resolves to a genuine approval record. No mechanism
  was added because the approved contract does not define one.
- This phase does not authorize Phase D, E, or F. Passing offline tests is not
  permission for provider recovery or evaluation integration.

**Recommended next step:** Review the failed-completion compatibility impact
and decide, under separate authorization if needed, whether a distinct
terminal failure event is required. Keep any such design separate from live
recovery and evaluation integration.
