# Phase A.3 Identity Integrity Report

## A. Files changed

- `src/recovery_ledger.py` — makes the canonical path/lock identity read-only,
  retains ledger device/inode identity, and verifies opened descriptors.
- `tests/test_recovery_ledger.py` — adds immutable-path, inode-replacement,
  absent-ledger, and cross-instance regression tests.
- `RECOVERY_LEDGER.md` — documents identity lifecycle and remaining limits.
- `reports/phase_a3_identity_integrity_report.md` — this report.

No experiment output, result, metric, prompt, dataset, runner, or evaluator file
was changed.

## B. Immutable path design

The canonical absolute ledger path and its derived adjacent lock path are
stored in private slotted attributes. Read-only `path` and `lock_path`
properties expose them. Normal assignment or deletion of either public
property is rejected. The canonical private path fields are also protected
against ordinary reassignment or deletion. Internal reads and mutations use
the private canonical fields, so a caller cannot redirect the ledger while
retaining the old lock identity.

## C. Device/inode verification design

If a regular single-link ledger exists at construction, the instance records
its `(st_dev, st_ino)`. Before locking, the path is checked against that known
identity; after the lock is acquired, every opened ledger descriptor is checked
with `fstat()`. A mismatch raises `RecoveryLedgerIdentityError`. An established
ledger that disappears is rejected, not recreated. Existing symlink and
hard-link rejection policies remain in force.

## D. Initially absent ledger lifecycle

An absent-at-construction instance starts with no ledger identity. Reads under
the shared lock return an empty ledger if the file is absent. A first append
holds the exclusive lock and creates the file with `O_CREAT | O_EXCL`; the
opened descriptor’s device/inode becomes that instance’s identity. If another
cooperating instance creates the ledger first, the waiting instance reads it
under the lock, adopts that descriptor identity, and verifies it on later
opens. If a file appears between an absent read and the exclusive create, the
create fails and the instance does not adopt or modify it.

## E. Replacement and TOCTOU handling

`O_NOFOLLOW` rejects final-component symlinks. Directory-relative opens pin
the parent directory for each ledger operation, and `fstat()` verifies the
actual opened file rather than relying only on a path check. An established
identity detects regular-file replacement, deletion/recreation, or a missing
ledger before an operation can append to the replacement.

These are cooperating-process guarantees. An instance created while no ledger
exists cannot distinguish the first valid file it observes under the lock
from an unrelated external file that appeared before that observation. A new
instance also treats the current valid file as its construction-time identity;
identity is not persisted in a separate registry across process restarts.
Arbitrary external replacement of parent directories, ledger or lock files,
or lock inodes while bypassing the protocol remains outside the guarantee.

## F. Regression tests and evidence

The focused tests cover:

- reassignment of `path` and `lock_path` being rejected;
- canonical and parent-symlink aliases sharing lock identity;
- ledger-file symlink resolution, post-construction symlink rejection, and
  hard-link rejection;
- replacing and deleting/recreating an established ledger being rejected;
- failed identity checks leaving the replacement contents unchanged;
- initially absent ledger creation and normal use;
- two instances created while absent sharing the first cooperating instance’s
  created ledger;
- existing recovery-selection, uncertainty, conflict, and multiprocessing
  locking behavior.

## G. Focused test result

Ran only:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_recovery_ledger.py
```

Result: **37 passed**.

## H. Protected-file hashes

| File | Before | After |
|---|---|---|
| `outputs/raw_responses.json` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` | `4e4f2d91fc00f9df81226a48d6735b8c4048aabab1d4f52ffca75903403a1b88` |
| `outputs/retry_log.jsonl` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` | `afc9ab5048babaab6f4021884242804ef85b66413ee848530e0dbb4ef7aaa663` |

Both hashes are unchanged.

## I. Limitations and unresolved risks

The identity binding is per instance and uses device/inode metadata; it is not
a durable, cross-process identity registry. A previously absent instance
adopts the first valid ledger it observes under the lock. A new instance
created after an external replacement cannot infer the prior inode. External
path or lock-file replacement and non-cooperating writers remain outside the
locking guarantee. The subsystem remains limited to cooperating processes on
local macOS/POSIX filesystems honoring `flock`; no absolute durability or
exactly-once provider execution is claimed.

## J. Safety boundaries

- No `.env`, credentials, API keys, or held-out/test data were accessed.
- No runner, evaluator, Groq client, API, or network operation was invoked.
- No experiment outputs, raw responses, retry logs, metrics, results, prompts,
  or datasets were modified.
- No default recovery ledger or lock file was created or modified.
- Tests used temporary directories and synthetic ledger records only.
- No full test suite, Phase B, evaluator integration, or live recovery was
  performed.
