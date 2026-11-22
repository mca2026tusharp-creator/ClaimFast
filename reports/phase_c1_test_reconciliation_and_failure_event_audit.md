# Phase C.1 — Read-Only Test Reconciliation and Failure-Event Design Audit

**Date:** 2026-11-14

## Scope and files read

This was a read-only audit except for creating this requested report. No tests
or application code were executed, and no event type or implementation was
changed.

**Exact files read:**

- `reports/phase_c_offline_contract_tests_report.md`
- `src/recovery_ledger.py`
- `tests/test_recovery_ledger.py`
- `reports/phase_b2_recovery_protocol_specification.md`
- `RECOVERY_LEDGER.md`

**File created:** `reports/phase_c1_test_reconciliation_and_failure_event_audit.md`.
No other files were modified.

## Audit A — Test-count reconciliation

### Findings

| Count requested | Finding |
|---|---|
| Before Phase C regression additions | **VERIFIED (reported):** The Phase C report states `37 passed in 0.51s`. The raw output is not included in that report, so the historical run is not independently reproducible from the permitted evidence. |
| After additions, before the implementation fix | **VERIFIED (report arithmetic):** The report states four failed cases and 53 passed cases, which totals **57**, not 58. The raw pre-fix output and collected-test listing are not included. Thus the reported arithmetic is exact, but the historical collection is **NOT VERIFIED** independently. |
| Final Phase C run | **VERIFIED (reported):** The report states `58 passed in 0.50s`. The current test definitions are consistent with 58 cases: 42 test functions, of which four are parameterized with 6, 4, 4, and 6 cases; the other 38 functions contribute one case each. |
| Failed and passed before the fix | **VERIFIED (reported):** Four failed and 53 passed. Their sum is 57. |
| What accounts for the one-case difference | **NOT VERIFIED:** The report lists an invalid-adjudication-decision test among the additions, and that test is present in the current test file, but the report does not establish when it was added relative to the pre-fix run. It does not provide a pre-fix test list. No specific test is attributed to the discrepancy here. |

The report’s pre-fix figures therefore cannot describe a 58-case run: they
describe 57 cases arithmetically, while the final run and current definitions
describe 58. The permitted evidence does not establish whether one test was
added, removed, or parameterized differently between those runs. Resolving
that historical cause would require the original pre-fix collection/output,
which is unavailable in the authorized materials. No historical output was
reconstructed from the current test file.

## Audit B — Current terminal-failure behavior

### Current event model

**VERIFIED:** The exact event schema defines `recovery_started`,
`recovery_completed`, `recovery_adjudicated`, and
`recovery_selected_for_evaluation`. There is no distinct terminal failed-
recovery event ([schema](../src/recovery_ledger.py#L27-L50);
[ledger contract](../RECOVERY_LEDGER.md#L7-L26)).

**VERIFIED:** `recovery_completed` is accepted only when its result passes
`_validate_result_record()` and `_is_successful_result()`: true parse and
schema flags, dictionary `parsed_json`, null `error`, and identity matching
the event key ([event validation](../src/recovery_ledger.py#L199-L225);
[result validation and success predicate](../src/recovery_ledger.py#L159-L198),
[predicate](../src/recovery_ledger.py#L724-L731)).

### State behavior

1. **Started, no completion:** The reducer retains the start with no
   `completed` event. A second start for that observation is rejected while
   the start is unadjudicated. In resolver diagnostics, any state with no
   completion appears in `provider_outcome_uncertainty`; adjudication does not
   change that predicate ([reducer](../src/recovery_ledger.py#L229-L263),
   [resolver](../src/recovery_ledger.py#L748-L759)).

2. **Provider response received, but unsuccessful:** Calling
   `complete_recovery()` builds a completion event and passes it through the
   locked append path. Validation rejects a result failing the successful-
   result predicate before the event is written. The prior start therefore
   remains incomplete and appears as uncertain in resolver diagnostics
   ([completion API](../src/recovery_ledger.py#L576-L602),
   [append validation](../src/recovery_ledger.py#L652-L671),
   [event validation](../src/recovery_ledger.py#L199-L225)). The ledger does
   not persist that the client observed an unsuccessful response.

3. **Known local failure before submission:** If a `recovery_started` event
   has already been appended, the current schema has no event that records a
   confirmed local stop before entering the provider call. Such a start also
   remains incomplete and is reported as uncertain. If failure occurs before
   the start append, the ledger has no event describing it. The schema cannot
   distinguish these cases ([event schema](../src/recovery_ledger.py#L27-L50),
   [uncertainty calculation](../src/recovery_ledger.py#L748-L759)).

4. **Successful response:** A successful, identity-matched result may be
   recorded as `recovery_completed`. The resolver includes it in
   `successful_recovery_ids`; scoring still depends on the original-result and
   explicit-selection rules ([completion validation](../src/recovery_ledger.py#L199-L225),
   [resolver](../src/recovery_ledger.py#L760-L829),
   [selection contract](../RECOVERY_LEDGER.md#L94-L120)).

### Distinguishability and uncertainty

| State | Distinguishable in the current ledger? | Reason |
|---|---|---|
| Ambiguous request outcome | **VERIFIED — represented as incomplete/uncertain** | A start without an accepted completion remains in `provider_outcome_uncertainty`. |
| Known provider response that is unsuccessful | **NOT VERIFIED — not durably distinguishable from incomplete** | The completion event is rejected and no alternative response-failure event exists. |
| Known local failure before provider submission | **NOT VERIFIED — not durably distinguishable after a start** | The event schema has no pre-submission terminal state; a start alone records intent, not submission or non-submission. |
| Successful response | **VERIFIED — represented by a valid completion** | The accepted completion contains a successful, identity-matched result; selection is separately represented. |

The current uncertainty diagnostic tracks starts without a successful
completion, not billing as a separate dimension. Adjudication authorizes a
possible further attempt but leaves the earlier incomplete start in the
uncertainty tuple. Neither a timeout, connection error, interruption, missing
completion, nor an unsuccessful response establishes that the provider did
not process or bill the request ([approved policy](./phase_b2_recovery_protocol_specification.md#approved-success-and-scoreability-definitions),
[ledger uncertainty contract](../RECOVERY_LEDGER.md#event-order-and-uncertainty)).

## Compatibility and change surface

**VERIFIED:** A terminal failure event would require coordinated changes to:

- The exact `EVENT_FIELDS` schema and event validation.
- `_reduce_events()` so the new event has explicit ordering, identity, terminal,
  and subsequent-start semantics.
- `RecoveryResolution` and `resolve_results()` so an unsuccessful response is
  diagnosed without adding it to `successful_recovery_ids` or
  `scoreable_result`.
- The uncertainty diagnostic, which currently treats no accepted completion
  as uncertain.
- Ledger methods, tests, and `RECOVERY_LEDGER.md`.
- Compatibility handling. The current validator rejects unknown event types.
  Additionally, the Phase C correction rejects previously persisted
  `recovery_completed` events with unsuccessful results; whether the default
  ledger contains such entries was not inspected and remains **NOT VERIFIED**
  ([Phase C compatibility note](./phase_c_offline_contract_tests_report.md#implementation-correction-and-compatibility)).

Treating a failed response event as equivalent to successful completion would
be unsafe: it would make the attempt appear resolved in the existing
uncertainty calculation. Treating it as a nonterminal event would preserve
uncertainty but would need explicit reducer rules for whether another start is
blocked and how adjudication applies.

## Recommended design options and safeguards

These are design options only; none is implemented or authorized.

### Option 1 — Preserve current fail-closed behavior

Continue rejecting unsuccessful `recovery_completed` events. The start remains
uncertain, which is conservative, but the ledger loses the fact that an
unsuccessful provider response was observed. This may require adjudication for
an outcome that the caller locally knows was a response, and it does not
provide a durable distinction from a crash or pre-submit stop.

### Option 2 — Add a distinct observed-response-failure event

Consider an append-only event whose meaning is narrowly “an unsuccessful
provider response was observed,” with sanitized, validated outcome metadata
and exact recovery/observation identity. It must not mean that the provider
did not process or bill the request. Model attempt terminality separately
from provider-processing/billing uncertainty; a response-failure event must
not be counted as a successful recovery, selected for scoring, or used to
clear uncertainty from an earlier recovery.

If known pre-submission local failures also need representation, define that
as a distinct state rather than inferring it from an exception name or absent
completion. The evidence needed to establish that state and its effect on
retry authorization require policy decisions; this audit does not define an
evidence-sufficiency algorithm.

### Safeguards required for any future event

- Keep failed results out of `successful_recovery_ids`,
  `scoreable_result`, and the selection-eligible set.
- Preserve the failure outcome and any unresolved provider uncertainty as
  separate diagnostics.
- Keep earlier uncertain recovery IDs visible after a later failed attempt.
- Do not let a terminal event alone authorize another attempt; retain the
  approved human authorization/adjudication gates.
- Validate event key, recovery ID, causal ordering, provenance shape, and
  sanitized data before append; retain append-only history.
- Specify old-reader/new-reader behavior and migration/version policy before
  writing the new event type to any ledger.

## Questions requiring separate policy decisions

1. Should the ledger durably represent an observed but unsuccessful provider
   response, or should it continue to leave that recovery incomplete?
2. What exact evidence permits classifying an outcome as a provider response
   versus an ambiguous outcome or a known pre-submission local stop?
3. Should an observed unsuccessful response remain provider-processing and
   billing uncertain, and what event state blocks or permits a later attempt?
4. What sanitized fields may be persisted for a failed response, and how
   should those fields remain distinct from a successful result?
5. How should a new event type be versioned for older readers and any existing
   ledgers, especially given the current rejection of unsuccessful
   `recovery_completed` records?
6. What authenticated mechanism, if any, establishes the designated human
   authority and validates evidence references?

## Execution and preservation statement

- No tests, application code, runner, evaluator, or experiment were executed.
- No provider/API/network requests were made.
- No `.env`, credentials, datasets, held-out data, experiment outputs, or
  retry-log contents were accessed.
- No default recovery ledger or lock file was created or accessed.
- This was read-only except for creating this report. No files other than
  `reports/phase_c1_test_reconciliation_and_failure_event_audit.md` were
  modified.
- Phase D, E, and F were not begun or authorized.
