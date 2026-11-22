import json
from pathlib import Path

import pytest

from src import config
import src.evaluate as evaluator
from src.evaluate import evaluate_split
from src.experiment_plan import load_development_plan, plan_expected_keys
from src.results_store import ResultsStore


def _result_for_key(split: str, record_id: str, prompt_id: str, run: int, *, label: dict):
    return {
        "split": split,
        "record_id": record_id,
        "prompt_id": prompt_id,
        "run": run,
        "raw_output": json.dumps(label, separators=(",", ":"), ensure_ascii=False),
        "parsed_json": label,
        "parse_ok": True,
        "schema_ok": True,
        "error": None,
        "input_tokens": 42,
        "output_tokens": 6,
        "reasoning_tokens": 2,
        "latency_seconds": 0.8,
        "timestamp_utc": "2026-11-14T00:00:00+00:00",
    }


def _build_valid_dev_store(path: Path):
    plan = load_development_plan()
    gold_records = []
    with (config.DATA_DIR / "dev.jsonl").open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            gold_records.append({
                "record_id": f"DEV-{line_number:02d}",
                "label": record["label"],
                "original_message": record["original_message"],
            })
    store = ResultsStore(path, {})
    expected_keys = plan_expected_keys("dev", plan)
    for key in expected_keys:
        _, record_id, prompt_id, run_text = key.split("|")
        run = int(run_text)
        label = next(r["label"] for r in gold_records if r["record_id"] == record_id)
        store.add_record(_result_for_key("dev", record_id, prompt_id, run, label=label))
    store.meta = {
        "plan_digest": plan["plan_digest"],
        "dataset_snapshot_sha256": plan["dataset"]["sha256"],
        "model": plan["model"]["name"],
        "temperature": plan["model"]["temperature"],
        "max_tokens": plan["model"]["max_tokens"],
        "reasoning_effort": plan["model"]["reasoning_effort"],
        "reasoning_format": plan["model"]["reasoning_format"],
        "prompt_sha256": {
            prompt_id: info["sha256"]
            for prompt_id, info in plan["prompts"].items()
        },
        "plan_version": plan["schema_version"],
    }
    return store, plan


def test_evaluate_split_requires_exact_plan_coverage(tmp_path):
    path = tmp_path / "results.json"
    store, plan = _build_valid_dev_store(path)
    store.save()

    rows = evaluate_split("dev", results_path=path, plan=plan)
    assert rows
    assert any(row["split"] == "dev" for row in rows)

    extra = {
        "split": "dev",
        "record_id": "DEV-21",
        "prompt_id": "A",
        "run": 1,
        "raw_output": '{"claim_type": "own_damage"}',
        "parsed_json": {"claim_type": "own_damage"},
        "parse_ok": True,
        "schema_ok": True,
        "error": None,
        "input_tokens": 42,
        "output_tokens": 6,
        "reasoning_tokens": 2,
        "latency_seconds": 0.8,
        "timestamp_utc": "2026-11-14T00:00:00+00:00",
    }
    store.add_record(extra)
    store.save()
    with pytest.raises(ValueError, match="Unexpected result keys"):
        evaluate_split("dev", results_path=path, plan=plan)


def test_evaluate_split_rejects_missing_required_keys(tmp_path):
    path = tmp_path / "results.json"
    store, plan = _build_valid_dev_store(path)
    missing_key = next(iter(plan_expected_keys("dev", plan)))
    _, record_id, prompt_id, run_text = missing_key.split("|")
    key_to_delete = f"dev|{record_id}|{prompt_id}|{run_text}"
    del store.records[key_to_delete]
    store.save()

    with pytest.raises(ValueError, match="Missing required result keys"):
        evaluate_split("dev", results_path=path, plan=plan)


def test_evaluate_split_pubishes_immutable_bundle_with_manifest(tmp_path, monkeypatch):
    path = tmp_path / "results.json"
    store, plan = _build_valid_dev_store(path)
    store.save()

    output_root = tmp_path / "outputs"
    monkeypatch.setattr(config, "OUTPUTS_DIR", output_root)

    rows = evaluate_split("dev", results_path=path, plan=plan)
    assert rows
    bundles = sorted((output_root / "evaluations").glob("*"))
    assert len(bundles) == 1
    manifest = json.loads((bundles[0] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["plan_digest"] == plan["plan_digest"]
    assert manifest["output_hashes"]["metrics.json"]
    assert manifest["output_hashes"]["results.csv"]
    assert (bundles[0] / "metrics.json").exists()
    assert (bundles[0] / "results.csv").exists()


def test_evaluate_cli_accepts_the_separate_results_store(tmp_path, monkeypatch):
    results_path = tmp_path / "development-results.json"
    calls = []
    plan = {"split": "dev"}
    monkeypatch.setattr(
        evaluator,
        "load_experiment_plan",
        lambda split, path=None: plan,
    )

    monkeypatch.setattr(
        evaluator,
        "evaluate_split",
        lambda split, *, results_path=None, plan=None: calls.append(
            (split, results_path, plan)
        ),
    )

    assert evaluator.main([
        "--split", "dev",
        "--results", str(results_path),
    ]) == 0
    assert calls == [("dev", results_path, plan)]
