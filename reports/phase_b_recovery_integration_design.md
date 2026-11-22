# Phase B — Recovery Integration Design Review

## Executive summary

**VERIFIED:** The existing runner creates observation keys from split, record ID,
prompt ID, and run; skips keys already reported present by `ResultsStore`; sends
requests in `experiment_runtime.run_pending`; and saves each final result through
`ResultsStore.add_record()` and `.save()`. The runner maintains a separate
append-only retry log for HTTP 429 responses. It does not import or call the
recovery ledger.

**VERIFIED:** The recovery ledger is an offline event log and resolver. It records
recovery lifecycle events, keeps provider-outcome uncertainty separate from
evaluation selection, and returns only originals or explicitly selected,
conflict-free recovery results through `build_scoreable_results()`. Its
documentation says the existing evaluator does not call this interface.

**RECOMMENDED:** Keep recovery as a separately authorized path. Do not have the
ordinary runner automatically retry failed or missing observations. If a
recovery request is eventually authorized, write a `recovery_started` event
before the provider call, record a completed result only after receiving and
validating it, and keep recovery results out of the original results store.
An incomplete start must remain uncertain and must never be treated as proof
that a provider request was or was not processed.

**NOT VERIFIED:** The evaluator and metrics implementations were not inspected.
The permitted files do not establish their module paths or show their input
loading and scoring behavior. The ledger documentation explicitly says there
is no evaluator integration in this phase.

## Scope and evidence labels

- **VERIFIED** means directly established by the permitted source or
  documentation inspected for this review.
- **INFERRED** means a conservative conclusion from verified source behavior,
  not a directly recorded fact.
- **RECOMMENDED** means a proposed future safeguard or design choice, not
  current behavior.
- **NOT VERIFIED** means the permitted sources do not establish the claim.

Line references below point to the source as inspected. No project code was
executed. In particular, behavior inside `ResultsStore` was not inspected;
the report describes only the calls and control flow visible in the runner.

## Current execution architecture

### Planning and observation identity

**VERIFIED:** `run_experiment.main()` accepts a split, prompt selection, run
count, call cap, and dry-run option; it loads records, prompts, result metadata,
and a `ResultsStore` before planning calls. The runner generates one item for
each record/prompt/run combination and computes its key with `result_key(item)`.
The generated item includes split, record ID, prompt ID, run, and rendered
prompt text. The pending list includes an item only when `store.has_key(key)`
returns false. The pending-filter code does not branch on a stored record's
success or error status.

Sources: [runner setup and store load](../src/run_experiment.py#L49-L83);
[pending observation construction](../src/run_experiment.py#L130-L142).

**VERIFIED:** `load_records()` opens the path derived from the requested split
and creates record IDs from the split and input line number. This describes
source behavior only; no dataset was opened for this review.

Source: [record loading](../src/experiment_runtime.py#L29-L43).

**VERIFIED:** Recovery observation keys use four non-empty pipe-separated
components, with a positive integer in the run component. The result validator
also checks that a completed recovery result's `result_key` equals the
recovery's observation key.

Sources: [recovery key validation](../src/recovery_ledger.py#L109-L121);
[recovery result identity validation](../src/recovery_ledger.py#L123-L147).

### Existing results, failures, and retry representation

**VERIFIED:** Results metadata is checked against the current runner metadata.
Refreshing metadata is restricted to an empty results store. In dry-run mode,
the runner returns before loading the retry counts, creating a Groq client, or
calling `run_pending()`. In live mode, it loads retry counts, creates a client
with `max_retries=0`, then calls `run_pending()`.

Sources: [metadata gate](../src/run_experiment.py#L72-L128);
[dry-run and live-call gates](../src/run_experiment.py#L174-L202).

**VERIFIED:** The runner stores each result through `store.add_record()` and
`store.save()`, including non-429 HTTP errors, caught generic exceptions,
interruptions handled by Python, and completed responses. A non-429
`APIStatusError` is represented as `HTTP <status>`. A generic exception is
represented by its exception class name; the exception text is not copied into
that result by this code path. A caught `KeyboardInterrupt` is saved as
`Interrupted; request outcome unknown`. An unparseable 429 reset value is also
saved as a result and stops the run.

Sources: [result construction](../src/experiment_runtime.py#L226-L256);
[HTTP error and 429 handling](../src/experiment_runtime.py#L365-L408);
[interrupt and generic exception handling](../src/experiment_runtime.py#L410-L436).

**VERIFIED:** Retry history is separate from results. `load_retry_counts()`
accepts only the retry-log schema `{key, attempt, timestamp_utc, wait_seconds}`,
checks sequential attempt numbers per key, and rejects malformed or ambiguous
entries. `append_retry_event()` writes the event, flushes it, and calls
`fsync()`. The event is appended for a received 429 response, not for an
ordinary request start.

Sources: [retry-log schema and loading](../src/experiment_runtime.py#L20-L22)
and [retry-count validation](../src/experiment_runtime.py#L77-L124);
[retry-event append](../src/experiment_runtime.py#L127-L145);
[429 branch](../src/experiment_runtime.py#L365-L408).

**VERIFIED:** The runtime counts five retries after the initial call: the
constant is `MAX_429_RETRIES = 5`, and the terminal check is at attempt
`MAX_429_RETRIES + 1`. Calls are counted immediately before entering the
provider call, so a retry consumes a call from `--max-calls`. The daily-token
limit check occurs before a request.

Sources: [retry constant](../src/experiment_runtime.py#L18-L22);
[pre-call checks and provider call](../src/experiment_runtime.py#L302-L363);
[retry terminal condition](../src/experiment_runtime.py#L393-L408).

**VERIFIED:** Retry timing supports numeric and HTTP-date `Retry-After` values.
When no parseable `Retry-After` is returned, the runtime chooses a fallback
wait; when a supplied value cannot be parsed, it writes a retry event with a
null wait and stops safely.

Sources: [Retry-After parser](../src/experiment_runtime.py#L46-L75);
[429 wait handling](../src/experiment_runtime.py#L369-L398).

**INFERRED:** A result saved with an error will normally be excluded from a
later pending plan if `ResultsStore.has_key()` treats that key as present. The
runner's filter is based on `has_key()` alone; the exact index and persistence
semantics of that method were not verified because `results_store.py` was
outside the inspected-file scope.

### Provider call and local persistence boundary

**VERIFIED:** The provider request begins at
`client.chat.completions.create(...)`. A returned response is read for content
and token usage, converted into a result record, and passed to
`store.add_record()` and `store.save()`. A retry-log event is written only
after a 429 response is caught.

Sources: [request and response extraction](../src/experiment_runtime.py#L333-L363);
[429 handling](../src/experiment_runtime.py#L365-L408);
[final result save](../src/experiment_runtime.py#L420-L436).

**NOT VERIFIED:** The runner module's docstring describes results as saved
atomically, but the underlying `ResultsStore` implementation was not among the
permitted files inspected. Therefore the exact write protocol, atomicity,
concurrency behavior, and crash durability of `store.save()` are not
independently established here.

**INFERRED:** There is no transaction spanning the provider request, retry log,
results store, and recovery ledger. The visible runner and runtime call these
systems separately; a future integration must define ordering and recovery
behavior for failures between those operations.

### Evaluation and metrics consumption

**VERIFIED:** The recovery documentation identifies
`build_scoreable_results(original_records, events)` as the supported
recovery-aware scoring boundary and says that the existing evaluator does not
call it in Phase A.

Source: [scoreable-results interface and non-integration statement](../RECOVERY_LEDGER.md#L111-L125).

**NOT VERIFIED:** No permitted source imports or names an evaluator or metrics
module path. The actual evaluator's input format, duplicate handling, metrics
aggregation, and historical-output behavior therefore cannot be established
from this review. No evaluator or metrics module was opened.

## Recovery-ledger interface

### Events and lifecycle

**VERIFIED:** The ledger defines four event types:
`recovery_started`, `recovery_completed`, `recovery_adjudicated`, and
`recovery_selected_for_evaluation`. Starts and terminal lifecycle events carry
a recovery ID, observation key, UTC timestamp, and provenance. Completion
contains a result record; adjudication authorizes another attempt; evaluation
selection identifies a recovery and a unique selection ID.

Sources: [event schemas](../src/recovery_ledger.py#L27-L60);
[documented event fields](../RECOVERY_LEDGER.md#L7-L26).

**VERIFIED:** A recovery must start before completion or adjudication. A start
with no terminal event is uncertain and blocks another recovery for that
observation until an adjudication event. Adjudication permits another attempt
but does not resolve whether the earlier provider request was processed or
billed.

Sources: [event reduction and start-state rules](../src/recovery_ledger.py#L228-L312);
[uncertainty documentation](../RECOVERY_LEDGER.md#L81-L92).

**VERIFIED:** There is no dedicated `recovery_failed` event. A
`recovery_completed` event is terminal even when its schema-valid result is
unusable; the resolver's success predicate is a separate check. An integration
must therefore define the meaning of completion carefully. **RECOMMENDED:**
Do not use a completion event as a generic marker for a timeout or ambiguous
failure if that would suppress unresolved provider uncertainty. Keep the start
incomplete until the outcome is safely recorded or explicitly adjudicated.

Sources: [event types and reducer terminal handling](../src/recovery_ledger.py#L27-L60)
and [success predicate](../src/recovery_ledger.py#L719-L726).

**VERIFIED:** The ledger does not execute a provider request. Its module and
documentation describe an append-only ledger and explicitly state that the
subsystem is offline and not integrated with the runner or evaluator.

Sources: [ledger module description and public methods](../src/recovery_ledger.py#L1-L5)
and [offline-only statement](../RECOVERY_LEDGER.md#L1-L5).

### Completion, selection, and conflict handling

**VERIFIED:** A completed recovery result must have the existing result-record
schema and match its observation key. A recovery is considered usable only
when its parse/schema flags are true, parsed JSON is an object, and its error
is null.

Sources: [result validation](../src/recovery_ledger.py#L123-L166);
[success predicate](../src/recovery_ledger.py#L719-L726).

**VERIFIED:** A selection event must refer to a completed, usable recovery for
the same observation and cannot precede completion. Only one selection decision
per observation is accepted. Selection is a separate event; it does not
resolve provider uncertainty.

Sources: [selection validation](../src/recovery_ledger.py#L262-L291);
[selection and uncertainty semantics](../RECOVERY_LEDGER.md#L94-L101).

**VERIFIED:** Two or more successful recoveries for one observation produce a
conflict and no scoreable result, even if a selection event exists. With one
successful recovery and a failed original, the recovery is scoreable only when
that exact recovery has an explicit selection. A successful original remains
selected rather than being replaced by a recovery.

Source: [result resolution](../src/recovery_ledger.py#L728-L824).

**VERIFIED:** If the original is successful and exactly one recovery is also
successful, the resolver selects the original and does not classify the pair
as `conflict_requires_review`; that status is set when there are at least two
successful recoveries. The resolver does not compare the original and recovery
payloads for equality. This preserves the original, but it does not produce a
specific conflict status for a differing single recovery.

Source: [successful-recovery conflict threshold and original-selection branch](../src/recovery_ledger.py#L755-L790).

### Locking and identity guarantees

**VERIFIED:** The ledger canonicalizes the input path on construction, derives
the adjacent lock path from that canonical path, and exposes read-only
properties. The existing-file device/inode identity is retained per instance.

Sources: [construction and path properties](../src/recovery_ledger.py#L329-L379);
[documented path and inode policy](../RECOVERY_LEDGER.md#L28-L50).

**VERIFIED:** Reads use a shared `flock`; mutations use an exclusive lock.
Append reloads the latest events, validates the candidate event sequence,
opens the ledger relative to a directory descriptor with no-follow flags,
checks the opened descriptor with `fstat()`, appends, flushes, and fsyncs before
releasing the lock. First creation uses exclusive creation, followed by a
parent-directory fsync.

Sources: [lock acquisition/release](../src/recovery_ledger.py#L450-L491);
[safe read and descriptor validation](../src/recovery_ledger.py#L493-L548);
[exclusive append transaction](../src/recovery_ledger.py#L647-L715).

**VERIFIED:** The lock is advisory and intended for cooperating processes on
local macOS/POSIX filesystems that honor `flock`. The documentation excludes
arbitrary non-cooperating path, ledger-file, or lock-file replacement and
does not claim exactly-once provider execution.

Source: [locking and external-replacement limitations](../RECOVERY_LEDGER.md#L52-L79).

## Integration-point analysis

| Point | Verified current behavior | Potential ledger use | Main risk and necessity |
|---|---|---|---|
| Planning and pending selection | `main()` computes keys and filters using `store.has_key()` before dispatch. [Runner planning](../src/run_experiment.py#L130-L142) | A future, separately invoked recovery planner could accept explicit observation keys and read the ledger to reject unresolved or conflicted candidates. | **RECOMMENDED; optional to ordinary execution.** Changing ordinary pending selection could turn saved failures into implicit retries or alter historical results. Preserve ordinary runner behavior until that policy is explicitly approved. |
| Immediately before provider submission | `run_pending()` calls the provider after the pre-call checks. [Pre-call path](../src/experiment_runtime.py#L302-L363) | For an explicitly authorized recovery only, append `recovery_started` before the provider call and issue no request unless that append succeeds. | **RECOMMENDED; necessary for an auditable recovery attempt.** A start proves only that the local workflow recorded intent; a crash before submission remains indistinguishable from a request that may have been submitted. |
| After provider response and validation | A returned response is parsed into fields and later converted to a result. [Response handling](../src/experiment_runtime.py#L333-L363) [Result creation](../src/experiment_runtime.py#L420-L436) | Append `recovery_completed` with the full validated result once a response has been received and the result record is ready. | **RECOMMENDED; necessary to record a recovery result.** A crash or ledger-write failure before completion leaves the recovery uncertain even if the provider responded. The completion record is not proof of billing or exactly-once execution. |
| Results persistence | The runner calls `add_record()` and `save()` after request handling. [Save paths](../src/experiment_runtime.py#L386-L388) [final save](../src/experiment_runtime.py#L435-L436) | Keep a recovery result in the recovery ledger rather than silently merging it into the original results store. | **RECOMMENDED; necessary for preserving original observations.** The two stores have no visible shared transaction. Mirroring creates a partial-write and duplicate-selection problem. |
| Retry history | The runner logs completed 429 events in a separate retry log. [Retry event writer](../src/experiment_runtime.py#L127-L145) [429 path](../src/experiment_runtime.py#L365-L408) | Use the recovery ledger for an explicitly authorized recovery lifecycle, not as a replacement for the existing 429 retry log. | **Optional for routine 429 handling.** A recovery event does not encode the same per-429 timing or retry-budget details; conflating the logs would obscure their distinct semantics. |
| Evaluation boundary | The ledger exposes `build_scoreable_results`; documentation says the evaluator does not call it. [Ledger resolver](../src/recovery_ledger.py#L826-L841) [non-integration statement](../RECOVERY_LEDGER.md#L111-L125) | A future evaluator adapter could pass original records and ledger events to the resolver and score only its returned records. | **Necessary before recovery affects metrics.** Directly appending recovery results to the ordinary store could score unselected or conflicted observations and change historical results. |

**RECOMMENDED ordering:** `recovery_started` append succeeds → provider request →
response/result validation → `recovery_completed` append succeeds → later
explicit selection, if policy permits. Do not hold the ledger lock across the
network call. The ledger's current locking API protects each ledger operation,
not the entire external request lifecycle.

## Critical failure-scenario matrix

| Scenario | What current source can establish | What it cannot establish | Ledger record and unresolved state | What must never be inferred automatically |
|---|---|---|---|---|
| Process crash before API request | The runner has a pre-call path and a provider call; it does not log ordinary request starts. [Pre-call and request](../src/experiment_runtime.py#L302-L363) | A crash may leave no durable evidence distinguishing “not submitted” from a start that was recorded only by a future integration. | A previously appended `recovery_started` with no terminal event remains uncertain. | Do not infer that no provider request occurred from an incomplete start. Do not retry merely because a result is absent. |
| Crash after submission, before response persistence | The result save follows the provider call; the retry log is written only when a 429 response is handled. [Provider call](../src/experiment_runtime.py#L333-L363) [429 append](../src/experiment_runtime.py#L365-L408) | Whether the provider processed the request, returned a response, or billed it. | An incomplete start records unresolved provider-outcome uncertainty; no completion should be fabricated. | Do not infer failure, non-processing, or no billing from missing local output. |
| Provider timeout or connection failure with ambiguous outcome | Generic exceptions are represented by exception class and saved as result errors; request/response transport details are not durably recorded here. [Exception and save path](../src/experiment_runtime.py#L410-L436) | Whether the remote service received or completed the operation. | Keep an incomplete recovery uncertain unless a real validated result was received and recorded. Any authorization of another attempt must be explicit. | Do not treat exception class or local timeout as proof that the provider did not process or bill the request. |
| Successful response received, local persistence fails | The code obtains response fields before calling the result-store methods. [Response extraction](../src/experiment_runtime.py#L333-L363) [Save call](../src/experiment_runtime.py#L420-L436) | Whether a failed `save()` left any bytes or an otherwise recoverable result; `ResultsStore` internals were not inspected. | A completed recovery event can preserve a validated result if its append succeeds; otherwise the start remains uncertain. | Do not assume that a save exception means no result was written or that the provider request can safely be repeated. |
| Duplicate or concurrent recovery attempts | Ledger mutations reload under an exclusive lock; event reduction rejects another unresolved recovery start for the same key. [Lock transaction](../src/recovery_ledger.py#L647-L715) [active-start rule](../src/recovery_ledger.py#L238-L260) | Exactly-once API execution, or prevention of duplicate calls by a client that ignores a failed start append or coordinates outside the ledger. | A successful second `recovery_started` is rejected while a prior one is unresolved. | Do not issue a provider call unless its start append succeeded. Do not interpret ledger locking as a lock spanning remote execution. |
| Recovery succeeds while original attempt remains uncertain | The resolver reports uncertainty separately from evaluation selection. [Resolution fields and construction](../src/recovery_ledger.py#L72-L96) [resolver](../src/recovery_ledger.py#L728-L824) | Whether the original provider attempt was processed or billed. | Record the recovery completion; a separate valid selection may make it scoreable, while the original uncertainty remains reported. | Do not let a selected recovery clear or rewrite uncertainty about the original attempt. |
| Multiple successful recoveries for one observation | The resolver counts successful recovery IDs and sets `conflict_requires_review` for two or more. [Conflict branch](../src/recovery_ledger.py#L755-L790) | Which response is authoritative absent a separate review process. | Preserve all events, return no scoreable result for that observation, and require review. | Do not choose the newest, first, or explicitly selected recovery automatically when multiple successful recoveries exist. |
| Recovery differs from an existing successful original | A successful original remains selected; the resolver does not compare payload equality. With two or more successful recoveries it reports a conflict. [Resolver branches](../src/recovery_ledger.py#L762-L790) | Whether a single successful recovery is semantically equivalent to or conflicts with the original. | Keep the original selected. Consider adding an explicit diagnostic/adjudication policy before any future evaluator integration. | Never overwrite the original with a recovery. Do not claim that a single recovery/original pair was compared or found consistent. |
| Restart after partially completed recovery | The ledger reloads and validates the append-only event sequence. An incomplete start remains uncertain and blocks a new start until adjudicated. [Ledger read](../src/recovery_ledger.py#L493-L548) [lifecycle reducer](../src/recovery_ledger.py#L228-L312) | Whether an incomplete attempt actually reached the provider. | Resume from the ledger state; leave uncertainty visible. An explicit adjudication may authorize a further attempt without resolving the prior outcome. | Do not equate restart, adjudication, or absence of completion with proof of non-execution. |

## Evaluation-integrity safeguards

1. **Keep originals immutable. — VERIFIED/RECOMMENDED.** The resolver returns
   copies and does not mutate original records; the documentation says the
   original successful result remains selected. Future integration should
   preserve the existing result store and produce a separate, versioned
   recovery-aware evaluation input. Sources:
   [resolver](../src/recovery_ledger.py#L728-L824);
   [documented interface](../RECOVERY_LEDGER.md#L111-L125).
2. **Require explicit selection. — VERIFIED.** One successful recovery is not
   scoreable merely because it is the only success; a valid
   `recovery_selected_for_evaluation` event is required. Source:
   [selection branch](../src/recovery_ledger.py#L771-L803).
3. **Exclude conflicts. — VERIFIED.** Two or more successful recoveries result
   in `scoreable_result=None`, even if a selection exists. Source:
   [conflict branch](../src/recovery_ledger.py#L764-L790).
4. **Do not score duplicate observation keys. — RECOMMENDED.** Treat the
   observation key as the evaluation uniqueness key, validate that the input
   originals are a complete and unambiguous mapping, and ensure the final
   scoring input contains at most one record per key. The resolver accepts a
   mapping and returns one resolution per mapping key; downstream behavior was
   not inspected.
5. **Do not treat ledger events as provider proof. — VERIFIED/RECOMMENDED.**
   A start is a local event, and an incomplete start remains uncertain even
   after adjudication. Do not derive provider execution or billing facts from
   event presence, absence, selection, or result choice. Source:
   [event model](../src/recovery_ledger.py#L228-L312);
   [uncertainty documentation](../RECOVERY_LEDGER.md#L81-L92).
6. **Freeze historical metrics. — RECOMMENDED.** Preserve prior evaluation
   inputs and metrics. Generate recovery-aware metrics as a separately
   identified evaluation version with explicit authorization, recorded input
   provenance, and a clear statement of which observations were excluded or
   selected. The actual metrics pipeline and historical-output behavior are
   **NOT VERIFIED** in this review.
7. **Surface unresolved originals and recovery diagnostics. — VERIFIED/
   RECOMMENDED.** `ScoreableResults` includes per-key diagnostics alongside
   scoreable records. A future scoring boundary should retain and publish those
   diagnostics rather than silently dropping excluded observations. Source:
   [scoreable result types](../src/recovery_ledger.py#L72-L100);
   [builder](../src/recovery_ledger.py#L826-L841).

## Staged implementation plan

### Phase B — Design review (current)

**VERIFIED:** This phase documents source behavior, risks, and gates without
changing implementation. Completion requires explicit decisions on uncertainty,
retry authorization, result selection, and historical metrics before any
integration work.

### Phase C — Offline interface and contract tests

**RECOMMENDED entry criteria:** Approve the recovery protocol and the decisions
listed below. Define which runner observations are eligible, what evidence
permits `recovery_adjudicated`, how a recovery result is validated, and how
provenance is assigned.

**RECOMMENDED scope:** Add offline tests using temporary ledger/result files
and synthetic records only. Test event ordering, stale/unresolved starts,
selection, original preservation, conflict exclusion, malformed ledgers, and
the exact scoreable-input shape. Do not invoke a provider or touch project
outputs.

**Exit gate:** Contract tests demonstrate that uncertain, unselected, and
conflicted results cannot enter the scoreable set and that originals are
unchanged.

### Phase D — Isolated runner integration tests

**RECOMMENDED entry criteria:** Phase C contracts pass and a narrow adapter
interface is approved. Use an injected fake provider, synthetic observations,
temporary paths, and deterministic failure points around request submission,
response receipt, ledger append, and result persistence.

**RECOMMENDED scope:** Verify that no provider call occurs if a start event
cannot be appended; simulate a lost response and post-response persistence
failure; verify restart behavior and concurrent-start rejection; ensure recovery
records do not modify the ordinary results store.

**Exit gate:** All failure-injection cases preserve uncertainty and evaluation
integrity. Tests must not use credentials or live endpoints.

### Phase E — Separately authorized controlled live recovery

**RECOMMENDED entry criteria:** Explicit approval of the exact observation
keys, request count, model/settings, retry and billing-uncertainty policy,
operator, provenance, stop conditions, and intended use of returned results.
Confirm a backup and a monitored, isolated ledger path.

**RECOMMENDED scope:** Only the approved bounded request(s); no automatic
retry after uncertainty. Stop on any unexpected response, persistence error,
budget ambiguity, or identity/lock error. This review does not authorize or
recommend a live recovery by itself.

**Exit gate:** Reconcile the provider outcome and local ledger state without
assuming that a missing or ambiguous response means no processing or billing.
Require separate approval before using a recovery in evaluation.

### Phase F — Evaluation integration (separate approval)

**RECOMMENDED entry criteria:** Evaluator and metrics source paths and behavior
are established; Phase C/D contracts pass; original-result preservation and
historical-metric versioning policy are explicitly approved.

**RECOMMENDED scope:** Integrate only through the scoreable-results boundary,
with a separately versioned evaluation input and diagnostics. Do not overwrite
or silently recalculate historical metrics.

**Exit gate:** Offline tests prove exactly one scoreable result per eligible
observation, no unselected or conflicted recovery is included, and historical
artifacts remain unchanged unless separately authorized.

## Risks and unresolved decisions

These require explicit approval before implementation or live use:

1. Which failures or missing observations are eligible for recovery, and who
   may authorize each attempt?
2. What evidence is sufficient to append `recovery_adjudicated` and permit a
   further request when the earlier outcome is unknown?
3. Is any retry allowed after timeout, disconnect, interruption, or a crash
   around request submission? If so, what duplicate-billing risk is accepted?
   How should a known provider error versus an ambiguous failure be represented,
   given that the current event schema has no dedicated failure event?
4. Should a recovery be attempted if the ordinary results store already
   contains a failed record for that key, given that the current pending filter
   skips keys reported present?
5. How should a single successful recovery that differs from a successful
   original be diagnosed? Current resolution preserves the original but does
   not compare the two payloads or mark that pair as a recovery conflict.
6. Who can create an evaluation-selection event, what evidence must it cite,
   and can a selection ever be revoked or superseded? The current schema allows
   only one selection decision per observation.
7. May recovery-aware metrics ever replace historical metrics, or must they
   always be separately versioned? No such policy is established by the
   inspected sources.
8. What are the required ledger retention, backup, access-control, and
   reconciliation procedures for a live recovery?
9. What exact evaluator/metrics modules consume results, and what input contract
   and duplicate semantics do they implement? These could not be established
   from the permitted files.

## Files and validation boundary

- **Files read:** `src/run_experiment.py`, `src/experiment_runtime.py`,
  `src/recovery_ledger.py`, and `RECOVERY_LEDGER.md`.
- **File created:** `reports/phase_b_recovery_integration_design.md`.
- **Tests and application code:** None executed.
- **Credentials, held-out data, and experiment output contents:** Not accessed.
- **Network/API:** No requests made.
- **Limitations:** `ResultsStore` internals and evaluator/metrics implementations
  were not inspected. Therefore exact result-store durability and the current
  evaluation/metrics consumption path remain unverified.
