"""Canonical development and held-out plan verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src import config
from src.prompt_helpers import PROMPT_FILES
from src.validate_dataset import validate_record


class ExperimentPlanError(ValueError):
    """Raised when the development plan cannot be verified."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_and_line_count(path: str | Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    line_count = 0
    last_byte = b""
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest.update(block)
            line_count += block.count(b"\n")
            last_byte = block[-1:]
    if last_byte and last_byte != b"\n":
        line_count += 1
    return digest.hexdigest(), line_count


def _remove_plan_digests(plan: dict[str, Any]) -> dict[str, Any]:
    reduced = dict(plan)
    reduced.pop("plan_digest", None)
    reduced.pop("pinned_expected_plan_digest", None)
    return reduced


def build_development_plan() -> dict[str, Any]:
    """Return the canonical development evaluation plan derived from current files."""
    dataset_path = config.DATA_DIR / "dev.jsonl"
    if not dataset_path.exists():
        raise ExperimentPlanError(f"Development dataset is missing: {dataset_path}")

    record_ids: list[str] = []
    with dataset_path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ExperimentPlanError(
                    f"Development dataset line {line_number} is invalid JSON: {error.msg}"
                ) from error
            errors = validate_record(record, f"{dataset_path.name}:{line_number}")
            if errors:
                raise ExperimentPlanError(
                    f"Development dataset record {line_number} is invalid: {'; '.join(errors)}"
                )
            record_ids.append(f"DEV-{line_number:02d}")

    if not record_ids:
        raise ExperimentPlanError("Development dataset has no valid records.")

    prompt_hashes: dict[str, dict[str, str]] = {}
    for prompt_id, filename in PROMPT_FILES.items():
        prompt_path = config.PROMPTS_DIR / filename
        if not prompt_path.exists():
            raise ExperimentPlanError(f"Prompt file is missing: {prompt_path}")
        prompt_hashes[prompt_id] = {
            "path": str(prompt_path.relative_to(config.PROJECT_ROOT)),
            "sha256": _sha256_file(prompt_path),
        }

    expected_observation_keys: list[str] = []
    for record_id in record_ids:
        for prompt_id in PROMPT_FILES:
            expected_observation_keys.append(f"dev|{record_id}|{prompt_id}|1")

    plan: dict[str, Any] = {
        "schema_version": "development-eval-plan/v1",
        "plan_name": "development_dev_plan",
        "split": "dev",
        "dataset": {
            "path": str((config.DATA_DIR / "dev.jsonl").relative_to(config.PROJECT_ROOT)),
            "sha256": _sha256_file(dataset_path),
            "record_ids": record_ids,
            "record_count": len(record_ids),
        },
        "prompts": prompt_hashes,
        "model": {
            "name": config.MODEL_NAME,
            "temperature": config.TEMPERATURE,
            "max_tokens": config.MAX_TOKENS,
            "reasoning_effort": config.REASONING_EFFORT,
            "reasoning_format": config.REASONING_FORMAT,
        },
        "coverage": {
            "dev": {
                "run_numbers": [1],
                "expected_observation_keys": expected_observation_keys,
                "expected_observation_count": len(expected_observation_keys),
            }
        },
    }
    plan["plan_digest"] = hashlib.sha256(
        _canonical_json_bytes(_remove_plan_digests(plan))
    ).hexdigest()
    plan["pinned_expected_plan_digest"] = config.DEVELOPMENT_PLAN_EXPECTED_DIGEST
    return plan


def build_heldout_plan(
    dataset_path: str | Path | None = None,
    prompts_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build the held-out plan from file hashes and fixed coverage, without parsing data."""
    dataset_path = Path(dataset_path) if dataset_path is not None else config.DATA_DIR / "test.jsonl"
    prompts_dir = Path(prompts_dir) if prompts_dir is not None else config.PROMPTS_DIR
    dataset_hash, record_count = _sha256_and_line_count(dataset_path)
    if record_count != 60:
        raise ExperimentPlanError(
            f"Held-out dataset must contain 60 JSONL lines; found {record_count}."
        )

    record_ids = [f"TEST-{index:02d}" for index in range(1, 61)]
    prompt_hashes: dict[str, dict[str, str]] = {}
    for prompt_id, filename in PROMPT_FILES.items():
        prompt_path = prompts_dir / filename
        if not prompt_path.is_file():
            raise ExperimentPlanError(f"Prompt file is missing: {prompt_path}")
        prompt_hashes[prompt_id] = {
            "path": str(prompt_path.relative_to(config.PROJECT_ROOT))
            if prompt_path.is_relative_to(config.PROJECT_ROOT)
            else filename,
            "sha256": _sha256_file(prompt_path),
        }

    run_numbers = [1, 2, 3]
    expected_keys = [
        f"test|{record_id}|{prompt_id}|{run}"
        for record_id in record_ids
        for prompt_id in PROMPT_FILES
        for run in run_numbers
    ]
    plan: dict[str, Any] = {
        "schema_version": "heldout-eval-plan/v1",
        "plan_name": "heldout_test_plan_v1",
        "split": "test",
        "dataset": {
            "path": "data/test.jsonl",
            "sha256": dataset_hash,
            "record_ids": record_ids,
            "record_count": record_count,
        },
        "prompts": prompt_hashes,
        "model": {
            "name": config.MODEL_NAME,
            "temperature": config.TEMPERATURE,
            "max_tokens": config.MAX_TOKENS,
            "reasoning_effort": config.REASONING_EFFORT,
            "reasoning_format": config.REASONING_FORMAT,
        },
        "coverage": {
            "test": {
                "run_numbers": run_numbers,
                "expected_observation_keys": expected_keys,
                "expected_observation_count": len(expected_keys),
            }
        },
    }
    if len(expected_keys) != 900 or len(set(expected_keys)) != 900:
        raise ExperimentPlanError("Held-out plan must contain exactly 900 unique keys.")
    plan_digest = hashlib.sha256(
        _canonical_json_bytes(_remove_plan_digests(plan))
    ).hexdigest()
    plan["plan_digest"] = plan_digest
    plan["pinned_expected_plan_digest"] = plan_digest
    return plan


def load_heldout_plan(path: str | Path | None = None) -> dict[str, Any]:
    """Load a pinned held-out plan and verify its snapshot without parsing JSONL records."""
    plan_path = Path(path) if path is not None else config.HELDOUT_PLAN_PATH
    if not plan_path.is_file():
        raise ExperimentPlanError(f"Held-out plan file is missing: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ExperimentPlanError(f"Held-out plan is invalid JSON: {error.msg}") from error
    if not isinstance(plan, dict):
        raise ExperimentPlanError("Held-out plan must be a JSON object.")

    expected_digest = config.HELDOUT_PLAN_EXPECTED_DIGEST
    if plan.get("pinned_expected_plan_digest") != expected_digest:
        raise ExperimentPlanError("Held-out plan digest pin does not match configuration.")
    computed_digest = hashlib.sha256(
        _canonical_json_bytes(_remove_plan_digests(plan))
    ).hexdigest()
    if plan.get("plan_digest") != expected_digest or computed_digest != expected_digest:
        raise ExperimentPlanError("Held-out plan digest does not match its pin.")

    current_plan = build_heldout_plan()
    if _remove_plan_digests(plan) != _remove_plan_digests(current_plan):
        raise ExperimentPlanError(
            "Held-out plan does not match the current dataset snapshot, prompts, or config."
        )
    coverage = plan.get("coverage", {}).get("test", {})
    expected_keys = coverage.get("expected_observation_keys")
    if (
        not isinstance(expected_keys, list)
        or len(expected_keys) != 900
        or len(set(expected_keys)) != 900
        or coverage.get("expected_observation_count") != 900
        or coverage.get("run_numbers") != [1, 2, 3]
    ):
        raise ExperimentPlanError("Held-out plan must define exactly 900 unique observations.")
    plan_ids = plan.get("dataset", {}).get("record_ids")
    expected_ids = [f"TEST-{index:02d}" for index in range(1, 61)]
    if plan_ids != expected_ids:
        raise ExperimentPlanError("Held-out plan record IDs are not TEST-01 through TEST-60.")
    return plan


def load_development_plan(path: str | Path | None = None) -> dict[str, Any]:
    """Load and verify the current approved development plan."""
    plan_path = Path(path) if path is not None else config.DEVELOPMENT_PLAN_PATH
    if not plan_path.exists():
        raise ExperimentPlanError(f"Development plan file is missing: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ExperimentPlanError(f"Development plan is invalid JSON: {error.msg}") from error
    if not isinstance(plan, dict):
        raise ExperimentPlanError("Development plan must be a JSON object.")

    plan_digest = plan.get("plan_digest")
    if not isinstance(plan_digest, str):
        raise ExperimentPlanError("Plan digest is missing from the approved plan.")

    expected_plan_digest = plan.get("pinned_expected_plan_digest")
    if expected_plan_digest != config.DEVELOPMENT_PLAN_EXPECTED_DIGEST:
        raise ExperimentPlanError(
            "Approved plan digest pin does not match the configuration pin."
        )

    computed_digest = hashlib.sha256(
        _canonical_json_bytes(_remove_plan_digests(plan))
    ).hexdigest()
    if computed_digest != plan_digest:
        raise ExperimentPlanError("Approved plan digest does not match the canonical plan.")
    if plan_digest != config.DEVELOPMENT_PLAN_EXPECTED_DIGEST:
        raise ExperimentPlanError("Approved plan digest does not match the expected pinned digest.")

    dataset_path = config.DATA_DIR / "dev.jsonl"
    current_dataset_hash = _sha256_file(dataset_path)
    if plan["dataset"]["sha256"] != current_dataset_hash:
        raise ExperimentPlanError(
            "Development plan dataset hash does not match the current dev.jsonl snapshot."
        )

    for prompt_id, prompt_info in plan.get("prompts", {}).items():
        prompt_path = config.PROJECT_ROOT / prompt_info["path"]
        if _sha256_file(prompt_path) != prompt_info["sha256"]:
            raise ExperimentPlanError(
                f"Prompt file hash mismatch for {prompt_id}: {prompt_path}"
            )

    model = plan.get("model", {})
    if model.get("name") != config.MODEL_NAME:
        raise ExperimentPlanError("Development plan model does not match config.MODEL_NAME.")
    if model.get("temperature") != config.TEMPERATURE:
        raise ExperimentPlanError("Development plan temperature does not match config.TEMPERATURE.")
    if model.get("max_tokens") != config.MAX_TOKENS:
        raise ExperimentPlanError("Development plan max_tokens does not match config.MAX_TOKENS.")

    coverage = plan.get("coverage", {}).get("dev", {})
    if not coverage:
        raise ExperimentPlanError("Development plan is missing the dev coverage block.")
    expected_run_numbers = coverage.get("run_numbers")
    if expected_run_numbers != [1]:
        raise ExperimentPlanError("Development plan expects an unsupported run grid.")

    expected_keys = set(coverage.get("expected_observation_keys", []))
    if len(expected_keys) != coverage.get("expected_observation_count"):
        raise ExperimentPlanError("Development plan coverage keys are not unique.")
    return plan


def load_experiment_plan(
    split: str,
    path: str | Path | None = None,
) -> dict[str, Any]:
    if split == "dev":
        return load_development_plan(path)
    if split == "test":
        return load_heldout_plan(path)
    raise ExperimentPlanError(f"Unsupported experiment split: {split!r}.")


def validate_result_metadata_against_plan(
    metadata: dict[str, Any],
    *,
    plan: dict[str, Any] | None = None,
) -> None:
    plan = load_development_plan() if plan is None else plan
    if not isinstance(metadata, dict):
        raise ExperimentPlanError("Result metadata must be a dictionary.")

    if metadata.get("plan_digest") != plan["plan_digest"]:
        raise ExperimentPlanError("Result metadata does not match the approved plan digest.")
    if metadata.get("dataset_snapshot_sha256") != plan["dataset"]["sha256"]:
        raise ExperimentPlanError("Result metadata uses the wrong dataset snapshot hash.")
    if metadata.get("model") != plan["model"]["name"]:
        raise ExperimentPlanError("Result metadata model does not match the approved plan.")
    if metadata.get("temperature") != plan["model"]["temperature"]:
        raise ExperimentPlanError("Result metadata temperature does not match the approved plan.")
    if metadata.get("max_tokens") != plan["model"]["max_tokens"]:
        raise ExperimentPlanError("Result metadata max_tokens does not match the approved plan.")
    if metadata.get("reasoning_effort") != plan["model"]["reasoning_effort"]:
        raise ExperimentPlanError("Result metadata reasoning_effort does not match the approved plan.")
    if metadata.get("reasoning_format") != plan["model"]["reasoning_format"]:
        raise ExperimentPlanError("Result metadata reasoning_format does not match the approved plan.")

    prompt_hashes = metadata.get("prompt_sha256")
    if not isinstance(prompt_hashes, dict):
        raise ExperimentPlanError("Result metadata prompt_sha256 is missing.")
    for prompt_id, prompt_info in plan["prompts"].items():
        if prompt_hashes.get(prompt_id) != prompt_info["sha256"]:
            raise ExperimentPlanError(
                f"Result metadata prompt hash mismatch for {prompt_id}."
            )


def plan_expected_run_numbers(split: str, plan: dict[str, Any] | None = None) -> list[int]:
    plan = load_experiment_plan(split) if plan is None else plan
    coverage = plan.get("coverage", {}).get(split)
    if coverage is None:
        raise ExperimentPlanError(f"No plan coverage is defined for split {split!r}.")
    run_numbers = coverage.get("run_numbers")
    if not isinstance(run_numbers, list) or not run_numbers:
        raise ExperimentPlanError(f"Plan coverage for {split!r} does not define run numbers.")
    if any(type(value) is not int or value < 1 for value in run_numbers):
        raise ExperimentPlanError(f"Plan coverage for {split!r} contains invalid run numbers.")
    return list(run_numbers)


def plan_expected_keys(split: str, plan: dict[str, Any] | None = None) -> list[str]:
    plan = load_experiment_plan(split) if plan is None else plan
    coverage = plan.get("coverage", {}).get(split)
    if coverage is None:
        raise ExperimentPlanError(f"No plan coverage is defined for split {split!r}.")
    expected_keys = coverage.get("expected_observation_keys", [])
    if not isinstance(expected_keys, list) or not expected_keys:
        raise ExperimentPlanError(f"Plan coverage for {split!r} has no expected keys.")
    if len(set(expected_keys)) != len(expected_keys):
        raise ExperimentPlanError(f"Plan coverage for {split!r} contains duplicate keys.")
    if len(expected_keys) != coverage.get("expected_observation_count"):
        raise ExperimentPlanError(f"Plan coverage for {split!r} has an incorrect key count.")
    return list(expected_keys)


def plan_result_metadata(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = load_development_plan() if plan is None else plan
    metadata = {
        "plan_digest": plan["plan_digest"],
        "dataset_snapshot_sha256": plan["dataset"]["sha256"],
        "model": plan["model"]["name"],
        "temperature": plan["model"]["temperature"],
        "max_tokens": plan["model"]["max_tokens"],
        "reasoning_effort": plan["model"]["reasoning_effort"],
        "reasoning_format": plan["model"]["reasoning_format"],
        "prompt_sha256": {
            prompt_id: prompt_info["sha256"] for prompt_id, prompt_info in plan["prompts"].items()
        },
        "plan_version": plan["schema_version"],
    }
    return metadata
