# Phase B.2 — Recovery Protocol and Evaluation Policy Specification

## Status and evidence convention

This specification records approved recovery-policy decisions. Approval is
limited to policy; it is not implementation authorization or permission to
run recovery requests or change evaluation outputs.

- **VERIFIED** — established by the permitted audit reports or ledger contract.
- **INFERRED** — a conservative conclusion from verified behavior.
- **RECOMMENDED** — proposed policy, not current behavior.
- **NOT VERIFIED** — the permitted documents do not establish the fact.

### Current status and authorization

- **Policy decisions:** B2-D01 through B2-D15 are approved as recorded below.
- **Implementation:** Not authorized.
- **Phase C:** Awaiting separate explicit authorization.
- **Phases D, E, and F:** Not authorized.

### Phase B.3 approval record

On **2026-11-14**, the user accepted the recommended defaults for all 15
decisions: B2-D01, B2-D02, B2-D03, B2-D04, B2-D05, B2-D06, B2-D07,
B2-D08, B2-D09, B2-D10, B2-D11, B2-D12, B2-D13, B2-D14, and B2-D15.
**Additional conditions:** None. This approval does not authorize
implementation or any implementation phase.

## 1. Recovery eligibility

### Observation identity and initial checks

**VERIFIED:** The existing observation identity is the four-part key
`split|record_id|prompt_id|run`. The runner creates candidates from records,
prompts, and run numbers, and its normal pending filter checks only whether
`ResultsStore.has_key(key)` is true. It does not choose based on a stored
record's success/error state. Sources:
[key definition](../src/results_store.py#L28-L33);
[runner candidate planning](../src/run_experiment.py#L130-L142);
[membership check](../src/results_store.py#L81-L82).

**RECOMMENDED:** Recovery is not a continuation of the ordinary pending
filter. Require a separately approved request for a specific observation key,
verified against an authoritative experiment plan and the current results and
ledger snapshots. Do not treat “absent from `ResultsStore`” or
“`has_key()` is false” as sufficient evidence that the provider was never
called.

**RECOMMENDED — authoritative plan contents:** Before recovery or
recovery-aware evaluation, identify an authoritative plan artifact that
establishes (1) every expected observation key; (2) each key's split,
record ID, prompt ID, and run; (3) expected run coverage, including its
split/prompt dimensions; (4) relevant prompt and configuration identity; and
(5) a stable snapshot identity or verifiable hash over canonical plan
contents. The actual plan artifact and its integrity-verification mechanism
have not been identified or approved in the permitted documents. They must be
identified and approved before evaluation integration; this specification
does not assume that such an artifact already exists.
Sources: [runner candidate construction](../src/run_experiment.py#L130-L142);
[current split-wide run derivation](../src/evaluate.py#L57-L69);
[Phase B.1 provenance limitations](./phase_b1_persistence_evaluation_audit.md#L392-L425).

### Proposed eligibility by case

| Case | Proposed default | Local/provider distinction |
|---|---|---|
| Missing observation in `ResultsStore` | **Not eligible under the current resolver contract.** First establish the planned key from an authoritative plan and establish why no result was persisted. Do not fabricate a result record. A separately approved contract change is needed before a missing original can be represented to the resolver. | **INFERRED:** Absence of a stored row is not evidence that no request was submitted or processed. |
| Failed observation already present in `ResultsStore` | Keep the original record unchanged. It may be considered for recovery only after a human review identifies the failure class and explicitly authorizes that observation. The presence of an `error` field alone is not proof of safe-to-repeat failure. | A saved local error is evidence about local recording; it may not establish provider non-processing. |
| HTTP 429 | Do not use the existing retry history to authorize or execute additional retries until the previously identified duplicate attempt-number anomaly has been independently reconciled. A received 429 does not automatically establish that another request is safe. Any later retry or separate recovery requires reconciled history, an explicit approved retry budget, and human authorization; do not infer that a 429 guarantees no processing or billing unless authoritative provider semantics are established. | A received HTTP response establishes that the client observed that response; it does not, by itself, establish billing or that another request is safe. |
| Other HTTP error | Do not automatically recover. Preserve the observed status and require review of whether the error is terminal, correctable, or ambiguous. A corrected request is still a new provider attempt and requires explicit authorization. | An HTTP status is a known response, but this specification does not presume it proves non-processing or no billing. |
| Connection error or timeout | Treat as provider-outcome-uncertain unless reliable evidence establishes the request could not have been submitted. No automatic retry. Further attempt requires explicit adjudication plus acceptance of possible duplicate processing/billing. | Local transport failure does not prove what the provider received or did. |
| Interrupted request or process crash | Treat an interruption after the request boundary as uncertain. A caught `KeyboardInterrupt` is stored by the current runtime as “request outcome unknown”; a hard process crash may leave no final result. No automatic retry. | A local interruption/crash is not evidence of provider non-execution. |
| Known local failure before request submission | It may be considered for a later request only if durable, trustworthy evidence proves execution stopped before entering the provider call, the authoritative plan confirms the key, and a human authorizes the attempt. The current runner does not durably log ordinary request-start events, so the current artifacts may not establish this distinction. | “Known local” requires positive evidence that the provider call was not entered; a missing result is insufficient. |
| Successful original result | Ineligible for a recovery request under the proposed default. Preserve and continue selecting the original for scoring. If a separate successful recovery already exists, record a discrepancy for review; do not replace the original. | A successful local record is not proof about billing for any earlier uncertain request, but it is the selected result under the existing resolver. |

**VERIFIED:** The current runner appends retry events after caught 429 responses,
not at ordinary request start, and catches some interruptions as an unknown
request outcome. These facts explain why the current attempt log cannot be
treated as a complete request-submission journal. Sources:
[429 retry log behavior](../src/experiment_runtime.py#L365-L408);
[interrupt handling](../src/experiment_runtime.py#L410-L417);
[retry event schema and append](../src/experiment_runtime.py#L77-L145).

**RECOMMENDED:** Classify eligibility into at least three categories in a
future control record: (1) known local pre-submit stop, (2) observed provider
response with policy-specific interpretation, and (3) provider outcome
uncertain. Only category (1) can be considered for a recovery without
acknowledging duplicate-provider risk; category (3) must never be auto-retried.
The classification itself must cite evidence and must not be inferred solely
from an exception name or missing output.

## 2. Success definition

### Existing predicates

**VERIFIED:** `ResultsStore.load()` validates top-level and record field sets,
the identity key, and `reasoning_tokens`; `add_record()` checks field-set and
`reasoning_tokens` constraints before insertion. Those validation paths do not
define a single “successful result” predicate or enforce consistency between
`error`, parse/schema flags, and `parsed_json`.
Sources: [store load validation](../src/results_store.py#L46-L79);
[store insertion validation](../src/results_store.py#L84-L95).

**VERIFIED:** The current metrics usability predicate requires
`parse_ok is True`, `schema_ok is True`, and `parsed_json` to be a dictionary.
It does not check `error`. The Phase B.1 audit identified that an error-marked
record with those other fields set can therefore be scored as usable.
Sources: [metrics usability predicate](../src/metrics.py#L44-L60);
[Phase B.1 finding](./phase_b1_persistence_evaluation_audit.md#L255-L323).

**VERIFIED:** The recovery resolver's success predicate uses those three
conditions and additionally requires `error is None`. The recovery result
validator also requires the exact result schema and a `result_key` matching
the expected observation key.
Sources: [recovery result validation](../src/recovery_ledger.py#L159-L205);
[recovery success predicate](../src/recovery_ledger.py#L719-L726).

### Approved success and scoreability definitions

**APPROVED — USER ACCEPTED — `successful_result`:** A technically valid
result satisfying the approved response-validation requirements and the
authoritative observation-identity requirements. At minimum:

1. `parse_ok is True`.
2. `schema_ok is True`.
3. `parsed_json` is a dictionary.
4. `error is None`.
5. Its `split`, `record_id`, `prompt_id`, and `run` produce exactly the
   expected observation key in the authoritative plan.

This adopts the recovery resolver's stricter error treatment and makes
identity consistency part of technical validity. Sources:
[resolver success predicate](../src/recovery_ledger.py#L719-L726);
[resolver identity validation](../src/recovery_ledger.py#L159-L205);
[store key format](../src/results_store.py#L28-L33).

**APPROVED — USER ACCEPTED — `scoreable_result`:** A `successful_result`
that also satisfies the applicable evaluation-selection and
evaluation-integrity policy. This includes an original retained as selected
under the applicable policy or an explicitly selected, conflict-free recovery
for the same planned observation. Selection is an evaluation decision; it
does not determine whether the response itself was technically valid or
resolve provider-outcome uncertainty.
Sources: [resolver result and selection states](../src/recovery_ledger.py#L755-L824);
[selection and conflict contract](../RECOVERY_LEDGER.md#L94-L120).

**APPROVED — USER ACCEPTED:** A technically successful recovery is not
automatically eligible for scoring. An unselected recovery, a recovery for an
observation with a successful original, or a recovery involved in a conflict
is not a `scoreable_result` under this policy.
Sources: [resolver selection/conflict behavior](../src/recovery_ledger.py#L755-L803);
[documented scoreable-results interface](../RECOVERY_LEDGER.md#L109-L124).

**APPROVED — USER ACCEPTED:** Error-marked records (`error is not None`) are
not `successful_result`s for recovery-aware scoring. Inconsistent records
whose parse/schema fields indicate success despite a non-null `error` must be
held for explicit review.

**APPROVED — USER ACCEPTED:** Do not silently change the existing evaluator or
metrics implementation. A recovery-aware scoring adapter should separately
enforce `successful_result` and `scoreable_result` before passing predictions
to metrics. Any change to existing metrics must be versioned and must not
overwrite historical outputs.
Sources: [current metrics predicate](../src/metrics.py#L44-L60);
[fixed evaluator output paths](../src/evaluate.py#L223-L232).

## 3. Recovery authorization and uncertainty

### Authority and provenance

**RECOMMENDED:** Only a designated human experiment owner, or a human
explicitly delegated by that owner, may authorize a provider recovery. The
agent, runner, evaluator, or an automated policy must not self-authorize a
request. Evaluation selection should require a separate designated reviewer
or an explicit approval decision recorded for that purpose.

**VERIFIED:** Current ledger provenance has exactly `actor` and `reference`
fields; the documentation says these are short identifiers, not credentials,
prompts, response text, or diagnostic dumps. Sources:
[event schema](../src/recovery_ledger.py#L27-L60);
[provenance contract](../RECOVERY_LEDGER.md#L7-L26).

**RECOMMENDED:** Record a stable approver identifier as `actor` and a durable
approval record, ticket, or decision identifier as `reference`. The referenced
approval should identify the exact observation key, reason, permitted attempt
count, risk acceptance, and the applicable policy/version. Keep secrets,
credentials, raw headers, prompts, and response bodies out of provenance.
If the current provenance fields cannot point to adequate evidence, obtain
approval for a contract extension rather than placing free-form sensitive data
in the ledger.

### Start event and request boundary

**RECOMMENDED:** Append `recovery_started` under the ledger's exclusive
transaction immediately before the provider call. Do not make the provider
call unless the append returns successfully. The start event records intent
and an attempt identity; it is not proof that the provider received a request.
The ledger's mutations lock/reload/validate/append as a transaction, but the
lock must not be held across the network call. Sources:
[ledger append transaction](../src/recovery_ledger.py#L647-L715);
[ledger lock contract](../RECOVERY_LEDGER.md#L52-L60);
[request-boundary design review](./phase_b_recovery_integration_design.md#L275-L290).

**RECOMMENDED:** If appending the start event fails, stop before the provider
call. Report the failure explicitly. Do not continue with a request that
cannot be associated with an auditable recovery start, and do not silently
fall back to the ordinary runner.

### Incomplete starts and adjudication

**VERIFIED:** A started recovery with no terminal event remains provider
outcome uncertain. The current reducer blocks another start for that
observation until an adjudication event. The existing adjudication event means
`authorize_further_attempt`; it does not establish whether the earlier request
was processed or billed. Sources:
[active-start and event lifecycle rules](../src/recovery_ledger.py#L228-L312);
[uncertainty policy](../RECOVERY_LEDGER.md#L81-L92).

**RECOMMENDED:** After a crash, timeout, lost response, or failed persistence
following request submission, leave the start incomplete. Reconcile available
local evidence and provider-side evidence without treating absence as proof.
Do not append a fabricated completion or “failed” result to make the ledger
look settled.

**APPROVED — USER ACCEPTED:** `recovery_adjudicated` may be authorized only by the
designated human authority after reviewing evidence and explicitly accepting
the possibility that the prior request was processed or billed. The evidence
reference should record the reason for the decision and the risk accepted.
When evidence cannot determine the outcome, the adjudication authorizes a
possible next attempt; it does not resolve the old uncertainty.

**APPROVED — USER ACCEPTED:** A further attempt may proceed only after a successful
adjudication append and a new explicit recovery authorization. It must use a
new recovery ID and a new `recovery_started` event. Keep the prior incomplete
recovery visible in diagnostics. This is consistent with the reducer allowing
a new start after adjudication while retaining unresolved provider uncertainty.
Sources: [reducer lifecycle](../src/recovery_ledger.py#L238-L312);
[uncertainty remains after adjudication](../RECOVERY_LEDGER.md#L81-L92).

The approved policy requires the reviewer to examine and reference available
evidence but does not establish a universal evidence-sufficiency standard.
Adjudication does not resolve the original provider-outcome uncertainty.

## 4. Recovery result selection

### Selection authority and evidence

**VERIFIED:** The current ledger accepts one `recovery_selected_for_evaluation`
decision per observation. It must reference a started, completed, usable
recovery for the same key, include provenance, and follow completion in ledger
order. Selection does not clear provider uncertainty. Sources:
[selection validation](../src/recovery_ledger.py#L262-L291);
[selection documentation](../RECOVERY_LEDGER.md#L94-L101).

**RECOMMENDED:** Evaluation selection requires an explicit human decision by
the designated evaluation authority, separate from the provider call itself.
The referenced evidence should identify the observation key, selected recovery
ID, validated result, competing originals/recoveries, selection rationale, and
the evaluation version to which the decision applies.

**RECOMMENDED:** Selection may be considered only when the original is not
successful and exactly one successful recovery is available. The resolver
already requires explicit selection for the single successful recovery when
the original is unsuccessful; preserve that rule. A selection does not clear
uncertainty from any earlier provider attempt.
Sources: [resolver selection logic](../src/recovery_ledger.py#L755-L824);
[selection and uncertainty contract](../RECOVERY_LEDGER.md#L94-L120).

### Multiple recoveries and conflict

**VERIFIED:** Two or more successful recoveries for one observation produce
`conflict_requires_review` and no scoreable result, even if a selection event
exists. A separate conflict-review mechanism is not implemented. Sources:
[resolver conflict branch](../src/recovery_ledger.py#L755-L790);
[conflict documentation](../RECOVERY_LEDGER.md#L94-L107).

**RECOMMENDED:** Do not score any observation with multiple successful
recoveries under the current contract. Preserve all events and results, surface
the conflict, and require a separately approved review process before any
future selection mechanism is designed. Do not choose newest, first, or
already-selected result automatically.

### Successful original versus recovery

**VERIFIED:** The resolver preserves a successful original rather than
replacing it with a recovery. It flags two or more successful recoveries as a
conflict, but it does not compare the payload of one successful recovery with
the successful original or mark that pair as a recovery conflict.
Sources: [resolver branches](../src/recovery_ledger.py#L755-L790);
[Phase B design finding](./phase_b_recovery_integration_design.md#L236-L246).

**RECOMMENDED:** If an original is successful, do not issue a recovery request
for that observation and do not select a recovery over it. If a successful
recovery already exists and differs, preserve the original, keep the recovery
out of scoring, and surface a discrepancy diagnostic. A future policy may
permit a different outcome only through explicit user approval and a
versioned review mechanism.

### Revocation or supersession

**VERIFIED:** The current event schema has no selection-revocation or
selection-supersession event, and only one selection decision per observation
is accepted. Sources: [event fields](../src/recovery_ledger.py#L27-L60);
[single-selection check](../src/recovery_ledger.py#L262-L291).

**RECOMMENDED:** Treat a selection decision as immutable under the current
schema. Do not edit or remove it. If revocation or supersession is required,
pause evaluation integration and obtain approval for a new append-only event
contract and versioning semantics; do not simulate revocation by rewriting
the ledger.

## 5. Missing original observations

**VERIFIED:** `resolve_results()` requires every recovery event key to exist
in `original_records`; an event for an unknown original key raises an error.
The resolver also validates that each original dictionary key matches the
record's identity and result schema. Sources:
[resolver input and unknown-key rejection](../src/recovery_ledger.py#L728-L751);
[result identity validation](../src/recovery_ledger.py#L159-L205).

**APPROVED — USER ACCEPTED:** Missing originals are excluded under the
current resolver contract. An authoritative plan must establish that the
observation was planned before it is treated as a planned-but-missing
observation. Do not fabricate a `ResultsStore` record, response, timestamp,
parse status, or failure reason. A future typed representation requires
separate approval before it can be used; none is approved or implemented.
Sources: [resolver input and unknown-key rejection](../src/recovery_ledger.py#L728-L751);
[authoritative-plan requirements](#1-recovery-eligibility).

## 6. Evaluation integrity

### Resolver use and output shape

**VERIFIED:** `build_scoreable_results(original_records, events)` returns a
tuple of scoreable full result records and a diagnostics mapping. The evaluator
currently loads `ResultsStore` directly and does not call the resolver. The
metric function accepts a sequence of prediction dictionaries after
split/prompt filtering, so the records are structurally compatible, but the
current evaluator does not provide an injection point for them.
Sources: [scoreable result interface](../src/recovery_ledger.py#L79-L100)
and [builder](../src/recovery_ledger.py#L826-L841);
[evaluator input path](../src/evaluate.py#L170-L209);
[metrics input contract](../src/metrics.py#L237-L253).

**RECOMMENDED:** Load an immutable snapshot of original records and ledger
events, run the resolver, then pass only `ScoreableResults.records` to a
separate scoring adapter. Do not write resolver output into the original
`ResultsStore`. Retain `diagnostics` in a sidecar or the versioned evaluation
package; do not discard uncertainty or conflict information.

### Identity, duplicates, expected observations, and runs

**VERIFIED:** Store identity is
`split|record_id|prompt_id|run`; the evaluator's per-prompt scorer keys
predictions by `(record_id, run)` and errors on duplicate pairs that survive
its gold/run filters. The current evaluator derives a run range from the
maximum run across all records in a split and reuses it for each prompt.
Sources: [store identity](../src/results_store.py#L28-L33);
[metrics duplicate and alignment behavior](../src/metrics.py#L258-L294);
[evaluator run derivation](../src/evaluate.py#L57-L69)
and [per-prompt scoring](../src/evaluate.py#L193-L209).

**RECOMMENDED:** Before scoring, validate uniqueness against a canonical
observation plan and fail closed on duplicate or mismatched identities; do
not silently deduplicate. Derive expected observation keys and run numbers
from the approved plan, not from whatever records happen to be present or
from recovery-added rows. Keep split and prompt dimensions explicit in the
adapter before passing records to the scorer.

### Original store and historical metrics

**VERIFIED:** `ResultsStore.records` is publicly mutable, `add_record()`
retains record objects by reference, and its normal API rejects an existing
key. Its save method replaces one file via a temporary file, but does not
coordinate writers. The evaluator writes fixed `metrics.json` and
`results.csv` paths sequentially in `"w"` mode, with no versioned output or
rollback. Sources: [store mutability and insertion](../src/results_store.py#L35-L44)
and [add_record](../src/results_store.py#L84-L95);
[store save](../src/results_store.py#L97-L129);
[evaluator writes](../src/evaluate.py#L223-L232).

**RECOMMENDED:** Preserve the original ResultsStore byte-for-byte during
recovery-aware evaluation. Use a separate input/output directory or
version-specific filenames. Record the source result snapshot identity,
ledger snapshot identity, expected plan, selection decisions, and policy
version. Do not overwrite historical metrics; publish a new evaluation
version only after all required files are complete and validated.

**RECOMMENDED:** Keep unresolved-provider-outcome IDs, selection statuses,
conflicts, excluded keys, and missing planned observations visible in
diagnostics even when their records are not scoreable. A score-only metrics
table is not a substitute for these diagnostics.
Source: [resolver diagnostics shape](../src/recovery_ledger.py#L79-L100)
and [diagnostic construction](../src/recovery_ledger.py#L808-L824).

## 7. Persistence and failure handling

### ResultsStore-specific limits

**VERIFIED:** `ResultsStore.add_record()` stores the original dictionary
reference, and `records` is a public mutable mapping. A post-insertion change
to record identity can make the record disagree with the mapping key; save
does not revalidate the mapping before serialization. Sources:
[store initialization](../src/results_store.py#L35-L44);
[insertion](../src/results_store.py#L84-L95);
[save serialization](../src/results_store.py#L97-L125).

**RECOMMENDED:** The future adapter must work on copies and must not mutate
the original store or its records. Treat the loaded original snapshot as
read-only, validate every key again at the adapter boundary, and stop if a
key/record identity mismatch is found.

**VERIFIED:** `ResultsStore` has no concurrent-writer lock. Its save method
uses a same-directory temporary file, file fsync, `os.replace()`, and
directory fsync. This is per-file replacement behavior, not a transaction
spanning result, ledger, or metrics files; failures after replace can still
raise after the new destination has been installed.
Sources: [save ordering](../src/results_store.py#L97-L129);
[Phase B.1 concurrency analysis](./phase_b1_persistence_evaluation_audit.md#L143-L183).

**RECOMMENDED:** Until concurrency control is separately designed, allow one
authorized writer for a given result-store path at a time. If a save reports
an error, do not assume the previous file is still present or that the new
file is fully durable; stop, inspect through an approved offline recovery
procedure, and reconcile before retrying.

### Evaluator, ledger, and cross-file failures

**VERIFIED:** The evaluator writes `metrics.json` and then `results.csv` using
direct writes to fixed paths. The two outputs can be left at different
versions if the second write fails. The recovery ledger appends events under
its own lock and fsync protocol; neither mechanism provides a cross-file
transaction with ResultsStore or evaluator outputs.
Sources: [evaluator output order](../src/evaluate.py#L223-L232);
[ledger append transaction](../src/recovery_ledger.py#L647-L715);
[documented ledger limits](../RECOVERY_LEDGER.md#L52-L79).

**RECOMMENDED failure handling:**

1. If `recovery_started` append fails, do not call the provider.
2. If the provider call may have started but no result can be durably
   recorded, leave the recovery incomplete and uncertain.
3. If a result is durably appended to the recovery ledger but later evaluation
   writing fails, keep the result and selection state unchanged; do not repeat
   the provider call. Rebuild a new evaluation version from snapshots only
   after reconciliation.
4. If one evaluation output is written and a later output fails, mark that
   evaluation version incomplete and do not publish it as authoritative.
5. Never infer that a failed local write means no provider processing or that
   the operation is safe to retry.

**INFERRED:** Because provider execution, ResultsStore replacement, ledger
append, and evaluation output writes are separate operations, failures between
them can leave different durable states. No cross-file exactly-once or
transactional guarantee is established.
Sources: [ResultsStore save](../src/results_store.py#L97-L129);
[ledger append](../src/recovery_ledger.py#L647-L715);
[evaluator writes](../src/evaluate.py#L223-L232).

## 8. Decision register

Every row is marked **APPROVED — USER ACCEPTED** under the Phase B.3 approval
record. The accepted defaults and their rationale and consequences are
preserved below.

| Decision ID | Question | Recommended default | Reason | Consequence of choosing differently | Status |
|---|---|---|---|---|---|
| B2-D01 | Which observations are eligible for recovery? | Require a specific authoritative planned key and human approval; no automatic recovery from absence or failure. | `has_key()` checks presence, not provider outcome; the current runner does not log ordinary request starts. [Source](../src/results_store.py#L81-L82) [Source](../src/experiment_runtime.py#L127-L145) | Broader eligibility may repeat provider work or billing when an earlier request was processed. | APPROVED — USER ACCEPTED |
| B2-D02 | What defines a `successful_result`, and what additional conditions define a `scoreable_result`? | `successful_result` requires parse true, schema true, parsed JSON object, error null, and exact identity to the authoritative plan. `scoreable_result` additionally requires the applicable selection policy (an original may remain selected; a recovery requires explicit selection) and conflict-free evaluation eligibility. | This separates technical response validity from evaluation selection and aligns the error check with the resolver. [Source](../src/recovery_ledger.py#L719-L726) [Source](../src/recovery_ledger.py#L755-L824) | Conflating the concepts can score unselected recoveries or treat an error-marked record as technically successful. | APPROVED — USER ACCEPTED |
| B2-D03 | Who may authorize provider recovery? | Designated human experiment owner or explicitly delegated human; no automatic or agent self-authorization. | Recovery can have duplicate-processing/billing consequences; current ledger provenance records actor/reference only. [Contract](../RECOVERY_LEDGER.md#L7-L26) | Broader authority weakens accountability; narrower authority may delay recovery. | APPROVED — USER ACCEPTED |
| B2-D04 | What provenance is required? | Actor identifier plus reference to a durable approval record naming key, reason, attempt bound, and risk acceptance; no secrets or payloads. | Existing provenance schema is constrained and intentionally excludes sensitive/raw content. [Contract](../RECOVERY_LEDGER.md#L7-L26) | Free-form or missing provenance reduces auditability and may expose sensitive data. | APPROVED — USER ACCEPTED |
| B2-D05 | What happens when `recovery_started` cannot be appended? | Stop before any provider call; surface the error. | The ledger has no unlocked fallback and the event is the audit boundary. [Lock contract](../RECOVERY_LEDGER.md#L52-L60) | Proceeding would create a request with no corresponding durable start record. | APPROVED — USER ACCEPTED |
| B2-D06 | What evidence permits adjudication and another attempt? | Require human review, cited evidence, and explicit acceptance that the prior request may have been processed/billed; retain its uncertainty. | Adjudication authorizes another attempt but does not resolve provider uncertainty. [Contract](../RECOVERY_LEDGER.md#L81-L92) | Treating adjudication as proof of non-execution can cause unsafe retries and false audit claims. | APPROVED — USER ACCEPTED |
| B2-D07 | How should a truly missing original be represented? | Exclude it under the current interface until an authoritative plan and a separately approved typed representation exist; never fabricate a result record. | Resolver rejects recovery events whose keys are absent from `original_records`. [Source](../src/recovery_ledger.py#L728-L751) | Invented placeholders can be mistaken for actual requests/results; excluding them may leave planned work unresolved. | APPROVED — USER ACCEPTED |
| B2-D08 | Who selects a recovery for evaluation, and on what evidence? | Require a designated evaluation reviewer and a reference to the exact completed recovery, key, rationale, and evaluation version. | Selection is explicit, unique per observation, and separate from uncertainty. [Source](../src/recovery_ledger.py#L262-L291) | Automatic selection can admit unreviewed output or obscure uncertainty. | APPROVED — USER ACCEPTED |
| B2-D09 | What if there are multiple successful recoveries? | Mark conflict; score none until a separately approved review contract exists. | Current resolver excludes two or more successful recoveries even if a selection exists. [Source](../src/recovery_ledger.py#L755-L790) | Choosing one automatically may silently resolve competing outputs. | APPROVED — USER ACCEPTED |
| B2-D10 | What if one recovery differs from a successful original? | Preserve and score the original; flag discrepancy; do not select or overwrite with recovery. | Resolver keeps the original but does not compare a single recovery against it. [Source](../src/recovery_ledger.py#L762-L790) | Permitting recovery selection changes the historical prediction and requires an explicit adjudication policy. | APPROVED — USER ACCEPTED |
| B2-D11 | Can selection be revoked or superseded? | No, not under the current ledger schema; any future revocation requires a new append-only contract and version. | Current schema permits one selection decision and has no revocation event. [Source](../src/recovery_ledger.py#L27-L60) [Source](../src/recovery_ledger.py#L262-L291) | Rewriting history or allowing silent replacement undermines auditability. | APPROVED — USER ACCEPTED |
| B2-D12 | What authoritative plan defines expected observations and runs? | Identify and approve an immutable plan artifact that establishes expected observation keys (split, record ID, prompt ID, run), expected run coverage, relevant prompt/configuration identity, and a stable snapshot identity or verifiable hash. Validate unique keys; do not derive expected coverage from recovery-augmented records. | The evaluator currently derives a split-wide run range from stored results, and the permitted documents do not identify an authoritative plan artifact or integrity mechanism. [Source](../src/evaluate.py#L57-L69) [Plan-artifact limitation](./phase_b1_persistence_evaluation_audit.md#L392-L425) | Without an approved plan and integrity check, missing/duplicate observations or mixed run coverage can distort denominators and recovery eligibility. | APPROVED — USER ACCEPTED |
| B2-D13 | How are evaluation outputs preserved? | Write a separate versioned output set with provenance; never overwrite historical metrics for recovery-aware evaluation. | Current evaluator writes fixed paths in `"w"` mode, sequentially. [Source](../src/evaluate.py#L223-L232) | Reusing existing paths destroys prior metrics and can leave JSON/CSV inconsistent. | APPROVED — USER ACCEPTED |
| B2-D14 | What concurrency policy applies to result writes? | One authorized writer per ResultsStore path until a separately designed locking/merge protocol is approved. | ResultsStore has no concurrent-writer coordination and replaces full snapshots. [Source](../src/results_store.py#L97-L129) | Concurrent saves can lose updates. | APPROVED — USER ACCEPTED |
| B2-D15 | What does an error-marked but otherwise parse-valid record mean? | Treat it as not successful for recovery-aware scoring; require explicit review for inconsistent records. | Metrics currently ignores `error`, while the resolver requires it to be null. [Source](../src/metrics.py#L44-L60) [Source](../src/recovery_ledger.py#L719-L726) | Retaining current metric behavior may score results explicitly marked as errors. | APPROVED — USER ACCEPTED |

## 9. Implementation gates

These are prerequisites, not permissions. Approval of the policy decisions
does not satisfy every gate or authorize an implementation phase.

### Phase C — Offline contract tests

**RECOMMENDED entry criteria:**

- User decisions B2-D01 through B2-D15 are approved.
- The approved success definition and current missing-original exclusion
  behavior are fixed for testing. Phase C may test that exclusion using
  synthetic records; no typed missing-original representation is approved or
  implemented.
- Selection, conflict, revocation, and adjudication contracts are documented.
- Tests use temporary directories and synthetic records only.

**RECOMMENDED exit criteria:** Offline tests prove exact identity validation;
no start append means no provider call in later adapters; incomplete starts
remain uncertain; adjudication does not clear uncertainty; unselected and
conflicted recoveries are excluded; originals remain unchanged; malformed
inputs fail closed; and diagnostics remain available.

### Phase D — Fake-provider integration tests

**RECOMMENDED entry criteria:** Phase C contracts pass; a narrow recovery
adapter interface is approved; no live credentials or real endpoints are
configured or used.

**RECOMMENDED scope:** Use an injected fake provider, temporary result/ledger
paths, synthetic experiment plans, and explicit failpoints before request,
after submission, after response, during ledger persistence, and between
evaluation outputs. Verify restart, duplicate-start, and concurrent-call
behavior without touching project outputs.

**RECOMMENDED exit criteria:** No real provider request occurs; every
ambiguous outcome remains uncertain; failed persistence does not trigger an
automatic retry; and the original ResultsStore snapshot remains unchanged.

### Phase E — Controlled live recovery

**RECOMMENDED entry criteria:** Separate explicit user authorization names the
exact observation keys, request bound, operator, provider/model settings,
allowed retry classes, accepted billing/duplicate risk, stop conditions,
provenance reference, and output location. Confirm reconciliation and
monitoring procedures before the first call.

**RECOMMENDED scope:** Only the approved bounded request(s), with a durable
start event before each call and no automatic retry after uncertain outcomes.
Stop on an unexpected response, identity/lock error, append/save failure,
budget ambiguity, or mismatch with the approved plan.

**Separate approval gate:** Phase E authorization does not authorize use of a
recovery in evaluation or modification of historical metrics.

### Phase F — Evaluation integration

**RECOMMENDED entry criteria:** Phase C and D gates pass; success, missing
original, expected-run, duplicate, conflict, and selection policies are
approved; the recovery-aware adapter's enforcement of the approved `error`
rule is specified without silently changing existing evaluator behavior;
output versioning and historical preservation are specified.

**RECOMMENDED scope:** Integrate through `build_scoreable_results()` and a
separate scoring adapter. Preserve diagnostics and original results; produce
versioned outputs with plan/result/ledger provenance. Do not merge recovery
records into the ordinary ResultsStore.

**Separate approval gate:** Evaluation integration requires explicit user
approval independent of any Phase E live-recovery approval.

## 10. Validation and preservation statement

- **Phase B.2 creation — exact documents read:**
  `reports/phase_b_recovery_integration_design.md`,
  `reports/phase_b1_persistence_evaluation_audit.md`, and `RECOVERY_LEDGER.md`.
- **Phase B.2.1 correction — exact existing document read:**
  `reports/phase_b2_recovery_protocol_specification.md`.
- **Phase B.2 creation document searches:** Heading and policy searches were
  performed in the three permitted documents listed above.
- **Phase B.2 creation source-symbol searches:** The original execution
  transcript reports source-symbol searches against Python files, including
  recovery-ledger functions. The exact search terms and whether those searches
  returned source content are not independently established here.
- **Phase B.2 creation source-file reading:** The transcript does not
  independently establish whether any Python source file was opened and read;
  searching for symbols is not treated as proof that a file was read.
- **Phase B.2.1 correction searches:** Text searches were performed only in
  this existing specification to locate and verify the requested policy
  sections and decision rows.
- **Phase B.2.3 provenance correction:** The report was corrected to record
  the transcript-reported Python source-symbol searches, including searches
  for recovery-ledger functions, and to mark search-result contents and
  source-file reading as not independently established.
- **Application code execution:** None during Phase B.2 creation or the
  Phase B.2.1, B.2.3, and B.3 documentation updates.
- **File created during Phase B.2:** `reports/phase_b2_recovery_protocol_specification.md`.
- **Files created during Phase B.2.1:** None.
- **Phase B.3 approval:** On 2026-11-14, the user accepted all 15 recommended
  defaults, B2-D01 through B2-D15, with no additional conditions. The
  existing report was updated to record approval; implementation remains
  unauthorized.
- **Tests:** None executed during these documentation-only phases.
- **Runner/evaluator:** Not executed.
- **Credentials, `.env`, held-out data, datasets, and experiment-output
  contents:** Not accessed.
- **Retry log:** No retry-log contents were inspected. The duplicate
  attempt-number anomaly is treated as a previously identified finding; it was
  not independently reconciled during Phase B.2.
- **Network/API:** No requests made.
- **Implementation:** None performed. During the original Phase B.2 creation,
  no existing files were modified. During this Phase B.2.1 correction, the
  only modified file was this specification. The B.2.3 provenance correction
  and the B.3 approval/update also modified only this existing specification;
  no implementation files were changed.
- **Unresolved implementation questions (not pending policy decisions):**
  How to implement and test the missing-original exclusion boundary; how a
  future scoring adapter will enforce the approved `error is None` rule
  without silently changing historical evaluator behavior; how diagnostics
  and metrics will be versioned and retained; and how evidence references
  will be validated during adjudication. All 15 policy decisions remain
  **APPROVED — USER ACCEPTED**. Implementation authorization remains separate
  and has not been granted.
