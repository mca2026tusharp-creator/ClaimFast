"""Run a resumable ClaimFast experiment and save each final result atomically."""

import argparse
import json
import sys
from statistics import mean

from src import config
from src.experiment_plan import (
    ExperimentPlanError,
    load_experiment_plan,
    plan_expected_keys,
    plan_result_metadata,
    validate_result_metadata_against_plan,
)
from src.groq_client import create_groq_client
from src.experiment_runtime import (
    load_inference_records,
    load_records,
    load_retry_state,
    run_pending,
)
from src.prompt_helpers import (
    PROMPT_FILES,
    build_results_meta,
    load_prompts,
    render_prompt,
    validate_model_output,
)
from src.results_store import (
    RESULTS_PATH as LEGACY_RESULTS_PATH,
    RETRYABLE_FAILED_OBSERVATION_KEY,
    ResultsStore,
    is_successful_result,
    result_key,
)

RESULTS_PATH = config.DEVELOPMENT_RESULTS_PATH
RETRY_LOG_PATH = config.DEVELOPMENT_RETRY_LOG_PATH


def _confirmed(prompt: str, yes: bool) -> bool:
    return yes or input(prompt).strip().lower() in {"y", "yes"}


def _print_metadata_changes(old: dict, new: dict) -> None:
    print("Results metadata refresh preview:")
    old_hashes = old.get("prompt_sha256")
    if not isinstance(old_hashes, dict):
        old_hashes = {}
    for prompt_id in PROMPT_FILES:
        old_hash = old_hashes.get(prompt_id, "(missing)")
        new_hash = new["prompt_sha256"][prompt_id]
        print(f"  {prompt_id} prompt_sha256: {old_hash} -> {new_hash}")
    for field in (
        "model",
        "temperature",
        "max_tokens",
        "reasoning_effort",
        "reasoning_format",
    ):
        print(f"  {field}: {old.get(field, '(missing)')} -> {new[field]}")


def _should_queue_key(
    key: str,
    store: ResultsStore,
    retry_failed_keys: set[str],
    exclude_key: str | None,
) -> bool:
    if key == exclude_key:
        return False
    if retry_failed_keys:
        return key in retry_failed_keys
    return not store.has_key(key)


def _validate_heldout_selection(
    plan: dict,
    prompt_ids: list[str],
    runs: int,
    results_path,
    retry_log_path,
) -> int:
    if prompt_ids != list(PROMPT_FILES):
        raise ExperimentPlanError("Held-out runs must select all five approved prompts in plan order.")
    run_numbers = plan["coverage"]["test"]["run_numbers"]
    if run_numbers != [1, 2, 3] or runs != len(run_numbers):
        raise ExperimentPlanError("Held-out runs must select exactly runs 1 through 3.")
    expected_keys = plan_expected_keys("test", plan)
    if len(expected_keys) != 900 or len(set(expected_keys)) != 900:
        raise ExperimentPlanError("Held-out selection must contain exactly 900 unique keys.")

    results_path = results_path.resolve()
    retry_log_path = retry_log_path.resolve()
    forbidden_paths = {
        config.DEVELOPMENT_RESULTS_PATH.resolve(),
        config.DEVELOPMENT_RETRY_LOG_PATH.resolve(),
        LEGACY_RESULTS_PATH.resolve(),
        (config.OUTPUTS_DIR / "retry_log.jsonl").resolve(),
    }
    if results_path in forbidden_paths or retry_log_path in forbidden_paths:
        raise ExperimentPlanError("Held-out paths must not use development or legacy stores.")
    if results_path == retry_log_path:
        raise ExperimentPlanError("Held-out results and retry log paths must be distinct.")
    heldout_root = (config.OUTPUTS_DIR.resolve() / "heldout_runs")
    heldout_run_dir = config.HELDOUT_RUN_DIR.resolve()
    if heldout_root not in heldout_run_dir.parents:
        raise ExperimentPlanError("Held-out run directory must be under outputs/heldout_runs.")
    if heldout_run_dir not in results_path.parents:
        raise ExperimentPlanError("Held-out results path is outside the dedicated run directory.")
    if heldout_run_dir not in retry_log_path.parents:
        raise ExperimentPlanError("Held-out retry log path is outside the dedicated run directory.")
    return len(expected_keys)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), required=True)
    parser.add_argument(
        "--prompts",
        nargs="+",
        choices=tuple(PROMPT_FILES),
        default=list(PROMPT_FILES),
    )
    parser.add_argument("--runs", type=int, default=config.RUNS_PER_PROMPT)
    parser.add_argument("--max-calls", type=int)
    parser.add_argument(
        "--exclude-key",
        metavar="OBSERVATION_KEY",
        help="Exclude this exact observation key from pending execution.",
    )
    parser.add_argument(
        "--retry-failed-key",
        action="append",
        metavar="OBSERVATION_KEY",
        help="Retry this exact stored unsuccessful observation.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--no-daily-limit", action="store_true")
    parser.add_argument("--refresh-meta", action="store_true")
    args = parser.parse_args(argv)
    if args.runs <= 0 or (args.max_calls is not None and args.max_calls <= 0):
        parser.error("--runs and --max-calls must be positive integers")

    try:
        plan = load_experiment_plan(args.split)
        retry_failed_keys = set(args.retry_failed_key or ())
        if args.split == "dev":
            if (
                len(retry_failed_keys) > 1
                or retry_failed_keys - {RETRYABLE_FAILED_OBSERVATION_KEY}
            ):
                raise ExperimentPlanError(
                    "Development retry is limited to the approved failed observation."
                )
            retry_failed_key = next(iter(retry_failed_keys), None)
            retry_failed_keys.clear()
            results_path = RESULTS_PATH
            retry_log_path = RETRY_LOG_PATH
        else:
            retry_failed_key = None
            results_path = config.HELDOUT_RESULTS_PATH
            retry_log_path = config.HELDOUT_RETRY_LOG_PATH
            _validate_heldout_selection(
                plan,
                args.prompts,
                args.runs,
                results_path,
                retry_log_path,
            )
            expected_keys = set(plan_expected_keys("test", plan))
            if retry_failed_keys - expected_keys:
                raise ExperimentPlanError(
                    "A held-out retry key is not in the approved plan."
                )
            if args.dry_run:
                if retry_failed_keys:
                    raise ExperimentPlanError(
                        "Plan-only dry-run cannot verify stored retry eligibility."
                    )
                print("Runs per prompt: 3")
                print("Expected held-out observations: 900 unique keys")
                print(f"Results store: {results_path}")
                print(f"Retry log: {retry_log_path}")
                print("Development and legacy result paths: not used")
                print("Dry run; no dataset records, result stores, or API clients were loaded.")
                return 0

        if args.split == "test":
            records = load_inference_records(
                "test",
                plan["dataset"]["record_ids"],
                expected_dataset_sha256=plan["dataset"]["sha256"],
            )
            meta = build_results_meta(plan)
        else:
            records = load_records(args.split)
            meta = build_results_meta()
        prompts = load_prompts(args.prompts)
        plan_meta = plan_result_metadata(plan)
        canonical_model = (
            meta.get("model") == config.MODEL_NAME
            and meta.get("temperature") == config.TEMPERATURE
            and meta.get("max_tokens") == config.MAX_TOKENS
            and meta.get("reasoning_effort") == config.REASONING_EFFORT
            and meta.get("reasoning_format") == config.REASONING_FORMAT
        )
        canonical_prompts = all(
            meta.get("prompt_sha256", {}).get(prompt_id) == plan_meta["prompt_sha256"][prompt_id]
            for prompt_id in PROMPT_FILES
        )
        if canonical_model and canonical_prompts:
            meta.update(plan_meta)
        store = ResultsStore.load(results_path, meta)
        if args.prompts and not set(args.prompts).issubset(set(PROMPT_FILES)):
            raise ExperimentPlanError(f"Unknown prompt selection: {sorted(set(args.prompts) - set(PROMPT_FILES))}")
    except (OSError, ValueError, json.JSONDecodeError, ExperimentPlanError) as error:
        print(f"Runner stopped: {error}", file=sys.stderr)
        return 1
    metadata_fields = (
        "model",
        "temperature",
        "max_tokens",
        "reasoning_effort",
        "reasoning_format",
        "dataset_snapshot_sha256",
        "plan_digest",
        "prompt_sha256",
    )
    mismatches = [
        field for field in metadata_fields
        if store.meta.get(field) != meta.get(field)
    ]
    if args.refresh_meta and store.records:
        print(
            "Runner stopped: --refresh-meta is allowed only when the results "
            "store has zero records.",
            file=sys.stderr,
        )
        return 1
    if args.refresh_meta:
        _print_metadata_changes(store.meta, meta)
        if not _confirmed("Refresh empty results metadata? [y/N] ", args.yes):
            print("Metadata refresh cancelled; results were not changed.")
            return 0
        if args.dry_run:
            print("Dry run; metadata refresh was previewed but not written.")
            return 0
        store.meta = meta
        try:
            store.save()
        except OSError as error:
            print(
                "Runner stopped: results metadata could not be saved "
                f"({type(error).__name__}).",
                file=sys.stderr,
            )
            return 1
        print("Empty results metadata refreshed.")
    elif mismatches:
        if not store.records and args.dry_run:
            print(
                "Dry-run only: results metadata differs for "
                f"{', '.join(mismatches)}; no metadata was written. "
                "Use --refresh-meta before a live run."
            )
        else:
            print(
                "Runner stopped: results metadata mismatch for "
                f"{', '.join(mismatches)}.",
                file=sys.stderr,
            )
            return 1
    try:
        if (
            store.meta.get("model") == config.MODEL_NAME
            and store.meta.get("temperature") == config.TEMPERATURE
            and store.meta.get("max_tokens") == config.MAX_TOKENS
            and store.meta.get("reasoning_effort") == config.REASONING_EFFORT
            and store.meta.get("reasoning_format") == config.REASONING_FORMAT
        ):
            validate_result_metadata_against_plan(store.meta, plan=plan)
    except ExperimentPlanError as error:
        print(f"Runner stopped: {error}", file=sys.stderr)
        return 1
    if retry_failed_key is not None or retry_failed_keys:
        try:
            expected_keys = set(plan_expected_keys(args.split, plan))
        except ExperimentPlanError as error:
            print(f"Runner stopped: {error}", file=sys.stderr)
            return 1
        requested_retry_keys = retry_failed_keys | (
            {retry_failed_key} if retry_failed_key is not None else set()
        )
        for retry_key in requested_retry_keys:
            existing = store.records.get(retry_key)
            if retry_key not in expected_keys:
                print(
                    "Runner stopped: retry key is not in the authoritative plan.",
                    file=sys.stderr,
                )
                return 1
            if existing is None or is_successful_result(existing):
                print(
                    "Runner stopped: retry key must identify an existing "
                    "unsuccessful result.",
                    file=sys.stderr,
                )
                return 1
    pending = []
    prompt_text_by_key = {}
    for record in records:
        for prompt_id, template in prompts.items():
            for run in range(1, args.runs + 1):
                item = {
                    **{k: record[k] for k in ("split", "record_id")},
                    "prompt_id": prompt_id,
                    "run": run,
                    "prompt_text": render_prompt(template, record["message"]),
                }
                key = result_key(item)
                prompt_text_by_key[key] = item["prompt_text"]
                if _should_queue_key(
                    key,
                    store,
                    retry_failed_keys | (
                        {retry_failed_key} if retry_failed_key is not None else set()
                    ),
                    args.exclude_key,
                ):
                    pending.append(item)

    requested_retry_keys = retry_failed_keys | (
        {retry_failed_key} if retry_failed_key is not None else set()
    )
    if requested_retry_keys and not requested_retry_keys.issubset(
        {result_key(item) for item in pending}
    ):
        print(
            "Runner stopped: a retry key is not included in the requested run selection.",
            file=sys.stderr,
        )
        return 1

    usage_records = [
        r for r in store.records.values()
        if type(r.get("input_tokens")) is int and type(r.get("output_tokens")) is int
    ]
    average_tokens = mean(
        r["input_tokens"] + r["output_tokens"] for r in usage_records
    ) if usage_records else None
    planned_calls = min(len(pending), args.max_calls) if args.max_calls else len(pending)
    recorded_tokens = sum(
        r["input_tokens"] + r["output_tokens"] for r in usage_records
    )
    estimated_total = (
        recorded_tokens + planned_calls * average_tokens
        if average_tokens is not None else None
    )
    latencies = [
        r["latency_seconds"] for r in store.records.values()
        if isinstance(r.get("latency_seconds"), (int, float))
    ]
    average_latency = mean(latencies) if latencies else 0
    minutes = (
        planned_calls * average_latency
        + max(0, planned_calls - 1) * config.REQUEST_DELAY_SECONDS
    ) / 60
    print(f"Runs per prompt: {args.runs}")
    if args.exclude_key is not None:
        print(f"Excluded observation key: {args.exclude_key}")
    print(f"Total planned calls: {planned_calls}")
    print(f"Calls to do: {planned_calls}")
    print(f"Average tokens per call so far: {average_tokens if average_tokens is not None else 'unavailable'}")
    print(f"Estimated total tokens: {estimated_total if estimated_total is not None else 'unavailable'}")
    print(f"Estimated minutes: {minutes:.1f} (retries excluded)")
    if args.dry_run:
        print("Dry run; no API call was made.")
        return 0
    if not pending:
        print("No calls to do.")
        return 0
    if config.DAILY_TOKEN_LIMIT is None and not args.no_daily_limit:
        print(
            "Runner stopped: DAILY_TOKEN_LIMIT is not configured from the Groq "
            "console. Set it before a live run, or explicitly pass "
            "--no-daily-limit to run without this safeguard.",
            file=sys.stderr,
        )
        return 1
    if args.no_daily_limit:
        print("Daily token limit explicitly bypassed with --no-daily-limit.")
    if not _confirmed("Proceed? [y/N] ", args.yes):
        print("Cancelled; no API call was made.")
        return 0
    try:
        retry_state = load_retry_state(retry_log_path)
        client = create_groq_client(max_retries=0)
        return run_pending(
            pending,
            client,
            store,
            args.max_calls,
            retry_log_path=retry_log_path,
            prompt_text_by_key=prompt_text_by_key,
            no_daily_limit=args.no_daily_limit,
            retry_state=retry_state,
            retry_failed_key=retry_failed_key,
            retry_failed_keys=retry_failed_keys,
        )
    except KeyboardInterrupt:
        print("Interrupted; saved results are retained.")
        return 130
    except RuntimeError:
        print("Runner configuration error: GROQ_API_KEY is unavailable.")
        return 1
    except (OSError, ValueError) as error:
        print(f"Runner stopped safely: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
