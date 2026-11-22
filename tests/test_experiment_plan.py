import hashlib
import json

import pytest

from src import config
from src.experiment_plan import (
    ExperimentPlanError,
    load_development_plan,
    plan_expected_keys,
    plan_result_metadata,
    validate_result_metadata_against_plan,
)
from src.prompt_helpers import PROMPT_FILES


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def test_development_plan_matches_current_config_and_dataset():
    plan = load_development_plan()
    dataset_path = config.DATA_DIR / "dev.jsonl"
    record_ids = []
    with dataset_path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            json.loads(line)
            record_ids.append(f"DEV-{line_number:02d}")

    assert plan["dataset"]["record_ids"] == record_ids
    assert plan["dataset"]["sha256"] == _sha256_file(dataset_path)
    assert plan["model"]["name"] == config.MODEL_NAME
    assert plan["model"]["temperature"] == config.TEMPERATURE
    assert plan["model"]["max_tokens"] == config.MAX_TOKENS
    assert plan["model"]["reasoning_effort"] == config.REASONING_EFFORT
    assert plan["model"]["reasoning_format"] == config.REASONING_FORMAT
    for prompt_id, filename in PROMPT_FILES.items():
        prompt_path = config.PROMPTS_DIR / filename
        assert plan["prompts"][prompt_id]["sha256"] == _sha256_file(prompt_path)
    assert plan["coverage"]["dev"]["run_numbers"] == [1]
    assert plan["coverage"]["dev"]["expected_observation_count"] == len(
        plan["coverage"]["dev"]["expected_observation_keys"]
    )
    assert len(plan_expected_keys("dev", plan)) == len(set(plan_expected_keys("dev", plan)))
    assert plan["plan_digest"] == plan["pinned_expected_plan_digest"]
    assert plan["plan_digest"] == config.DEVELOPMENT_PLAN_EXPECTED_DIGEST


def test_result_metadata_rejects_tampering():
    plan = load_development_plan()
    meta = plan_result_metadata(plan)
    meta["prompt_sha256"]["A"] = "tampered-value"

    with pytest.raises(ExperimentPlanError, match="prompt hash mismatch"):
        validate_result_metadata_against_plan(meta, plan=plan)


def test_result_metadata_requires_all_prompt_hashes():
    plan = load_development_plan()
    meta = plan_result_metadata(plan)
    meta["prompt_sha256"].pop("C")

    with pytest.raises(ExperimentPlanError, match="prompt hash mismatch"):
        validate_result_metadata_against_plan(meta, plan=plan)


def test_plan_rejects_missing_or_extra_dataset_hash():
    plan = load_development_plan()
    meta = plan_result_metadata(plan)
    meta["dataset_snapshot_sha256"] = "bad-hash"

    with pytest.raises(ExperimentPlanError, match="wrong dataset snapshot"):
        validate_result_metadata_against_plan(meta, plan=plan)
