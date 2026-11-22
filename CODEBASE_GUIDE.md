# ClaimFast Codebase Guide

## Workflow

1. `src/run_experiment.py` loads and validates the selected split, renders each
   selected prompt for each record and run, then skips result keys already
   present in the store. `--runs` defaults to `RUNS_PER_PROMPT` in config
   (currently one); the dry-run plan prints the run count and total calls.
2. Before a live run, it shows the pending call count, prior average token
   usage, a token estimate, and a time estimate. It requests confirmation
   unless `--yes` is supplied. Live runs require a daily token limit from the
   Groq console unless `--no-daily-limit` is explicitly supplied.
3. `src.experiment_runtime` sends the configured model, temperature, token
   limit, fixed reasoning effort, hidden reasoning format, and rendered prompt
   through the client. It handles pacing, persistent retries, result
   validation, and daily token-limit enforcement.
4. `src.prompt_helpers` extracts Prompt C's final JSON block when needed and
   delegates the nine-field checks to `src.response_validation`.
5. `src.results_store` writes each final observation to
   `outputs/raw_responses.json` atomically. A result key is
   `split|record_id|prompt_id|run`.

## File roles

- `src/config.py`: project paths, model and generation settings, fixed
  `REASONING_EFFORT = "low"`, hidden reasoning output, one-run pilot default,
  two-second request delay, and `DAILY_TOKEN_LIMIT` (unset by default; set
  from the Groq console before a live run).
- `src/groq_client.py`: Groq client initialization and API-key configuration.
- `src/run_experiment.py`: CLI, planning, progress, and confirmation.
- `src/experiment_runtime.py`: iteration, request pacing, 429 retry behavior,
  and daily token-limit enforcement.
- `outputs/retry_log.jsonl`: separate append-only record of every HTTP 429,
  flushed and fsynced. Retry counts are restored by observation key on restart;
  malformed history stops the run instead of resetting the budget. New entries
  include sanitized rate-limit diagnostics; legacy entries remain readable.
- `src/results_store.py`: result loading, duplicate prevention, atomic save,
  and legacy-observation conversion.
- `src/prompt_helpers.py`: prompt definitions, rendering, Prompt C extraction,
  and prompt SHA-256 metadata.
- `src/metrics.py`: pure scoring functions for classification, extraction,
  hallucination, run agreement, usage, and safety-critical recall.
- `src/evaluate.py`: offline CLI that compares stored predictions with a
  selected gold split and writes headline and detailed reports.
- `src/response_validation.py`: exact output keys, allowed values, and field
  type validation.
- `scripts/test_groq_inference.py` and `scripts/test_groq_connection.py`:
  standalone smoke-test scripts; response validation is shared from
  `src/response_validation.py`.
- `prompts/`: five prompt variants, all using `{{CLAIM_MESSAGE}}`.
- `data/dev.jsonl`: development split. The test split is reserved for final
  evaluation.
- `outputs/results.csv`: one headline row per prompt and view for the selected
  split.
- `outputs/metrics.json`: detailed metrics, including fixed-order confusion
  matrices and per-field results.
- `outputs/raw_responses.json`: resumable results for the simplified runner.
- Each result stores the provider's reasoning-token count when available, but
  not reasoning text. The request uses the hidden reasoning format.
- The legacy `outputs/experiments/` folder was removed after the 15 completed
  development observations were migrated and cleanup was explicitly approved.
- `tests/`: offline store, fake-client runner, validation, and other unit
  tests.

## Fixed reasoning effort

All prompt variants use the same low reasoning effort so the experiment
compares prompt design without also varying the model's reasoning setting.
Groq supports this setting for `openai/gpt-oss-120b`; the SDK response's
`completion_tokens_details.reasoning_tokens` is stored as a count when present.
The request uses hidden reasoning format and never persists a reasoning text
field.

## Commands

```bash
# Preview one development call; no API request is made.
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m src.run_experiment \
  --split dev --prompts A --runs 1 --max-calls 1 --dry-run

# Execute that bounded call after interactive confirmation.
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m src.run_experiment \
  --split dev --prompts A --runs 1 --max-calls 1
```

The runner supports `--split dev|test`, one or more `--prompts` (`A`, `B-k3`,
`B-k5`, `B-k8`, `C`), `--runs N`, `--max-calls N`, `--dry-run`, `--yes`,
`--no-daily-limit`, and `--refresh-meta`. `--refresh-meta` shows old and new
settings and hashes and can rewrite metadata only when there are zero stored
records. A dry-run previews the refresh without writing. An empty store with
stale metadata can be previewed normally, but requires `--refresh-meta` before
a live run. Run `python -m src.run_experiment` from the repository root; no
`PYTHONPATH` adjustment is needed.

The daily limit defaults to `None`, and the CLI refuses a live run without a
configured limit unless `--no-daily-limit` is explicit. Before every request,
the runtime checks today's UTC usage plus a conservative estimate for the next
call: average observed total tokens, or rendered prompt length divided by four
plus `MAX_TOKENS` when there is no complete usage history. Missing usage or
timestamps for stored records are estimated conservatively rather than
ignored.

Only HTTP 429 responses are retried, at most five times for each observation
across restarts. Numeric or HTTP-date `Retry-After` values are honored; if the
header is absent, waits use 5, 10, 20, 40, and 40 seconds. If a supplied value
cannot be parsed, progress is saved and the run exits non-zero. Every 429 is
appended to the retry log with its observation key, attempt number, UTC
timestamp, and wait seconds. New entries also record status/classification,
sanitized Retry-After data, an allowlist of remaining/reset headers, and a
sanitized provider request ID. Other errors are saved and not automatically
retried.

## Evaluation

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m src.evaluate --split dev
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m src.evaluate --split test
```

The evaluator reads gold labels from the selected JSONL file and predictions
from `outputs/raw_responses.json`. IDs match the runner: `DEV-01` is the first
development line, and test records are named `TEST-01` through `TEST-60`.
Expected runs are inferred from the largest run number stored for that split;
if there are no predictions, the evaluator uses the runner's one-run default.
Missing prediction keys produce warnings and count as invalid outputs for
accuracy, F1, field match, safety recall, and validity rates.

Development reports include `all` and `dev_eval`. The latter removes the
union of examples present in the three few-shot prompt files:
`DEV-01`, `DEV-03`, `DEV-06`, `DEV-09`, `DEV-10`, `DEV-11`, `DEV-14`, and
`DEV-17`. Test reports include all records. Confusion matrices use fixed
claim-type and urgency label orders, with an invalid-output column. Per-field
F1 is macro-F1 over observed gold and valid predicted values, including null.
Location lenient match case-folds and strips punctuation, then accepts
containment or Jaccard token overlap of at least 0.5. Cost per 1,000 claims
uses the expected run count and the two price constants in `src/config.py`;
it is not calculated while either price remains `None`.

## Viva practice

1. **What does the result key protect against?**  
   It uniquely identifies a split, source record, prompt variant, and run, so a
   resumed run does not repeat completed work.
2. **Why are API results written through a temporary file?**  
   The store flushes the complete JSON document and atomically replaces the
   destination, protecting earlier results from partial writes.
3. **What is the retry policy?**  
   HTTP 429 responses are retried at most five times per observation, with
   retry counts persisted across restarts. An absent `Retry-After` uses the
   bounded fallback schedule; a supplied but unparseable value stops safely.
   Other errors are saved and not automatically retried.
4. **Why use one reasoning effort for every prompt?**  
   Keeping it fixed controls a model-side variable, so observed differences
   are attributable to the prompt variants rather than different reasoning
   settings.
5. **Why choose low reasoning effort?**  
   It is a single supported setting for the model that keeps the comparison
   consistent while limiting extra reasoning-token use.
6. **What reasoning data is retained?**  
   The runner stores the integer reasoning-token count when Groq returns it.
   Reasoning text is hidden in the request and is not stored.
7. **Why is `dev_eval` the headline development view?**  
   It excludes any record demonstrated in a few-shot prompt, keeping every
   prompt's development comparison on the same unseen records.
8. **Why do failed outputs remain in the metric denominator?**  
   Excluding them would overstate performance; invalid and missing outputs
   therefore count as wrong and are reported separately.
9. **How are hallucination rate and lenient location match defined?**  
   Non-null output for a null gold field is a hallucination, and policy or
   vehicle identifiers must occur in the normalized message. Lenient location
   match uses normalized containment or Jaccard token overlap of at least 0.5.
