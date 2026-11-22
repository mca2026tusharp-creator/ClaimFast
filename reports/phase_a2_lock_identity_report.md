# Phase A.2 Lock Identity Report

## A. Files changed

- `src/recovery_ledger.py` — canonicalizes ledger paths, derives the lock path
  from that identity, and safely opens the ledger without following a
  substituted final-component symlink.
- `tests/test_recovery_ledger.py` — adds path-alias and hard-link cases and
  deterministically exercises the public mutation lock.
- `RECOVERY_LEDGER.md` — documents canonical identity, symlink handling, and
  the remaining locking limitations.
- `reports/phase_a2_lock_identity_report.md` — this verification report.

No experiment, results, metrics, prompt, dataset, or runner/evaluator files
were changed.

## B. Canonical path and lock identity

`RecoveryLedger` resolves the input with `Path.resolve(strict=False)` during
construction. The resulting absolute path is used for subsequent ledger
access. The adjacent persistent lock path is derived only from that canonical
path by appending `.lock` to its filename. Path-resolution errors fail clearly.

Ledger reads and appends use no-follow, directory-relative opens, verify a
regular file, and reject a ledger with multiple hard links. The lock continues
to use `fcntl.flock`; no unlocked fallback was added.

## C. Symlink behavior and test evidence

Existing parent-directory and ledger-file symlinks resolve to their targets
when the instance is constructed. Tests verify that direct and symlink
spellings share the same canonical ledger and lock paths, and that mutation
through a ledger-file symlink is visible through the direct target path.
Tests also verify that a final-component symlink substituted after
construction is rejected, and that hard-linked ledger files are rejected
rather than assigned different lock identities.

These temporary-directory tests ran on the current macOS environment and
passed. Symlink or hard-link tests skip only if the underlying filesystem
cannot create the relevant link.

## D. Deterministic public-mutation locking test

Two spawned processes use the same temporary ledger. Process A acquires the
exclusive transaction lock and holds it behind an event. Process B signals
immediately before invoking public `start_recovery`. In B only, a test wrapper
around the real `flock` first attempts a nonblocking exclusive acquisition:
while A holds the lock, the kernel rejects that probe. B then calls the real
blocking lock operation. The test instruments the transaction read boundary
and verifies the order: lock contention while A holds the lock, release of A,
B's lock acquisition, transaction read, and successful mutation.

Because the transaction-read event is expected only after the lock probe,
the test fails if the public mutation bypasses the exclusive lock, even if
`_locked()` itself remains functional. Afterward the ledger reloads and
validates with exactly one expected `recovery_started` event. Instrumentation
is test-local; no production bypass or test hook was added.

## E. Existing multiprocessing race test

The incompatible-start race test remains in place. Its final run passed; it
validated the ledger and found exactly one start event, so both contradictory
starts were not committed.

## F. Focused test result

Ran only:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_recovery_ledger.py
```

Final result: **32 passed**.

During implementation, an initial run exposed a `FileNotFoundError` from
opening the lock file relative to a directory descriptor in the multiprocessing
race. The lock open was changed to use the canonical absolute lock path with
`O_NOFOLLOW`; the focused suite then passed. No full test suite was run.

## G. Protected-file hashes

| File | Before | After |
|---|---|---|
| `outputs/raw_responses.json` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` |
| `outputs/retry_log.jsonl` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` |

Both before/after hashes are identical.

## H. Limitations

The lock is advisory and protects cooperating processes that use this module
on a local macOS/POSIX filesystem honoring `flock`. Network or distributed
filesystems are not supported or reliably auto-detected. The implementation
does not protect against arbitrary external processes replacing parent
directories or files, unlinking/recreating the persistent lock file, or
bypassing the lock protocol. No exactly-once provider execution or absolute
power-failure durability is claimed.

## I. Safety boundaries

- No `.env`, credentials, API keys, or held-out/test data were accessed.
- No runner, evaluator, Groq client, API, or network operation was invoked.
- No experiment outputs, datasets, prompts, metrics, results, or retry history
  were modified.
- No recovery event was created in the default ledger. Both
  `outputs/recovery_events.jsonl` and `outputs/recovery_events.jsonl.lock`
  remain absent.
- Tests used only temporary synthetic ledgers.
