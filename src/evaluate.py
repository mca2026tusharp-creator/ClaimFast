"""Score stored ClaimFast predictions against a selected gold split."""

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src import config
from src.experiment_plan import (
    ExperimentPlanError,
    load_experiment_plan,
    plan_expected_keys,
    plan_expected_run_numbers,
    validate_result_metadata_against_plan,
)
from src.metrics import FIELD_NAMES, score_predictions
from src.prompt_helpers import PROMPT_FILES
from src.results_store import RESULTS_PATH, ResultsStore
from src.validate_dataset import validate_record

EXAMPLE_HEADER = re.compile(r"^### Example (DEV-\d+)\s*$", re.MULTILINE)
CLASSIFICATION_FIELDS = (
    "claim_type",
    "urgency",
)
EVALUATION_BUNDLE_VERSIONS = {
    "dev": "development-eval-bundle/v1",
    "test": "heldout-eval-bundle/v1",
}


def load_gold_records(split: str) -> list[dict[str, Any]]:
    path = config.DATA_DIR / f"{split}.jsonl"
    records = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{path.name}:{line_number}: invalid JSON: {error.msg}"
                ) from error
            errors = validate_record(record, f"{path.name}:{line_number}")
            if errors:
                raise ValueError("; ".join(errors))
            records.append({
                "record_id": f"{split.upper()}-{line_number:02d}",
                "label": record["label"],
                "original_message": record["original_message"],
            })
    return records


def load_dev_example_ids() -> set[str]:
    example_ids = set()
    for prompt_id in ("B-k3", "B-k5", "B-k8"):
        prompt_path = config.PROMPTS_DIR / PROMPT_FILES[prompt_id]
        text = prompt_path.read_text(encoding="utf-8")
        example_ids.update(EXAMPLE_HEADER.findall(text))
    if not example_ids:
        raise ValueError("No development examples were found in the few-shot prompts.")
    return example_ids


def expected_run_numbers(
    split: str,
    result_records: list[dict[str, Any]],
) -> list[int]:
    runs = [
        record["run"]
        for record in result_records
        if record.get("split") == split
    ]
    if any(type(run) is not int or run < 1 for run in runs):
        raise ValueError(f"{split} predictions contain an invalid run number.")
    return list(range(1, max(runs, default=1) + 1))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_evaluation_bundle(
    split: str,
    detail: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    plan: dict[str, Any],
    results_path: Path,
    ledger_path: Path | None,
) -> Path:
    evaluation_id = f"{split}-{plan['plan_digest'][:12]}-{uuid4().hex[:8]}"
    bundle_dir = config.OUTPUTS_DIR / "evaluations" / evaluation_id
    if bundle_dir.exists():
        raise FileExistsError(f"Evaluation bundle already exists: {bundle_dir}")
    bundle_dir.mkdir(parents=True, exist_ok=False)

    metrics_path = bundle_dir / "metrics.json"
    csv_path = bundle_dir / "results.csv"
    manifest_path = bundle_dir / "manifest.json"

    metrics_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=_csv_columns())
        writer.writeheader()
        writer.writerows(rows)

    manifest = {
        "schema_version": EVALUATION_BUNDLE_VERSIONS[split],
        "evaluation_id": evaluation_id,
        "split": split,
        "plan_digest": plan["plan_digest"],
        "pinned_expected_plan_digest": plan["pinned_expected_plan_digest"],
        "dataset_snapshot_sha256": plan["dataset"]["sha256"],
        "results_store_sha256": _hash_file(results_path),
        "ledger_sha256": None if ledger_path is None or not ledger_path.exists() else _hash_file(ledger_path),
        "input_hashes": {
            "dataset": plan["dataset"]["sha256"],
            "prompt_sha256": {prompt_id: prompt_info["sha256"] for prompt_id, prompt_info in plan["prompts"].items()},
            "results_store": _hash_file(results_path),
        },
        "output_hashes": {
            "metrics.json": _hash_file(metrics_path),
            "results.csv": _hash_file(csv_path),
        },
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return bundle_dir


def _csv_columns() -> list[str]:
    columns = [
        "split",
        "prompt_id",
        "view",
        "record_count",
        "expected_predictions",
        "missing_predictions",
        "claim_type_accuracy",
        "claim_type_macro_f1",
        "urgency_accuracy",
        "urgency_macro_f1",
        "parse_ok_rate",
        "schema_ok_rate",
    ]
    for field in FIELD_NAMES:
        columns.extend((f"{field}_exact_match", f"{field}_macro_f1"))
        if field == "location":
            columns.append("location_lenient_match")
    columns.extend((
        "hallucination_rate",
        "high_urgency_recall",
        "high_urgency_false_negatives",
        "injury_reported_true_recall",
        "injury_reported_true_false_negatives",
        "run_to_run_parsed_json_agreement",
        "run_to_run_claim_type_agreement",
        "mean_input_tokens",
        "mean_output_tokens",
        "mean_reasoning_tokens",
        "mean_latency_seconds",
        "cost_per_1000_claims",
    ))
    return columns


def _csv_row(split: str, prompt_id: str, view: str, metrics: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "split": split,
        "prompt_id": prompt_id,
        "view": view,
        "record_count": metrics["record_count"],
        "expected_predictions": metrics["expected_predictions"],
        "missing_predictions": metrics["missing_predictions"],
        "claim_type_accuracy": metrics["claim_type"]["accuracy"],
        "claim_type_macro_f1": metrics["claim_type"]["macro_f1"],
        "urgency_accuracy": metrics["urgency"]["accuracy"],
        "urgency_macro_f1": metrics["urgency"]["macro_f1"],
        "parse_ok_rate": metrics["parse_ok_rate"],
        "schema_ok_rate": metrics["schema_ok_rate"],
        "hallucination_rate": metrics["hallucination"]["overall"]["rate"],
        "high_urgency_recall": metrics["safety_critical"]["high_urgency_recall"],
        "high_urgency_false_negatives":
            metrics["safety_critical"]["high_urgency_false_negatives"],
        "injury_reported_true_recall":
            metrics["safety_critical"]["injury_reported_true_recall"],
        "injury_reported_true_false_negatives":
            metrics["safety_critical"]["injury_reported_true_false_negatives"],
        "run_to_run_parsed_json_agreement":
            metrics["run_to_run_agreement"]["parsed_json"],
        "run_to_run_claim_type_agreement":
            metrics["run_to_run_agreement"]["claim_type"],
        **metrics["usage"],
    }
    for field in FIELD_NAMES:
        row[f"{field}_exact_match"] = metrics["fields"][field]["exact_match"]
        row[f"{field}_macro_f1"] = metrics["fields"][field]["macro_f1"]
        if field == "location":
            row["location_lenient_match"] = metrics["fields"][field]["lenient_match"]
    return row


def _print_summary(rows: list[dict[str, Any]]) -> None:
    headers = (
        "Split", "Prompt", "View", "Claim acc.", "Claim F1",
        "Urgency acc.", "Urgency F1", "Parse", "Schema", "Missing",
    )
    print(
        f"{headers[0]:<7} {headers[1]:<7} {headers[2]:<9} "
        f"{headers[3]:>10} {headers[4]:>9} {headers[5]:>12} "
        f"{headers[6]:>10} {headers[7]:>7} {headers[8]:>7} {headers[9]:>9}"
    )
    for row in rows:
        print(
            f"{row['split']:<7} {row['prompt_id']:<7} {row['view']:<9} "
            f"{_format_rate(row['claim_type_accuracy']):>10} "
            f"{_format_rate(row['claim_type_macro_f1']):>9} "
            f"{_format_rate(row['urgency_accuracy']):>12} "
            f"{_format_rate(row['urgency_macro_f1']):>10} "
            f"{_format_rate(row['parse_ok_rate']):>7} "
            f"{_format_rate(row['schema_ok_rate']):>7} "
            f"{row['missing_predictions']:>9}/{row['expected_predictions']}"
        )


def _format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def evaluate_split(
    split: str,
    *,
    results_path: str | Path | None = None,
    ledger_path: str | Path | None = None,
    plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    plan = load_experiment_plan(split) if plan is None else plan
    if split != plan["split"]:
        raise ExperimentPlanError(f"Requested split {split!r} is not the plan's split {plan['split']!r}.")

    if split == "test" and results_path is None:
        raise ValueError("Held-out evaluation requires an explicit isolated results store.")
    results_path = Path(results_path) if results_path is not None else Path(RESULTS_PATH)
    if split == "test" and results_path.resolve() != config.HELDOUT_RESULTS_PATH.resolve():
        raise ValueError("Held-out evaluation must use the pinned held-out results path.")
    if not results_path.exists():
        raise FileNotFoundError(f"Results store does not exist: {results_path}")
    if not results_path.is_file():
        raise ValueError(f"Results store is not a regular file: {results_path}")

    store = ResultsStore.load(results_path)
    validate_result_metadata_against_plan(store.meta, plan=plan)
    if split == "test" and any(
        record.get("split") != "test" for record in store.records.values()
    ):
        raise ValueError("Held-out results store contains records from another split.")

    expected_keys = set(plan_expected_keys(split, plan))
    result_records = list(store.records.values())
    extracted_keys = {
        f"{record['split']}|{record['record_id']}|{record['prompt_id']}|{record['run']}"
        for record in result_records
        if record.get("split") == split
    }
    unexpected = extracted_keys - expected_keys
    if unexpected:
        raise ValueError(f"Unexpected result keys for {split}: {sorted(unexpected)[:10]}")
    missing = expected_keys - extracted_keys
    if missing:
        raise ValueError(f"Missing required result keys for {split}: {sorted(missing)[:10]}")
    run_numbers = plan_expected_run_numbers(split, plan)

    gold_records = load_gold_records(split)
    example_ids = load_dev_example_ids() if split == "dev" else set()
    views = {"all": gold_records}
    if split == "dev":
        views["dev_eval"] = [
            record for record in gold_records
            if record["record_id"] not in example_ids
        ]

    detail: dict[str, Any] = {
        "split": split,
        "run_numbers": run_numbers,
        "expected_observation_keys": sorted(expected_keys),
        "missing_observation_keys": sorted(expected_keys - extracted_keys),
        "excluded_dev_example_ids": sorted(
            example_ids,
            key=lambda record_id: int(record_id.split("-")[1]),
        ),
        "plan_digest": plan["plan_digest"],
        "dataset_snapshot_sha256": plan["dataset"]["sha256"],
        "views": {},
    }
    summary_rows = []
    for view, view_gold in views.items():
        detail["views"][view] = {}
        for prompt_id in PROMPT_FILES:
            prompt_predictions = [
                record for record in result_records
                if record.get("split") == split
                and record.get("prompt_id") == prompt_id
            ]
            metrics = score_predictions(
                view_gold,
                prompt_predictions,
                run_numbers,
                input_price_per_m=config.INPUT_PRICE_PER_M,
                output_price_per_m=config.OUTPUT_PRICE_PER_M,
            )
            metrics["split"] = split
            metrics["prompt_id"] = prompt_id
            metrics["view"] = view
            detail["views"][view][prompt_id] = metrics
            row = _csv_row(split, prompt_id, view, metrics)
            summary_rows.append(row)
            if metrics["missing_predictions"]:
                print(
                    f"WARNING: {split}/{prompt_id}/{view}: "
                    f"{metrics['missing_predictions']} missing prediction key(s) "
                    f"of {metrics['expected_predictions']} expected."
                )

    config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    bundle_dir = _write_evaluation_bundle(
        split,
        detail,
        summary_rows,
        plan=plan,
        results_path=results_path,
        ledger_path=None if ledger_path is None else Path(ledger_path),
    )

    _print_summary(summary_rows)
    print(f"Detailed metrics: {bundle_dir / 'metrics.json'}")
    print(f"Headline results: {bundle_dir / 'results.csv'}")
    print(f"Manifest: {bundle_dir / 'manifest.json'}")
    return summary_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), required=True)
    parser.add_argument(
        "--plan",
        type=Path,
        help="Plan file to verify (defaults to the pinned plan for the selected split).",
    )
    parser.add_argument(
        "--results",
        type=Path,
        help="ResultsStore path to evaluate. Required for the held-out split.",
    )
    args = parser.parse_args(argv)
    if args.split == "test" and args.results is None:
        parser.error("--results is required for held-out evaluation.")
    try:
        plan = load_experiment_plan(args.split, args.plan)
        evaluate_split(args.split, results_path=args.results, plan=plan)
    except (OSError, ValueError, json.JSONDecodeError, ExperimentPlanError) as error:
        print(f"Evaluation stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
