# ClaimFast Phase A.1 Recovery Safety Report

## A. Files changed and purpose

- `src/recovery_ledger.py` — added persistent OS-level file locking, explicit
  evaluation-selection events, separate provider-uncertainty diagnostics, and
  a scoreable-results builder.
- `tests/test_recovery_ledger.py` — expanded synthetic tests for event
  validation, selection policy, locking, and multiprocessing races.
- `RECOVERY_LEDGER.md` — documented locking, event ordering, selection,
  uncertainty, scoreable output, and limitations.
- `reports/phase_a1_recovery_safety_report.md` — this verification report.

No experiment runner, evaluator, metrics, dataset, prompt, or existing result
record was modified.

## B. Locking design and concurrency evidence

`RecoveryLedger` uses the persistent adjacent
`recovery_events.jsonl.lock` file and macOS/POSIX `fcntl.flock`. Readers take
shared locks. Every mutation takes an exclusive lock, reloads and validates the
latest JSONL history, validates the proposed event, appends, flushes, and
`fsync`s before releasing the lock. Lock acquisition/release failures raise;
there is no unlocked fallback. The lock file is not removed.

Concurrency evidence is from the focused test module:

- `test_load_events_requests_a_shared_os_lock` verifies that readers request a
  shared OS lock and that the lock file persists.
- `test_exclusive_lock_blocks_other_process_lock_acquisition` verifies
  cross-process lock exclusion.
- `test_two_processes_cannot_start_same_uncertain_observation` races two
  processes; exactly one start is accepted, and the final ledger validates
  with one non-contradictory start event.

## C. Evaluation-selection event schema and lifecycle

`recovery_selected_for_evaluation` contains exactly:
`event_type`, unique UUID `selection_id`, `observation_key`, exact
`recovery_id`, UTC `timestamp_utc`, and `provenance` (`actor`, `reference`).
The event must follow a completed, usable recovery for the same key, and its
timestamp cannot predate completion. Duplicate or conflicting selections for
one observation are rejected.

## D. Provider uncertainty versus evaluation selection

The resolver reports `provider_outcome_uncertainty` as the IDs of started
recoveries without completion; adjudication does not remove them. Evaluation
status and `scoreable_result` are separate fields.

For R1-start, R1-adjudicated, R2-start, R2-success: R1 remains in provider
uncertainty. R2 is not scoreable until an explicit selection event identifies
R2. After selection, R2 may be scoreable while R1 remains uncertain. This
records no claim about whether R1 was processed or billed.

## E. Scoreable-results interface and conflicts

`build_scoreable_results(original_records, events)` returns scoreable records
and per-key diagnostics. It includes a successful original unless a
multiple-success recovery conflict exists. It includes a recovery only when an
explicit valid selection exists and there is no conflict. Unselected,
incomplete, unusable, and conflict results are excluded.

Two or more successful recoveries always produce
`conflict_requires_review` and no scoreable result, even if a selection event
exists or the original was successful. The previous successful-original
behavior is preserved except in this explicitly required conflict case.
Selection events never replace a successful original.

## F. Test results

Command:

`PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_recovery_ledger.py`

Result: **26 passed**.

New/expanded coverage includes shared-reader and exclusive-writer lock
behavior, a two-process state-transition race, explicit selection and
selection timestamp validation, uncertainty remaining separate from selection,
successful originals with incomplete recoveries, success flags with a
non-null error, duplicate/contradictory selections, multiple-success conflicts
with no scoreable output, malformed lifecycle transitions, terminal-key
mismatch, and non-monotonic timestamps across separate recoveries.

## G. Protected-file hashes

| File | Before | After | Comparison |
|---|---|---|---|
| `outputs/raw_responses.json` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` | Equal |
| `outputs/retry_log.jsonl` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` | Equal |

Both `outputs/recovery_events.jsonl` and its adjacent lock file were absent
before and after verification; tests used temporary directories.

## H. Limitations and unresolved risks

- `flock` is advisory and protects cooperating processes on filesystems that
  honor it. The implementation targets local macOS/POSIX filesystems; remote
  or distributed filesystem semantics are not reliably auto-detected.
- `fsync` improves durability but cannot guarantee persistence against every
  filesystem, device, or power failure. A partial final line is rejected and
  never repaired automatically.
- If an append reports an error after writing, its persistence may be
  uncertain; reload before taking further action.
- The ledger cannot determine exactly-once provider execution or whether a
  request was processed or billed.
- The known retry-log ambiguity remains unresolved. Live recovery and
  evaluator integration remain out of scope.

## I. Safety-boundary confirmation

No `.env`, credentials, held-out data, or test-set copies were accessed. No
runner, evaluator, Groq client, API, or network operation was invoked. No
experiment outputs, datasets, prompts, metrics, raw responses, or retry events
were modified. The default recovery ledger was not created or modified.
