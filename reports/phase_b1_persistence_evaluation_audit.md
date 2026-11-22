# Phase B.1 — Read-Only Persistence and Evaluation Architecture Audit

## 1. Executive summary and verdict

**VERIFIED:** `ResultsStore` is implemented in `src/results_store.py`. It stores
one JSON document with `meta` and `records`; `records` is a dictionary indexed
by the four-part identity returned by `result_key()`. `has_key()` checks only
dictionary membership. `add_record()` rejects a duplicate key through its
public API, while `save()` writes a same-directory temporary file, fsyncs it,
replaces the destination, and fsyncs the containing directory.

**VERIFIED:** The evaluator entry point is `src/evaluate.py`. It loads
`outputs/raw_responses.json` through `ResultsStore`, reads the selected gold
split from the configured data directory, filters predictions by split and
prompt, then calls `src.metrics.score_predictions()`. Missing predictions are
aligned against every expected gold-record/run pair and counted; they are not
a fatal evaluation error. The evaluator writes `metrics.json` and `results.csv`
with direct `"w"` opens, so a later evaluation of another split overwrites
those same paths.

**VERIFIED:** The recovery resolver's `records` tuple has the result-record
shape accepted by `score_predictions()` after the evaluator's split/prompt
selection. But `evaluate_split()` hardcodes `ResultsStore.load()` and does not
call `build_scoreable_results()`. The evaluator also has no place in its
current output structure for the resolver's per-observation diagnostics.

**DEFECT FOUND:** The metrics usability predicate checks parse/schema flags and
`parsed_json`, but not `error`. Since the result-store schema accepts an
error-marked record with those flags set, the evaluator can score it as usable.
`ResultsStore` also retains inserted record dictionaries by reference and does
not revalidate the records in `save()`; mutation after insertion can make a
record's fields disagree with its dictionary key and write a file that a later
`load()` rejects.

**VERDICT — DO NOT MERGE RECOVERY RECORDS DIRECTLY INTO THE ORDINARY RESULTS
STORE.** The current scoring function can accept resolver records, but the
evaluator does not use the resolver, and direct store mutation would bypass
selection/conflict rules and the resolver diagnostics. A future adapter should
score a separate, explicitly selected record sequence, preserve the original
store, retain diagnostics separately, and write versioned evaluation outputs.
This audit does not authorize implementation or live recovery.

## 2. Files inspected and scope limitations

### Files inspected

- `reports/phase_b_recovery_integration_design.md` — initial reference.
- `src/run_experiment.py` — confirmed the runner's import and use of
  `ResultsStore`.
- `src/results_store.py` — result schema, indexing, load/add/save, and the
  directly relevant `migrate_observations()` helper.
- `src/evaluate.py` — evaluator entry point, input filtering, scoring calls,
  and output writes.
- `src/metrics.py` — score functions and aggregation.
- `src/config.py` — data, output, prompt, and price-setting paths/definitions.
- `src/validate_dataset.py` — gold-record validation interface and label
  schema.
- `src/recovery_ledger.py` — result validation, resolver, and scoreable-results
  return shape.
- `RECOVERY_LEDGER.md` — documented evaluation selection and scoreable-results
  contract.

**VERIFIED:** `src/evaluate.py` and `src/metrics.py` were discovered from the
imports and symbol references in the `src` source tree, not assumed from a
filename. References to line numbers below are source-code references; no data
or output contents were opened.

### Scope limitations

**NOT VERIFIED:** The contents, version, hashes, and label correctness of any
gold dataset were not inspected. Source code shows that the evaluator can
select a split and constructs its gold path from that split, but this audit
does not establish the contents of `data/dev.jsonl` or `data/test.jsonl`.
Sources: [gold loading path](../src/evaluate.py#L24-L43);
[configured data path](../src/config.py#L9-L14).

**NOT VERIFIED:** No runtime behavior, dependency-version behavior, file-system
failure behavior, or live evaluator output was tested. The conclusions below
are based on source inspection only.

## 3. ResultsStore implementation and persistence semantics

### Location, format, identity, and load behavior

**VERIFIED:** `ResultsStore` is defined in `src/results_store.py`. The default
path is `outputs/raw_responses.json`. The document has exactly two top-level
fields, `meta` and `records`; records are represented as a JSON object whose
property names are result keys. Each record must have the fields in
`RECORD_FIELDS`.

Sources: [default path and record schema](../src/results_store.py#L9-L26);
[class and load implementation](../src/results_store.py#L35-L79).

**VERIFIED:** `result_key(record)` concatenates `split`, `record_id`,
`prompt_id`, and `run` with `|`. On load, the store requires each record's
computed key to match its object key. It checks the top-level structure, exact
record-field set, key match, and non-negative integer form for
`reasoning_tokens`; the displayed loader does not perform equivalent
type/range validation for every other record field.

Sources: [key construction](../src/results_store.py#L28-L33);
[load validation](../src/results_store.py#L46-L79).

**VERIFIED:** If the path does not exist, `load()` returns an empty store.
Otherwise it reads and parses the JSON document and raises `ValueError` for
unreadable/malformed JSON or invalid top-level and record structures. The
loaded `meta` and records are retained in memory.

Source: [load behavior](../src/results_store.py#L46-L79).

### `has_key()`, failed records, and duplicates

**VERIFIED:** `has_key(key)` is exactly `key in self.records`; it does not
inspect a record's `error`, `parse_ok`, or `schema_ok` fields. Thus an
error-marked record is present if its identity is in the dictionary.

Source: [membership check](../src/results_store.py#L81-L82).

**VERIFIED:** `add_record()` checks the exact field set and the
`reasoning_tokens` constraint, derives the key, raises `ValueError` if the key
already exists, then stores the supplied record object under that key. It does
not replace an existing record through this method.

Source: [record insertion and duplicate rejection](../src/results_store.py#L84-L95).

**VERIFIED:** Through `add_record()`, duplicate keys cannot be inserted into a
single store instance. Persisted storage is a JSON object, which has no
first-class duplicate-key representation. However, the loader calls
`json.loads()` without a duplicate-property rejecting hook. Under Python's
standard JSON decoder behavior, repeated object member names are accepted and
the later value replaces the earlier value during decoding. Therefore a
hand-edited or otherwise malformed JSON document can contain duplicate
`records` properties that are silently collapsed before the store's key
validation.

Source: [default JSON parsing and subsequent key validation](../src/results_store.py#L46-L73).

**DEFECT FOUND:** Persisted duplicate JSON object member names are not
explicitly rejected. This weakens the store's claim to unambiguous
key-to-record loading: the validator sees only the decoder's surviving value,
not the duplicate entries from the source text.

### `save()`, atomicity, failure, locking, and durability

**VERIFIED:** `save()` creates the parent directory if needed, creates a
temporary file in the destination directory, serializes `{"meta": ..., "records":
...}`, appends a newline, flushes and fsyncs the temporary file, calls
`os.replace(temp_path, self.path)`, then fsyncs the parent directory. A `finally`
block removes the temporary file if it still exists.

Source: [save implementation](../src/results_store.py#L97-L129).

**VERIFIED:** This is a temporary-file-and-replace pattern for one destination
path. It attempts to make the individual path replacement atomic on the
underlying filesystem and requests file and directory fsyncs. It does not make
the results store and metrics outputs one transaction, nor does the source
establish universal power-loss or filesystem durability.

Source: [temporary file, fsync, replace, and directory fsync](../src/results_store.py#L101-L125).

**VERIFIED:** If serialization or temporary-file fsync fails before
`os.replace()`, the replacement has not yet been attempted; the `finally`
cleanup removes a still-existing temporary file when normal exception
unwinding reaches it. If directory fsync fails after `os.replace()`, the
destination may already contain the new document even though `save()` raises.
An abrupt process termination can bypass Python cleanup. These outcomes follow
from the ordering of operations; they were not induced in a test.

Source: [save ordering and cleanup](../src/results_store.py#L97-L129).

**VERIFIED:** `ResultsStore` contains no file lock or inter-process
coordination. **INFERRED:** Two processes that load separate snapshots, add
different records, and save concurrently can overwrite each other's updates:
each save serializes its own full in-memory `records` mapping and replaces the
same path; the last replacement wins.

Sources: [in-memory store initialization](../src/results_store.py#L35-L44);
[full-snapshot save](../src/results_store.py#L97-L125).

**NOT VERIFIED:** The code does not establish a stronger guarantee for
concurrent writers or for non-local filesystems. No locking protocol is
implemented in this class.

### Record mutability and direct persistence helper

**VERIFIED:** `records` is a public mutable dictionary. `add_record()` stores
the exact supplied dictionary object rather than a copy. `load()` also stores
decoded dictionaries directly. `save()` serializes the current public
dictionary without revalidating that each record still matches its dictionary
key.

Sources: [public mapping initialization](../src/results_store.py#L35-L44);
[insertion by reference](../src/results_store.py#L84-L95);
[load assignment](../src/results_store.py#L68-L79);
[save serialization](../src/results_store.py#L109-L117).

**DEFECT FOUND:** Mutating an inserted record's identity fields can make its
new `result_key(record)` disagree with the old `self.records` key. `save()`
will serialize that mismatch; a later `load()` will reject the record because
the persisted property name no longer matches the record fields. Direct
mutation of `store.records` can also bypass `add_record()` validation.

Sources: [key construction](../src/results_store.py#L28-L33);
[reference storage](../src/results_store.py#L84-L95);
[load-time identity check](../src/results_store.py#L68-L73);
[save without validation](../src/results_store.py#L97-L125).

**VERIFIED:** The module also defines `migrate_observations()`. It translates
legacy observation fields into a result record and inserts each through
`store.add_record()`, so duplicate migrated identities follow the same
duplicate rejection path. A source search found only its definition in `src`;
no in-`src` caller was found.

Source: [migration helper](../src/results_store.py#L131-L162).

## 4. Evaluator input contract and scoring behavior

### Entry point and inputs

**VERIFIED:** The evaluator entry point is `src/evaluate.py:main()`. Its CLI
requires `--split` to be either `dev` or `test` and invokes `evaluate_split()`.
This audit inspected only the source path and did not open either dataset.

Sources: [evaluator entry point](../src/evaluate.py#L239-L251);
[gold file path construction](../src/evaluate.py#L24-L43).

**VERIFIED:** `load_gold_records(split)` reads `config.DATA_DIR / f"{split}.jsonl"`,
validates each decoded record with `validate_record()`, and transforms it into
`record_id`, `label`, and `original_message`. Dataset labels must contain the
declared set of label fields; source validation includes claim type and other
label checks.

Sources: [gold loading and transformation](../src/evaluate.py#L24-L43);
[label field schema](../src/validate_dataset.py#L26-L38);
[record and label validation](../src/validate_dataset.py#L208-L250).

**VERIFIED:** For `dev`, `load_dev_example_ids()` reads prompt files for
`B-k3`, `B-k5`, and `B-k8`, extracts `DEV-<number>` example IDs, and the
evaluator produces both an `all` view and a `dev_eval` view that excludes
those IDs. The `test` path in source creates only the `all` view. Prompt file
contents and datasets were not opened for this audit.

Sources: [dev example ID extraction](../src/evaluate.py#L46-L55);
[view construction](../src/evaluate.py#L176-L189).

**VERIFIED:** `evaluate_split()` loads `ResultsStore` from `RESULTS_PATH`,
converts `store.records.values()` to a list, and derives `run_numbers` from all
loaded result records for the selected split. `expected_run_numbers()` returns
the range `1..max(run)` and defaults to `[1]` when there are no result records
for that split.

Sources: [store load and evaluator preparation](../src/evaluate.py#L170-L175);
[run-number derivation](../src/evaluate.py#L57-L69).

### Prediction filtering, errors, duplicates, and missing observations

**VERIFIED:** For each prompt in `PROMPT_FILES`, the evaluator selects
prediction records matching the requested split and prompt ID, then passes
them with the gold view and split-wide `run_numbers` to
`score_predictions()`. The evaluator's filter does not check `error`,
`parse_ok`, or `schema_ok`; those fields are handled by the metric function.

Sources: [prediction filter and score call](../src/evaluate.py#L193-L209);
[metrics usability predicate](../src/metrics.py#L44-L60).

**VERIFIED:** `score_predictions()` requires unique gold `record_id` values.
It keys predictions by `(record_id, run)`, ignores predictions whose IDs are
not in the gold set or whose run is not expected, and raises `ValueError` for
a duplicate key among the remaining predictions.

Source: [gold and prediction indexing](../src/metrics.py#L258-L270).

**VERIFIED:** The scorer does not require a complete prediction set. It builds
the expected grid of every gold record crossed with every run number, leaves
missing entries as `None`, and counts them in `missing_predictions`.
`evaluate_split()` prints a warning for missing entries but continues to
produce metrics.

Sources: [alignment and missing count](../src/metrics.py#L272-L282);
[warning and continued evaluation](../src/evaluate.py#L215-L221).

**VERIFIED:** A missing result and a result that fails `_is_usable_result()`
both become `INVALID_PREDICTION` for parsed fields. A missing result
increments `missing_predictions`; a present but unusable result does not.
Failed parses/schema results therefore remain part of the denominator rather
than being removed from scoring.

Sources: [usable-result and prediction conversion](../src/metrics.py#L44-L60);
[alignment and missing count](../src/metrics.py#L272-L295).

**VERIFIED:** `_is_usable_result()` requires `parse_ok is True`,
`schema_ok is True`, and `parsed_json` to be a dictionary. It does **not**
require `error is None`. `ResultsStore.add_record()` allows any string or null
for `error` by field presence alone; it does not enforce consistency between
`error` and the parse/schema flags.

Sources: [metric usability predicate](../src/metrics.py#L44-L51);
[store insertion validation](../src/results_store.py#L84-L95).

**DEFECT FOUND:** An error-marked result with true parse/schema flags and a
dictionary `parsed_json` is treated as usable by the evaluator. The recovery
resolver uses a stricter success predicate that also requires `error is None`.

Sources: [metric usability predicate](../src/metrics.py#L44-L51);
[recovery success predicate](../src/recovery_ledger.py#L719-L726).

**VERIFIED:** The evaluator compares predictions to labels loaded from the
selected gold split, not to labels embedded in prediction records. The actual
gold file contents/version were not examined, so their correctness or
stability is **NOT VERIFIED**.

Sources: [gold loading](../src/evaluate.py#L24-L43);
[scoring call](../src/evaluate.py#L197-L209).

**VERIFIED:** The evaluator and metrics functions read prediction/gold
records into local lists and dictionaries but do not assign into or mutate
those record objects. `evaluate_split()` adds split/prompt/view fields to the
new metrics result dictionary, not to the source records.

Sources: [evaluation data flow](../src/evaluate.py#L170-L212);
[metrics alignment and result construction](../src/metrics.py#L258-L294)
and [metrics return construction](../src/metrics.py#L399-L444).

## 5. Metrics calculations and historical-output behavior

### Aggregation and denominators

**VERIFIED:** `score_predictions()` defines `expected_predictions` as
`len(gold_records) * len(run_numbers)`. Missing count is the number of absent
entries in that full grid. Classification accuracy and field exact/lenient
match rates use the aligned expected rows as their denominator. Classification
macro-F1 uses fixed claim-type/urgency label sets with `zero_division=0`;
per-field macro-F1 uses the union of gold values and valid predicted values.

Sources: [labels and metric helpers](../src/metrics.py#L22-L42)
and [classification/field metrics](../src/metrics.py#L63-L130);
[expected grid and field denominators](../src/metrics.py#L272-L329).

**VERIFIED:** A parse/schema failure or missing result supplies
`INVALID_PREDICTION` to field and classification scoring. `parse_ok_rate` and
`schema_ok_rate` each divide the count of their respective flag being exactly
true by `expected_predictions`; missing entries count as false for these rates.
An empty expected grid yields `None` for those rates.

Sources: [invalid-prediction handling](../src/metrics.py#L44-L60);
[rates and denominators](../src/metrics.py#L404-L414).

**VERIFIED:** Hallucination counts use seven `FIELD_NAMES`. An invalid
prediction or a null prediction is not counted as hallucinated; a non-null
prediction for a null gold field is counted; identifier predictions are
checked against the original message. Each field's rate divides by
`expected_predictions`; overall rate divides by
`expected_predictions * len(FIELD_NAMES)`.

Sources: [field names](../src/metrics.py#L31-L39);
[hallucination predicate](../src/metrics.py#L166-L181);
[hallucination denominators](../src/metrics.py#L301-L351).

**VERIFIED:** High-urgency recall divides true positives by the number of
gold high-urgency rows; injury recall divides true positives by the number of
gold rows with `injury_reported is True`. Missing or invalid predictions count
as false negatives. A zero positive denominator yields `None`.

Source: [safety-critical counts and recall](../src/metrics.py#L356-L442).

**VERIFIED:** Usage means include only present, finite, non-negative numeric
values and exclude booleans. Missing predictions and records without valid
usage fields do not contribute to those means. Cost per 1,000 claims is `None`
unless both input/output prices and mean input/output token counts exist; when
defined, the calculation uses those means, `len(run_numbers)`, and the
configured prices.

Sources: [numeric means](../src/metrics.py#L183-L197);
[usage and cost calculation](../src/metrics.py#L374-L390).

**VERIFIED:** Run-to-run agreement is `None` when fewer than two runs or no
gold records are supplied. For each gold record, it increments agreement only
when all expected runs have usable results; its final denominator is the
number of gold records, including rows skipped because one or more runs were
unusable.

Source: [run agreement](../src/metrics.py#L199-L235).

**INFERRED:** For fixed gold and prediction inputs, run numbers, prices, code,
and dependency versions, the calculations contain no random sampling and
should be deterministic. Cross-version or cross-platform numerical identity
is **NOT VERIFIED**; no deterministic-output test was run.

Sources: [ordered/deduplicated run numbers and alignment](../src/metrics.py#L250-L294);
[metric computations](../src/metrics.py#L294-L444).

### Output paths, replacement, and provenance

**VERIFIED:** `evaluate_split()` writes to fixed paths under `config.OUTPUTS_DIR`:
`metrics.json` and `results.csv`. Both are opened with mode `"w"`, so a new
evaluation truncates/replaces the prior contents at each path. The writes are
sequential: JSON first, CSV second. They use no temporary-file-and-replace
pattern, lock, append mode, or versioned filename.

Source: [metrics output creation and writes](../src/evaluate.py#L223-L232).

**VERIFIED:** If the first write succeeds and the second fails, the JSON output
may describe the new evaluation while the CSV remains old, truncated, or
partial. If a write is interrupted, the source contains no rollback or
preservation mechanism. No such failure was induced.

Source: [sequential direct writes](../src/evaluate.py#L223-L232).

**VERIFIED:** The detailed output object includes the selected split,
run-number list, excluded development example IDs, and per-view/per-prompt
metrics. The shown output construction does not include a result-store
content hash, result metadata, recovery-ledger identity, selected recovery
IDs, or evaluation version. The evaluator loads `ResultsStore` without
comparing its `meta` to the current prompt/model metadata.

Sources: [detail object construction](../src/evaluate.py#L181-L212);
[store load without metadata argument](../src/evaluate.py#L170-L175);
[output writes](../src/evaluate.py#L223-L232).

**DEFECT FOUND:** Existing evaluation artifacts are not preserved by the
evaluator's write path: evaluating a split writes to the same fixed filenames
and overwrites prior contents, with no version/provenance record or
cross-file transaction.

Source: [fixed paths and direct write mode](../src/evaluate.py#L223-L232).

## 6. Compatibility analysis with `build_scoreable_results()`

### Shape and invocation

**VERIFIED:** `ResultsStore.records` has type `dict[str, dict[str, Any]]`,
keyed by `result_key`. `build_scoreable_results()` accepts a mapping with the
same key-to-record shape and returns `ScoreableResults(records, diagnostics)`,
where `records` is a tuple of full result dictionaries. `score_predictions()`
accepts a sequence of prediction dictionaries containing at least
`record_id`, `run`, `parsed_json`, `parse_ok`, and `schema_ok`, plus optional
usage values. Thus the resolver's record tuple is structurally compatible
with the metric function after selection by split and prompt.

Sources: [ResultsStore mapping and key](../src/results_store.py#L28-L44);
[scoreable result types](../src/recovery_ledger.py#L79-L100);
[resolver result builder](../src/recovery_ledger.py#L826-L841);
[metric input contract](../src/metrics.py#L237-L253).

**VERIFIED:** The current evaluator does not accept a results mapping or record
sequence as an argument. `evaluate_split()` loads the ordinary `ResultsStore`
itself and does not call `build_scoreable_results()`. A separate adapter or
change to the evaluator's input boundary would be required to score resolver
output without writing it into the original store.

Sources: [evaluator function and store load](../src/evaluate.py#L170-L175);
[evaluator scoring path](../src/evaluate.py#L193-L209);
[resolver interface](../src/recovery_ledger.py#L826-L841).

### Identity, duplicates, and missing originals

**VERIFIED:** Both store and resolver use the four-component result key
(`split`, `record_id`, `prompt_id`, `run`). The resolver validates each
original dictionary key against its record identity and rejects recovery
events for keys absent from the `original_records` mapping. The evaluator
filters by split/prompt before `score_predictions()` keys records by
`(record_id, run)`; the scorer raises on duplicate pairs that survive its
gold/run filters.

Sources: [store key](../src/results_store.py#L28-L33);
[resolver original/event identity checks](../src/recovery_ledger.py#L728-L751);
[evaluator filtering](../src/evaluate.py#L197-L205);
[scorer duplicate handling](../src/metrics.py#L258-L270).

**INFERRED:** The resolver cannot currently resolve a ledger event for an
observation that has no original record in the supplied mapping. A future
adapter must decide whether to create a schema-valid original failure record
from an authoritative experiment plan or to extend the contract; it must not
invent an original result or infer one from a missing result file entry.

Source: [unknown-original rejection](../src/recovery_ledger.py#L739-L751).

### Failed originals, selected recoveries, and diagnostics

**VERIFIED:** `ResultsStore` keeps failed records in its mapping and
`has_key()` returns true for them. The resolver defines success more strictly
than the metrics function: it also requires `error is None`. If the original
is unsuccessful and there is no explicitly selected successful recovery, the
resolver returns no scoreable result for that observation. A valid selected
recovery is returned as a record copy; the original mapping is not mutated.

Sources: [store membership/insertion](../src/results_store.py#L81-L95);
[resolver success predicate and selection logic](../src/recovery_ledger.py#L719-L803);
[deep-copied resolver output](../src/recovery_ledger.py#L826-L841).

**VERIFIED:** `build_scoreable_results()` carries diagnostics separately from
the `records` tuple. Those diagnostics include provider-outcome uncertainty,
selection status, successful recovery IDs, conflicts, and explanatory text.
The current evaluator stores per-prompt metrics but does not call the resolver
or add those diagnostics to its `metrics.json` detail.

Sources: [diagnostic type](../src/recovery_ledger.py#L79-L100);
[resolution fields](../src/recovery_ledger.py#L808-L824);
[builder](../src/recovery_ledger.py#L826-L841);
[evaluator detail/write path](../src/evaluate.py#L181-L212).

**VERIFIED:** Calling `ResultsStore.add_record()` for a recovery with the same
identity as an existing original raises a duplicate-key error; it does not
replace the original. But the public `records` dictionary can be assigned
directly, and the evaluator does not check a recovery selection event or
conflict status.

Sources: [duplicate rejection and public mapping](../src/results_store.py#L35-L44)
and [add_record](../src/results_store.py#L84-L95);
[evaluator prediction selection](../src/evaluate.py#L193-L209).

**INFERRED:** If recovery records are merged by overwriting or directly
assigning into `store.records`, or supplied to the evaluator through an
adapter that ignores `diagnostics`, usable unselected or conflicted recovery
records could be scored as ordinary predictions. If both an original and a
recovery for the same `(record_id, run)` are instead passed to
`score_predictions()`, it raises for the duplicate rather than selecting one.

Sources: [public mapping](../src/results_store.py#L35-L44);
[evaluator lacks ledger filtering](../src/evaluate.py#L193-L209);
[metrics duplicate rejection and usability](../src/metrics.py#L258-L270)
and [usable-result predicate](../src/metrics.py#L44-L60).

**VERIFIED:** The evaluator derives a single run-number range from all result
records in a split, then applies it to every prompt. **INFERRED RISK:** If
records or resolver outputs have different run coverage across prompts, a
recovery record with a higher run number can enlarge the expected grid for
other prompts and increase their missing counts. A future adapter should use
the approved experiment plan for expected runs rather than derive it from a
recovery-augmented result list.

Sources: [split-wide run derivation](../src/evaluate.py#L57-L69)
and [same run range used per prompt](../src/evaluate.py#L170-L205).

## 7. Concrete defects and integration risks

1. **DEFECT FOUND — error status is not part of evaluator usability.** The
   metric predicate ignores `error`, while store validation permits a
   non-null error alongside true parse/schema flags and parsed JSON. Such a
   record can be scored as valid. Sources:
   [metric predicate](../src/metrics.py#L44-L51);
   [store insertion checks](../src/results_store.py#L84-L95).
2. **DEFECT FOUND — mutable records can invalidate stored identities.**
   `add_record()` stores by reference and `save()` does not revalidate keys.
   A changed identity can be written under a stale key and rejected on reload.
   Sources: [insertion](../src/results_store.py#L84-L95);
   [save](../src/results_store.py#L97-L125);
   [load-time identity validation](../src/results_store.py#L68-L73).
3. **DEFECT FOUND — duplicate JSON object members are not rejected.** The
   loader uses default `json.loads()` behavior without a duplicate-key hook;
   duplicate property names can be collapsed before schema/key validation.
   Source: [JSON parsing and validation](../src/results_store.py#L46-L73).
4. **DEFECT FOUND — concurrent ResultsStore saves are not coordinated.**
   There is no lock; separate full-snapshot saves can last-writer-win and lose
   each other's additions. Sources: [store construction](../src/results_store.py#L35-L44);
   [whole-store replacement](../src/results_store.py#L97-L125).
5. **DEFECT FOUND — evaluation outputs overwrite history and are not written
   as one transaction.** Fixed `metrics.json` and `results.csv` paths are
   opened in `"w"` mode sequentially, with no versioning or rollback.
   Source: [evaluator output writes](../src/evaluate.py#L223-L232).
6. **INFERRED RISK — recovery diagnostics are lost if only `records` is
   passed.** The resolver returns diagnostics separately; the existing
   evaluator has no integration point for them. Sources:
   [scoreable result type](../src/recovery_ledger.py#L79-L100);
   [evaluator detail object](../src/evaluate.py#L181-L212).
7. **INFERRED RISK — direct merge bypasses selection/conflict policy.** The
   evaluator only applies split/prompt filters and metric usability checks;
   it does not consult the recovery ledger. Sources:
   [evaluator filter](../src/evaluate.py#L193-L209);
   [recovery selection/conflict resolution](../src/recovery_ledger.py#L764-L803).
8. **INFERRED RISK — evaluation coverage can depend on recovery-added runs.**
   The evaluator uses the split-wide maximum stored run for every prompt, not
   an explicit plan or per-prompt run set. Sources:
   [expected runs](../src/evaluate.py#L57-L69);
   [per-prompt call](../src/evaluate.py#L193-L209).
9. **NOT VERIFIED — cross-store crash consistency.** ResultsStore uses
   temporary-file replacement, but the evaluator's two output files are
   written separately and the recovery ledger has its own append transaction.
   No source establishes an atomic transaction spanning these persistence
   systems. Sources: [ResultsStore save](../src/results_store.py#L97-L129);
   [metrics writes](../src/evaluate.py#L223-L232);
   [ledger append transaction](../src/recovery_ledger.py#L647-L715).

## 8. Recommended future integration boundary

1. **RECOMMENDED — preserve the ordinary store.** Keep original
   `ResultsStore.records` unchanged. Do not copy selected recovery results into
   the original store; its duplicate-key rejection and mutable public mapping
   make implicit merge/replace semantics unsafe.
   Sources: [store API](../src/results_store.py#L81-L95);
   [mutable mapping](../src/results_store.py#L35-L44).
2. **RECOMMENDED — resolve before scoring.** Load the original result mapping
   and ledger events, call `build_scoreable_results()`, then pass only its
   scoreable records to a scoring adapter that filters by split/prompt and
   invokes `score_predictions()`. This is structurally compatible with the
   metric function but is not the current evaluator flow.
   Sources: [resolver interface](../src/recovery_ledger.py#L826-L841);
   [metric function input](../src/metrics.py#L237-L253);
   [current evaluator load/filter path](../src/evaluate.py#L170-L209).
3. **RECOMMENDED — keep diagnostics as a first-class artifact.** Preserve
   `ScoreableResults.diagnostics` alongside the scoreable inputs and metrics,
   including unresolved-provider uncertainty and conflicts. Do not reduce the
   resolver output to `records` alone.
   Sources: [scoreable result types](../src/recovery_ledger.py#L79-L100);
   [diagnostic construction](../src/recovery_ledger.py#L808-L824).
4. **RECOMMENDED — use a planned observation/run set.** Derive expected
   observations and run numbers from an explicitly approved experiment plan,
   not from a recovery-augmented store. Validate uniqueness before scoring.
   The current evaluator's split-wide range behavior is described at
   [expected run calculation](../src/evaluate.py#L57-L69).
5. **RECOMMENDED — version evaluation artifacts.** Write recovery-aware
   detail, summary, and diagnostics to a new run/version path with provenance
   identifying source result/ledger versions and selection decisions. Do not
   overwrite existing metrics. The current fixed-path overwrite behavior is at
   [evaluator output writes](../src/evaluate.py#L223-L232).
6. **RECOMMENDED — define persistence failure semantics.** If the resolver
   output or metrics are persisted separately, record which write completed
   and ensure a failure cannot be mistaken for a fully published evaluation.
   ResultsStore's file replacement does not make the evaluator's two outputs
   or the ledger part of the same transaction. Sources:
   [ResultsStore save](../src/results_store.py#L97-L129);
   [evaluator output writes](../src/evaluate.py#L223-L232);
   [ledger append](../src/recovery_ledger.py#L647-L715).

## 9. Unresolved policy decisions

1. Which failed or missing original observations are eligible for recovery?
   The resolver requires every event key to exist in `original_records`;
   how a truly missing original gets represented is undecided.
   Source: [unknown-original rejection](../src/recovery_ledger.py#L739-L751).
2. Which actor can authorize recovery selection, and what provenance is
   required before selected records are scored?
3. Should an error-marked record with otherwise valid parsed fields be treated
   as invalid in metrics, or is the `error` field informational for some
   results? Current scoring ignores it.
   Sources: [metrics usability predicate](../src/metrics.py#L44-L51);
   [result schema/insertion](../src/results_store.py#L10-L26)
   and [add_record](../src/results_store.py#L84-L95).
4. What planned run numbers define the expected metric denominator when
   different prompts have different stored or recovered run coverage?
5. Must existing metric outputs remain immutable, or may a new evaluation
   version supersede them? Current evaluator overwrites the same paths.
   Source: [output writes](../src/evaluate.py#L223-L232).
6. What result-store and metrics artifacts, hashes, and recovery diagnostics
   must be recorded as evaluation provenance?
7. How should a differing recovery result be handled when the original is
   successful? The resolver preserves the original, but selection and
   conflict policy for that comparison require explicit authorization.
   Source: [resolver selection/conflict branches](../src/recovery_ledger.py#L764-L803).
8. What concurrency model is intended for result writers? Current
   `ResultsStore` has no lock or merge protocol.
   Source: [store save implementation](../src/results_store.py#L97-L125).
9. What failure semantics are required for an evaluator crash between writing
   `metrics.json` and `results.csv`?
   Source: [sequential output writes](../src/evaluate.py#L223-L232).
10. Which version of the selected gold dataset is authoritative? This source
    audit verified the path and validator, not dataset contents or fingerprints.
    Sources: [gold path](../src/evaluate.py#L24-L43);
    [validation call](../src/validate_dataset.py#L208-L250).

## 10. Validation and preservation statement

- **Exact files read:** `reports/phase_b_recovery_integration_design.md`,
  `src/run_experiment.py`, `src/results_store.py`, `src/evaluate.py`,
  `src/metrics.py`, `src/config.py`, `src/validate_dataset.py`,
  `src/recovery_ledger.py`, and `RECOVERY_LEDGER.md`.
- **Exact report file created:** `reports/phase_b1_persistence_evaluation_audit.md`.
- **Application code and tests:** None executed.
- **Credentials and `.env`:** Not accessed.
- **Held-out data and datasets:** No dataset files or contents were accessed.
- **Experiment outputs, raw responses, retry logs, result files, or metrics
  artifacts:** No output files or contents were accessed.
- **Network/API:** No requests made.
- **Limitations:** No runtime failure behavior was tested; no output data or
  gold data was inspected; external filesystem guarantees and dependency-version
  numerical reproducibility were not established. The report's conclusions are
  limited to the inspected source paths and referenced line ranges.
