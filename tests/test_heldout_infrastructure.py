import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import config
from src.experiment_plan import (
    build_heldout_plan,
    load_heldout_plan,
    plan_result_metadata,
)
from src.prompt_helpers import PROMPT_FILES
from src.results_store import ResultsStore
import src.evaluate as evaluator
import src.experiment_runtime as runtime
import src.run_experiment as runner


def _synthetic_dataset(path: Path, count: int = 60) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for index in range(count):
            output.write(json.dumps({
                "original_message": f"Synthetic message {index + 1}",
                "label": {"synthetic_label": index + 1},
            }) + "\n")


def _synthetic_plan(record_ids: list[str] | None = None) -> dict:
    record_ids = record_ids or [f"TEST-{index:02d}" for index in range(1, 61)]
    run_numbers = [1, 2, 3]
    keys = [
        f"test|{record_id}|{prompt_id}|{run}"
        for record_id in record_ids
        for prompt_id in PROMPT_FILES
        for run in run_numbers
    ]
    return {
        "schema_version": "heldout-eval-plan/v1",
        "plan_name": "synthetic-heldout-plan",
        "split": "test",
        "dataset": {
            "path": "synthetic/test.jsonl",
            "sha256": "synthetic-dataset-hash",
            "record_ids": record_ids,
            "record_count": len(record_ids),
        },
        "prompts": {
            prompt_id: {"path": filename, "sha256": f"sha-{prompt_id}"}
            for prompt_id, filename in PROMPT_FILES.items()
        },
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
                "expected_observation_keys": keys,
                "expected_observation_count": len(keys),
            }
        },
        "plan_digest": "synthetic-plan-digest",
        "pinned_expected_plan_digest": "synthetic-plan-digest",
    }


def _prediction_record(record_id: str, prompt_id: str, run: int, label: dict) -> dict:
    return {
        "split": "test",
        "record_id": record_id,
        "prompt_id": prompt_id,
        "run": run,
        "raw_output": json.dumps(label),
        "parsed_json": label,
        "parse_ok": True,
        "schema_ok": True,
        "error": None,
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 0,
        "latency_seconds": 0.1,
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
    }


def test_heldout_plan_pins_synthetic_snapshot_and_expands_exact_coverage(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    dataset_path = data_dir / "test.jsonl"
    _synthetic_dataset(dataset_path)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for filename in PROMPT_FILES.values():
        (prompts_dir / filename).write_text(
            "Synthetic prompt {{CLAIM_MESSAGE}}\n",
            encoding="utf-8",
        )
    plan_path = tmp_path / "plans" / "heldout_test_plan_v1.json"

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr(config, "HELDOUT_PLAN_PATH", plan_path)
    plan = build_heldout_plan()
    plan_path.parent.mkdir()
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(config, "HELDOUT_PLAN_EXPECTED_DIGEST", plan["plan_digest"])

    loaded = load_heldout_plan()

    keys = loaded["coverage"]["test"]["expected_observation_keys"]
    assert loaded["dataset"]["record_ids"] == [
        f"TEST-{index:02d}" for index in range(1, 61)
    ]
    assert loaded["dataset"]["record_count"] == 60
    assert len(keys) == 900
    assert len(set(keys)) == 900
    assert keys[0] == "test|TEST-01|A|1"
    assert keys[-1] == "test|TEST-60|C|3"


def test_heldout_plan_rejects_changed_synthetic_snapshot(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    dataset_path = data_dir / "test.jsonl"
    _synthetic_dataset(dataset_path)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for filename in PROMPT_FILES.values():
        (prompts_dir / filename).write_text("Synthetic prompt\n", encoding="utf-8")
    plan_path = tmp_path / "heldout_test_plan_v1.json"

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr(config, "HELDOUT_PLAN_PATH", plan_path)
    plan = build_heldout_plan()
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(config, "HELDOUT_PLAN_EXPECTED_DIGEST", plan["plan_digest"])
    dataset_path.write_text(
        dataset_path.read_text(encoding="utf-8").replace(
            "Synthetic message 1", "Changed synthetic message 1"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="current dataset snapshot"):
        load_heldout_plan()


def test_inference_loader_skips_label_values_and_validation(tmp_path, monkeypatch):
    message = "Synthetic message only."
    huge_number = "9" * 5000
    dataset_path = tmp_path / "synthetic.jsonl"
    dataset_path.write_text(
        '{"label":{"sentinel":' + huge_number +
        '},"original_message":' + json.dumps(message) + '}\n',
        encoding="utf-8",
    )
    original_raw_decode = json.JSONDecoder.raw_decode

    def reject_label_decoding(decoder, text, index=0):
        if text.startswith(huge_number, index):
            raise AssertionError("A label value was decoded.")
        return original_raw_decode(decoder, text, index)

    monkeypatch.setattr(json.JSONDecoder, "raw_decode", reject_label_decoding)
    monkeypatch.setattr(
        runtime,
        "validate_record",
        lambda *_: pytest.fail("Inference must not validate labels."),
    )

    records = runtime.load_inference_records(
        "test",
        ["TEST-01"],
        path=dataset_path,
        expected_dataset_sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
    )

    assert records == [{
        "split": "test",
        "record_id": "TEST-01",
        "message": message,
    }]


def test_heldout_dry_run_checks_900_keys_without_loading_data_or_store(
    tmp_path, monkeypatch, capsys
):
    plan = _synthetic_plan()
    run_dir = tmp_path / "outputs" / "heldout_runs" / "heldout-test-synthetic"
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "HELDOUT_RUN_DIR", run_dir)
    monkeypatch.setattr(config, "HELDOUT_RESULTS_PATH", run_dir / "results.json")
    monkeypatch.setattr(config, "HELDOUT_RETRY_LOG_PATH", run_dir / "retry_log.jsonl")
    monkeypatch.setattr(config, "DEVELOPMENT_RESULTS_PATH", tmp_path / "dev" / "results.json")
    monkeypatch.setattr(config, "DEVELOPMENT_RETRY_LOG_PATH", tmp_path / "dev" / "retry.jsonl")
    monkeypatch.setattr(runner, "LEGACY_RESULTS_PATH", tmp_path / "raw_responses.json")
    monkeypatch.setattr(
        runner,
        "load_experiment_plan",
        lambda split: plan,
    )
    monkeypatch.setattr(
        runner,
        "load_inference_records",
        lambda *_args, **_kwargs: pytest.fail("Dry-run must not load records."),
    )
    monkeypatch.setattr(
        runner.ResultsStore,
        "load",
        staticmethod(lambda *_args, **_kwargs: pytest.fail("Dry-run must not load a store.")),
    )
    monkeypatch.setattr(
        runner,
        "create_groq_client",
        lambda **_kwargs: pytest.fail("Dry-run must not create a client."),
    )

    assert runner.main([
        "--split", "test",
        "--prompts", "A", "B-k3", "B-k5", "B-k8", "C",
        "--runs", "3",
        "--dry-run",
    ]) == 0

    output = capsys.readouterr().out
    assert "Expected held-out observations: 900 unique keys" in output
    assert "Development and legacy result paths: not used" in output
    assert not run_dir.exists()


def test_heldout_failed_record_retry_never_replaces_success(tmp_path):
    target = _prediction_record("TEST-01", "A", 1, {"claim_type": "synthetic"})
    target.update(parse_ok=False, schema_ok=False, error="HTTP 500")
    store = ResultsStore(tmp_path / "heldout-results.json", {})
    store.add_record(target)

    retried = _prediction_record("TEST-01", "A", 1, {"claim_type": "corrected"})
    store.replace_unsuccessful_record(retried)
    assert store.records["test|TEST-01|A|1"] == retried

    with pytest.raises(ValueError, match="unsuccessful result"):
        store.replace_unsuccessful_record(target)


def test_heldout_retry_selection_is_exact_and_normal_resume_skips_stored_keys(
    tmp_path,
):
    store = ResultsStore(tmp_path / "heldout-results.json", {})
    failed = _prediction_record("TEST-01", "A", 1, {"claim_type": "synthetic"})
    failed.update(parse_ok=False, schema_ok=False, error="HTTP 500")
    successful = _prediction_record("TEST-02", "A", 1, {"claim_type": "synthetic"})
    store.add_record(failed)
    store.add_record(successful)
    retry_key = "test|TEST-01|A|1"

    assert runner._should_queue_key(retry_key, store, {retry_key}, None)
    assert not runner._should_queue_key("test|TEST-01|B-k3|1", store, {retry_key}, None)
    assert not runner._should_queue_key("test|TEST-02|A|1", store, set(), None)
    assert runner._should_queue_key("test|TEST-03|A|1", store, set(), None)


def test_runtime_retries_only_the_selected_heldout_failure(
    tmp_path, monkeypatch
):
    fake_groq = type(sys)("groq")
    fake_groq.APIStatusError = type("APIStatusError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "groq", fake_groq)
    target = _prediction_record("TEST-01", "A", 1, {"claim_type": "bad"})
    target.update(parse_ok=False, schema_ok=False, error="HTTP 500")
    store = ResultsStore(tmp_path / "heldout-results.json", {})
    store.add_record(target)
    response_json = {
        "claim_type": "own_damage",
        "urgency": "low",
        "policy_number": None,
        "incident_date": None,
        "location": None,
        "vehicle_reg_no": None,
        "estimated_amount_inr": None,
        "injury_reported": None,
        "police_report_filed": None,
    }

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(
                            content=json.dumps(response_json)
                        ))],
                        usage=None,
                    )

    key = "test|TEST-01|A|1"
    pending = [{
        "split": "test",
        "record_id": "TEST-01",
        "prompt_id": "A",
        "run": 1,
        "prompt_text": "Synthetic prompt.",
    }]
    assert runtime.run_pending(
        pending,
        FakeClient(),
        store,
        max_calls=1,
        output=lambda _message: None,
        sleep=lambda _seconds: None,
        retry_log_path=tmp_path / "retry_log.jsonl",
        retry_state={},
        no_daily_limit=True,
        retry_failed_keys={key},
    ) == 0

    assert store.records[key]["error"] is None
    assert store.records[key]["parse_ok"] is True
    assert store.records[key]["schema_ok"] is True
    assert len(store.records) == 1


def test_heldout_evaluator_writes_heldout_bundle_from_explicit_synthetic_store(
    tmp_path, monkeypatch
):
    plan = _synthetic_plan(["TEST-01"])
    label = {
        "claim_type": "own_damage",
        "urgency": "low",
        "policy_number": None,
        "incident_date": None,
        "location": None,
        "vehicle_reg_no": None,
        "estimated_amount_inr": None,
        "injury_reported": None,
        "police_report_filed": None,
    }
    store_path = tmp_path / "heldout-results.json"
    store = ResultsStore(store_path, plan_result_metadata(plan))
    for prompt_id in PROMPT_FILES:
        for run in (1, 2, 3):
            store.add_record(_prediction_record("TEST-01", prompt_id, run, label))
    store.save()

    monkeypatch.setattr(
        evaluator,
        "load_gold_records",
        lambda split: [{
            "record_id": "TEST-01",
            "label": label,
            "original_message": "Synthetic message.",
        }],
    )
    output_root = tmp_path / "outputs"
    monkeypatch.setattr(config, "OUTPUTS_DIR", output_root)
    monkeypatch.setattr(config, "HELDOUT_RESULTS_PATH", store_path)

    rows = evaluator.evaluate_split(
        "test",
        results_path=store_path,
        plan=plan,
    )

    assert len(rows) == len(PROMPT_FILES)
    bundle_dir = next((output_root / "evaluations").iterdir())
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "heldout-eval-bundle/v1"
    assert manifest["split"] == "test"
    assert manifest["plan_digest"] == plan["plan_digest"]


def test_heldout_evaluator_requires_explicit_results_store():
    with pytest.raises(ValueError, match="explicit isolated results store"):
        evaluator.evaluate_split(
            "test",
            plan=_synthetic_plan(["TEST-01"]),
        )


def test_heldout_evaluator_cli_requires_plan_and_explicit_store(
    tmp_path, monkeypatch
):
    plan = _synthetic_plan(["TEST-01"])
    calls = []
    monkeypatch.setattr(
        evaluator,
        "load_experiment_plan",
        lambda split, path=None: plan if split == "test" else None,
    )
    monkeypatch.setattr(
        evaluator,
        "evaluate_split",
        lambda split, *, results_path=None, plan=None: calls.append(
            (split, results_path, plan)
        ),
    )

    assert evaluator.main([
        "--split", "test",
        "--plan", str(tmp_path / "synthetic-plan.json"),
        "--results", str(tmp_path / "synthetic-results.json"),
    ]) == 0
    assert calls == [
        ("test", tmp_path / "synthetic-results.json", plan),
    ]
