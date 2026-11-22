# Phase C.2 — Terminal Recovery Outcome Event Contract Design

**Status:** Design proposal only. No event type, schema, implementation, or
policy extension is approved by this document.

## Scope and files read

The design is based only on the following authorized files:

- `src/recovery_ledger.py`
- `tests/test_recovery_ledger.py`
- `RECOVERY_LEDGER.md`
- `reports/phase_b2_recovery_protocol_specification.md`
- `reports/phase_c_offline_contract_tests_report.md`
- `reports/phase_c1_test_reconciliation_and_failure_event_audit.md`

No tests or application code were executed. No default ledger, lock file,
experiment output, retry log, credential, dataset, or provider/API was
accessed. This report is the only file created or modified.

## Current contract

**VERIFIED:** The existing event schema has `recovery_started`,
`recovery_completed`, `recovery_adjudicated`, and
`recovery_selected_for_evaluation`; it has no distinct observed-failure event.
`recovery_completed` accepts only an identity-matched result satisfying the
successful-result predicate ([event schema and validation](../src/recovery_ledger.py#L27-L50),
[result validation](../src/recovery_ledger.py#L159-L225),
[success predicate](../src/recovery_ledger.py#L724-L731)).

**VERIFIED:** A started recovery without an accepted completion remains in
`provider_outcome_uncertainty`. Adjudication permits a later attempt but
does not remove the earlier recovery ID from that uncertainty diagnostic
([reducer](../src/recovery_ledger.py#L229-L263),
[resolver](../src/recovery_ledger.py#L748-L759),
[ledger contract](../RECOVERY_LEDGER.md#L81-L92)).

**VERIFIED:** An unsuccessful result passed to `complete_recovery()` fails
event validation and is not appended. The existing start remains incomplete;
the ledger does not durably record that an unsuccessful provider response was
observed ([completion API](../src/recovery_ledger.py#L576-L602),
[append path](../src/recovery_ledger.py#L652-L671),
[Phase C audit](./phase_c1_test_reconciliation_and_failure_event_audit.md#failure-event-behavior)).

## Proposed event

### Event name and purpose

`recovery_response_failed` would mean only:

> An unsuccessful provider response was observed for this recovery attempt.

It would be distinct from an absent response, a timeout, connection failure,
process interruption, or a confirmed local pre-submission failure. It would
not claim that the request was unprocessed, unbilled, or safe to retry.

### Proposed minimal schema

```json
{
  "event_type": "recovery_response_failed",
  "event_version": 1,
  "recovery_id": "<canonical UUID>",
  "observation_key": "<split>|<record_id>|<prompt_id>|<positive run>",
  "timestamp_utc": "<ISO-8601 UTC timestamp>",
  "provenance": {
    "actor": "<safe identifier>",
    "reference": "<safe evidence/decision reference>"
  }
}
```

The event would use an exact field set. It would not embed a ResultsStore
record, raw response/body, headers, credential, provider diagnostic, prompt,
or arbitrary error text. The event type itself records the coarse outcome
class. This is the least-data proposal; adding status codes or other
response-specific fields requires an approved allowlist and sanitization
contract. The current provenance validator checks identifier shape only; it
does not authenticate the actor or dereference the reference
([provenance validation](../src/recovery_ledger.py#L146-L157)).

**USER DECISION REQUIRED:** Approve whether this minimal event is sufficient
or whether a closed set of sanitized response metadata is needed. No
response-metadata allowlist is assumed here.

## Proposed state transitions

The design separates attempt classification from provider-processing and
billing uncertainty. A response-failure event can close the classification of
what was observed for one attempt while leaving the provider outcome
uncertain.

| Prior state | Event | Proposed next state | Rules and uncertainty |
|---|---|---|---|
| No state for ID | `recovery_started` | `started_unresolved` | Start records intent only; it does not prove provider submission. |
| `started_unresolved` | `recovery_response_failed` | `response_observed_unsuccessful` | Accept only for the same ID/key and after start. Mark response observed, but leave processing/billing status unknown. Do not score and do not permit another start yet. |
| `started_unresolved` | `recovery_completed` | `completed_successfully` | Retain current successful-result validation and selection rules. |
| `started_unresolved` | `recovery_adjudicated` | `adjudicated_unresolved` | Preserve uncertainty; a later attempt requires a distinct ID, a new start, and separate explicit authorization. |
| `response_observed_unsuccessful` | `recovery_adjudicated` | `adjudicated_response_failure_unresolved` | Adjudication may authorize a further attempt but does not erase processing/billing uncertainty for the failed-response attempt. |
| `adjudicated_unresolved` or `adjudicated_response_failure_unresolved` | new `recovery_started` with a new ID | New `started_unresolved` plus retained prior uncertainty | Keep all earlier unresolved IDs in diagnostics. Duplicate recovery IDs remain forbidden. |
| Any terminal response state | duplicate failure, completion after failure, or another adjudication for the same attempt | Rejected | At most one response-failure event and one adjudication per recovery ID; no completion may follow a failure event. |

This table proposes that `recovery_response_failed` is terminal for the
attempt's observed-response classification but is not a provider-outcome
resolution. Adjudication remains a separate event and decision. The reducer
must not overload `completed` to represent this event if doing so would remove
the recovery ID from uncertainty diagnostics or permit an unadjudicated retry.

### Event ordering and duplicate rules

Proposed rules:

1. The event requires one prior valid `recovery_started` for the exact
   `recovery_id` and `observation_key`.
2. Its timestamp must not precede the start timestamp; JSONL order remains
   authoritative.
3. There may be at most one `recovery_response_failed` per recovery ID.
4. It cannot follow `recovery_completed` or `recovery_adjudicated`.
5. After it, no `recovery_completed` or second failure event is accepted for
   that ID. A single `recovery_adjudicated` may follow it.
6. Another start for the same observation remains blocked until adjudication.
   The next attempt must use a new recovery ID and have its own start event.
7. A failed-response event is never a selection target. Existing
   `recovery_selected_for_evaluation` remains restricted to a completed,
   successful recovery.

These are proposed reducer invariants, not current behavior.

## Resolver and diagnostic changes

**RECOMMENDED:** Add a separate per-attempt outcome classification to
diagnostics, such as `response_observed_unsuccessful`, rather than treating
this event as `completed` success or as an ordinary unclassified incomplete
start.

**RECOMMENDED:** Keep separate fields for:

- Whether an unsuccessful response was observed.
- Whether a successful result exists.
- Whether provider processing/billing remains uncertain.
- Whether adjudication has authorized a possible next attempt.
- Whether the result is scoreable.

The failure event must never be added to `successful_recovery_ids`, returned
as `scoreable_result`, or accepted by the selection validator. It must not
clear uncertainty from an earlier recovery. A selected successful later
recovery may be scoreable under the approved policy while diagnostics still
show the earlier failed-response and incomplete attempts as unresolved.

The exact diagnostic field names and whether an observed response can resolve
any aspect of “provider outcome” are policy questions. A response being
observed is not evidence that the request was not processed or billed.

## Distinguishing other outcomes

| Observation | Proposed representation |
|---|---|
| Unsuccessful provider response actually observed | `recovery_response_failed`, only if the observation criterion is separately approved and satisfied. |
| Timeout, connection failure, lost response, or process interruption | No response-failure event. Leave the start unresolved; do not infer non-processing or non-billing. |
| Confirmed local failure before provider submission | Not represented by this event. A separate local-abort state would require a reliable request-boundary contract and separate policy approval. |
| Successful, identity-matched response | Existing `recovery_completed`; it remains subject to selection rules. |

No exception name, missing result, process exit, or lack of a response is
sufficient by itself to label an attempt as a known local pre-submission
failure or an observed provider response.

## Compatibility, versioning, and migration

### Readers and writers

The proposed event is additive: a new reader can continue reading old valid
event types without changing their field sets. However, the current reader
uses an exact event-type schema and rejects unknown event types. An old binary
therefore will not read a ledger after a new event is appended. This is
backward-readable by new software, but it is not forward-compatible with old
readers.

**RECOMMENDED:** Do not write the new event until all active ledger readers
have been upgraded to understand it, or use an explicitly versioned/separate
ledger design. A separate file avoids making old readers encounter the new
event but creates ordering, merge, duplicate-ID, and resolver-input problems;
it is not a drop-in compatibility solution.

### Event and ledger versioning

The proposed `event_version: 1` distinguishes the new event's schema and
allows future readers to reject unsupported versions explicitly. It does not
make old readers compatible with the new event type. Existing events have no
version field; adding a global ledger header or adding version fields to old
events would itself require a broader migration and would break strict older
readers.

**USER DECISION REQUIRED:** Decide whether to use an event-local version,
increment a ledger-wide schema version, or maintain a new versioned ledger.
No versioning strategy is approved by this design.

### Existing ledgers

Do not rewrite or silently reinterpret existing entries. The Phase C
implementation rejects legacy `recovery_completed` entries whose results
fail the successful-result predicate; whether such entries exist in the
default ledger is not known because it was not inspected
([Phase C compatibility note](./phase_c_offline_contract_tests_report.md#implementation-correction-and-compatibility)).

Before any event is written, a separately authorized compatibility plan must
decide how to handle such legacy entries. Options include fail-closed manual
review, a separately versioned reader that preserves them as
unclassified/non-scoreable history, or an append-only migration artifact.
Automatic rewriting, deletion, or treating a legacy failure as proof of
non-processing/non-billing is not proposed.

## Test plan (not executed)

If separately authorized, focused offline tests should use temporary ledgers
and synthetic data to verify:

1. Exact schema, version, provenance shape, recovery ID/key matching, and
   timestamp ordering.
2. Rejection without append when no valid start exists, when the key differs,
   or when the failure event is duplicated or follows another terminal
   outcome.
3. A valid failure event is persisted and reloads as an observed unsuccessful
   response, not a successful completion.
4. A failure event alone does not permit a second start; adjudication and a
   new recovery ID/start are required.
5. The failed response remains non-scoreable and non-selectable.
6. Earlier incomplete IDs and the failed attempt's unresolved
   processing/billing uncertainty remain in diagnostics after later attempts.
7. Timeout/connection/interruption fixtures remain ambiguous and cannot be
   encoded as observed responses without the approved observation criterion.
8. Old valid event logs remain readable by the new reader, unknown versions
   fail closed, and old-reader behavior is explicitly tested/documented.
9. Legacy failed-completion handling is tested without modifying any existing
   ledger or output.

No test was run as part of this design task.

## Decisions requiring separate user approval

1. What criterion establishes that a provider response was actually observed?
2. Which sanitized response metadata, if any, may be stored? Should the
   minimal schema omit status codes and provider-specific error categories?
3. Should confirmed local pre-submission failures have a distinct event, and
   what trustworthy request-boundary evidence would support that claim?
4. Does the new event use an event-local version, a ledger-wide schema
   increment, or a separate ledger?
5. What is the compatibility policy for old readers and any legacy failed
   `recovery_completed` entries?
6. How will designated human authority and evidence references be
   authenticated or validated? The current ledger only checks identifier
   syntax.
7. Does an observed unsuccessful response remain uncertain for provider
   processing and billing, and what adjudication gate applies before another
   attempt?

## Non-authorization statement

This document proposes a contract only. It does not authorize implementation,
test execution, migration, ledger creation, or event writing. Phase D, E, and
F remain unauthorized. No failed-response event was implemented or appended.

