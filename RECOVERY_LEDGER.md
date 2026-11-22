# Phase A Recovery Ledger

This subsystem is offline only. It does not initialize a Groq client, execute
recovery requests, or integrate with the experiment runner or evaluator.

## Event ledger and schema

The append-only ledger is `outputs/recovery_events.jsonl`. Each JSONL event has
an exact schema:

- `recovery_started`: `event_type`, `recovery_id`, `observation_key`,
  `timestamp_utc`, and `provenance`
- `recovery_completed`: the start fields plus `result`, a result record with
  the existing ResultsStore fields and exactly the same observation identity
- `recovery_adjudicated`: the start fields plus
  `decision: "authorize_further_attempt"`
- `recovery_response_failed`: exactly `event_type`, `event_version` (integer
  `1`), `recovery_id`, `observation_key`, `timestamp_utc`, and `provenance`
- `recovery_selected_for_evaluation`: `event_type`, unique `selection_id`,
  `observation_key`, exact `recovery_id`, `timestamp_utc`, and `provenance`

`provenance` is exactly `{"actor": "...", "reference": "..."}`. Values are
short identifiers, not credentials, prompts, response text, or diagnostic
dumps. Timestamps must be ISO-8601 UTC timestamps. A recovery result is usable
only when parsing and schema flags are true, `parsed_json` is an object, and
`error` is null.

## Authorization boundary

The ledger validates provenance shape; it does not authenticate a human or
verify that a reference identifies an approval. A syntactically valid
`actor`/`reference` pair is not sufficient authorization to initiate a
recovery. Before calling `RecoveryLedger.start_recovery()`, the authorized
operator or workflow must verify the designated human's approval and the
durable approval reference for the specific observation. This is a procedural
requirement, not cryptographic authentication. Neither the current ledger nor
the runner enforces this approval boundary.

`recovery_response_failed` records only that an unsuccessful provider response
was affirmatively observed for the identified recovery. It requires a preceding
valid start with the same recovery ID and exact four-part observation key
(`split|record_id|prompt_id|run`); JSONL order is authoritative, and its
timestamp cannot precede the start. It is rejected if duplicated or if it
follows completion or adjudication. Completion cannot follow this event;
adjudication may follow it once. It stores no status code, response body,
headers, credentials, provider diagnostics, or arbitrary error text. Its
`event_version` is local to this event type; existing event schemas are
unchanged.

This event is not a successful completion and cannot be selected or scored.
It preserves provider-processing and billing uncertainty. It blocks another
start until the recovery is adjudicated under the existing human-review
policy; adjudication permits a possible further attempt but does not erase
uncertainty. A subsequent attempt needs a new recovery ID and its own start.
The event must not be used for timeouts, connection failures, interruptions,
missing results, or generic exceptions. No local pre-submission failure event
is defined.

## Locking, append, and durability

`RecoveryLedger` resolves its input to a canonical absolute path at
construction, following existing parent-directory and ledger-file symlinks.
Resolution failures, including symlink loops, fail clearly. The persistent
adjacent lock path is derived only from that canonical ledger path, so relative
paths, `..` spellings, and symlink aliases use the same lock file. A ledger
file symlink is therefore accessed through its resolved target. Final-component
symlink substitution is rejected when opening the ledger or lock file, and
hard-linked ledger files are rejected because their path identity is
ambiguous. The canonical ledger and lock paths are stored privately and
exposed through read-only `path` and `lock_path` properties; normal assignment
cannot change the target or split it from its lock identity.

For a ledger present at construction, the instance records its device and
inode. Every read or append verifies the opened descriptor with `fstat()` and
rejects a mismatch. A known ledger that disappears is rejected rather than
silently recreated. If the ledger is absent at construction, its identity is
initially unset. A first append holds the exclusive lock and creates the file
with exclusive creation; its descriptor's device/inode becomes the instance
identity. If another cooperating instance creates the ledger first, a waiting
instance opens it under the shared or exclusive lock, records that descriptor
identity, and verifies it on subsequent opens. If a path appears between an
absent read and exclusive creation, exclusive creation fails rather than
adopting that file.

On local macOS/POSIX filesystems, readers use shared `fcntl.flock` locks.
Mutations hold an exclusive OS-level lock for the entire read-latest, validate,
append, flush, and file-`fsync` transaction. A newly created ledger also
triggers a parent-directory `fsync`. Required no-follow and
directory-relative open operations must be available; otherwise access fails
instead of falling back to weaker behavior. Lock acquisition or release
failures raise an error; there is no unlocked fallback. The lock file is never
removed.

The lock is advisory: every writer must use this module. The target is a local
macOS/POSIX filesystem that honors `flock`; network or distributed filesystems
are unsupported and are not reliably auto-detected. Keep the ledger on local
storage. Descriptor checks detect ordinary ledger replacement once an instance
has an established identity. They do not protect against arbitrary
non-cooperating processes that replace parent directories or ledger/lock files,
unlink and recreate the persistent lock file, or bypass the locking protocol.
An instance constructed while the ledger is absent can identify the first
ledger it observes under the lock as the cooperating instance's creation; it
cannot distinguish an unrelated external file that appeared before its first
observation. Identity is retained by a `RecoveryLedger` instance, not in a
separate durable identity registry; a new instance treats the current valid
single-link file as its construction-time identity. Do not manually replace or
remove the lock file. `fsync` improves durability but cannot guarantee
persistence against every filesystem, device, or power failure. A crash during
append may leave a partial line; reload rejects incomplete or malformed lines
and does not repair or truncate them. If an append reports an error after
writing, reload the ledger before deciding what happened; do not blindly repeat
the event.

## Event order and uncertainty

JSONL line order is authoritative for event sequencing. Timestamps record event
times; they are not sequence numbers and need not be globally monotonic.
Timestamps must be UTC-aware and satisfy causal constraints: completion,
adjudication, or an observed unsuccessful response cannot predate its start,
and an evaluation selection cannot predate recovery completion.

An incomplete `recovery_started` event remains provider-outcome-unknown even
after `recovery_adjudicated` authorizes another attempt. An observed
unsuccessful response is also provider-processing/billing-uncertain and
requires adjudication before another start. Adjudication never establishes
whether the earlier request was processed or billed. Diagnostics retain
attempt classifications (`unresolved_start`,
`response_observed_unsuccessful`, or `successful_completion`), adjudication
status, and provider-processing/billing uncertainty separately from evaluation
selection and scoreability.

The event is additive for readers updated to understand it. Older readers
that reject unknown event types fail closed when they encounter it; therefore
the event must not be written until all active project readers are compatible.
No existing event is rewritten or silently reinterpreted. The event-local
version does not make old readers compatible.

## Evaluation selection and conflicts

A recovery is never selected merely because it is the only successful one.
`recovery_selected_for_evaluation` must identify a started, completed, usable
recovery for the same observation, include a unique selection ID and
provenance, and follow its completion in ledger order. Only one selection
decision per observation is accepted. A selection does not clear provider
uncertainty. A selection for a recovery cannot replace a successful original.

Two or more successful recoveries create `conflict_requires_review`. No
scoreable result is returned for that observation, even if an explicit
selection exists or the original was successful. This prevents a selection
decision from silently resolving competing successful versions. A separate
review process is not implemented in this phase.

## Scoreable-results interface

`build_scoreable_results(original_records, events)` is the single supported
interface for producing scoreable recovery-aware results. It returns:

- `records`: successful originals, or failed-original recovery results with an
  explicit valid selection and no multiple-success conflict
- `diagnostics`: one resolution per original key, including provider
  uncertainty IDs, per-recovery attempt outcome/adjudication/uncertainty
  diagnostics, evaluation-selection status, conflicts, explicit scoreability,
  and an explanation

Uncertain, incomplete, unselected, unusable, and conflict results are not
included. A successful R2 can therefore be selected for evaluation after an
explicit decision while R1 remains provider-outcome-unknown. The resolver does
not mutate the original records. The existing evaluator does not call this
interface in Phase A.

## Offline recovery integration boundary

`src/recovery_integration.py` provides an offline orchestration boundary for a
caller-supplied provider callable. The caller must pass an explicit allowlist
of observation keys and an approval-verification callback. The callback is
invoked before `recovery_started`; the ledger start is durably appended before
the provider callable is invoked. The integration invokes the provider at most
once per call and does not write recovery results into `ResultsStore`.

The callback is an interface for an authorized operator/workflow, not an
authentication mechanism. It does not establish that the caller or callback
is authorized, and shaped provenance is not proof of approval. The responsible
workflow must verify the designated human approval, the durable
observation-specific reference, and the authoritative plan before invoking
this boundary. No live provider implementation or runner integration is
included.

Only the dedicated `UnsuccessfulProviderResponse` signal, or a returned
response whose output fails the existing parser/schema checks, records
`recovery_response_failed`. Other provider exceptions propagate without a
terminal event, leaving the start unresolved and uncertain. This boundary does
not automatically retry. The callback and provider must be injected; no
default ledger is opened by importing the module.

The integration trusts the injected provider to raise
`UnsuccessfulProviderResponse` only after an unsuccessful response was
actually received; it does not independently verify transport-level receipt.
Arbitrary exceptions, including timeout and connection errors, are not
evidence of response receipt. No real provider adapter or provider receipt has
been verified.

## Recovery-aware evaluation status

The approved recovery-aware evaluation integration is not implemented. The
approved protocol requires an immutable, verifiable experiment plan for
expected observation/run coverage, but no such artifact or integrity
verification mechanism has been identified. Do not derive expected coverage
from recovery-augmented results, create evaluation artifacts, or treat the
existing evaluator as recovery-aware until that plan is identified and
approved. `src/recovery_evaluation.py` is a diagnostic-only adapter: it
preserves the resolver result, selection event provenance, and parsed-output
discrepancies when a successful original and recovery differ. It does not
calculate metrics, establish expected run coverage, publish evaluation
artifacts, or verify the authoritative plan. Discrepancy detection compares
`parsed_json`, not response metadata.

## Limitations

The lock protects cooperating processes on filesystems that honor `flock`; it
does not protect against manual edits or non-cooperating writers. Local
`fsync` cannot establish exactly-once provider execution or whether a request
was processed or billed. The known retry-log ambiguity remains unresolved.
The injected offline boundary is not live-provider readiness. The current
runner still does not enable live recovery, and evaluation integration remains
blocked by the authoritative-plan gap.
