# Phase C.3 — Terminal Recovery Outcome Implementation Report

**Date:** 2026-11-14

## Scope and authorization

Implemented the approved offline `recovery_response_failed` event contract.
The event records an unsuccessful provider response only when an explicit
caller invokes the ledger method; no runner/provider integration or automatic
classification was added. This report does not authorize live recovery or
Phases D, E, or F.

## Files read and modified

**Exact files read:**

- `reports/phase_c2_terminal_failure_event_contract_design.md`
- `reports/phase_b2_recovery_protocol_specification.md`
- `reports/phase_c_offline_contract_tests_report.md`
- `reports/phase_c1_test_reconciliation_and_failure_event_audit.md`
- `src/recovery_ledger.py`
- `tests/test_recovery_ledger.py`
- `RECOVERY_LEDGER.md`

A targeted workspace search for ledger paths and API symbols found no
additional active implementation reader/writer outside
`src/recovery_ledger.py`; the only other code match was the authorized test
module. Other matches were documentation. This search does not establish
compatibility for external copies or consumers.

**Exact files modified:**

- `src/recovery_ledger.py`
- `tests/test_recovery_ledger.py`
- `RECOVERY_LEDGER.md`
- `reports/phase_c3_terminal_failure_event_implementation_report.md` (this
  report)

No other file was intentionally modified.

## Implementation

The new event has an exact field set and no response-specific payload:

```json
{
  "event_type": "recovery_response_failed",
  "event_version": 1,
  "recovery_id": "<canonical lowercase UUID>",
  "observation_key": "<split>|<record_id>|<prompt_id>|<positive run>",
  "timestamp_utc": "<UTC timestamp>",
  "provenance": {
    "actor": "<validated identifier>",
    "reference": "<validated identifier>"
  }
}
```

`event_version` must be exactly integer `1` (not a boolean). Validation
rejects extra/missing fields, malformed provenance, invalid identities, an
unknown recovery ID, a key mismatch, timestamps before the start, duplicate
failure events, and failure events after completion or adjudication. The
locked append path validates before writing. A completion after a failure is
rejected. One adjudication may follow a failure; another recovery remains
blocked until adjudication and must use a new recovery ID and start event.

The failure event does not set successful completion, enter
`successful_recovery_ids`, or qualify for selection or scoring. It preserves
provider-processing/billing uncertainty, including after adjudication and
later attempts. It is not inferred from timeout, connection failure,
interruption, missing results, or generic exceptions. No local
pre-submission-failure event was added. Existing event schemas were unchanged.

Per-recovery diagnostics now distinguish `unresolved_start`,
`response_observed_unsuccessful`, and `successful_completion`, and expose
adjudication and provider-processing/billing uncertainty. Observation-level
diagnostics retain successful recovery IDs, conflicts, evaluation selection
status, and an explicit scoreability flag. Existing selection and conflict
semantics are unchanged.

## Tests

Added focused temporary-ledger tests for exact schema/version and persistence
across reload; malformed version/provenance/extra fields; unknown recovery and
key mismatch; timestamps; duplicate and out-of-order outcomes; blocked
completion and selection; adjudication followed by a distinct attempt; and
uncertainty/scoreability diagnostics. A successful original remains selected
when a failed-response recovery exists. The existing multiple-success
conflict, locking, path-identity, and replacement-protection tests remain in
the focused module.

**Exact command:**

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_recovery_ledger.py
```

**Actual output:**

```text
........................................................................ [100%]
72 passed in 0.52s
```

No test failure remained. The editor diagnostics check reported no errors in
`src/recovery_ledger.py` or `tests/test_recovery_ledger.py`.

## Compatibility and limitations

The targeted workspace search found the ledger implementation to be the only
active in-workspace reader/writer. The updated reader accepts the new event.
An older reader using the prior exact `EVENT_FIELDS` schema will reject the
unknown event type and fail closed. Do not append the new event until all
active project readers are upgraded. The event-local version does not make an
old reader compatible.

Existing event schemas and legacy ledger entries were not migrated, rewritten,
or silently reinterpreted. The default ledger was not accessed, so its legacy
contents remain unverified. The ledger validates provenance identifier shape
but does not authenticate the actor, verify the referenced evidence, or
independently prove that a provider response was received. Calling
`record_unsuccessful_response()` is an explicit assertion by its caller; the
method does not infer a response from an exception.

No independent audit was performed. No default ledger or lock file was
created, read, or modified.

## Protected output hashes

Only SHA-256 hashes were computed before and after the implementation; file
contents were not read or printed.

| File | Before | After | Comparison |
|---|---|---|---|
| `outputs/raw_responses.json` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` | Match |
| `outputs/retry_log.jsonl` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` | Match |

## Safety and stop condition

No live provider calls, network/API operations, runner, evaluator, held-out
data, credentials, or experiment-output/retry-log contents were accessed.
The protected files were only hashed. Tests used temporary directories and
synthetic fixtures. No further phase or integration work was started.
