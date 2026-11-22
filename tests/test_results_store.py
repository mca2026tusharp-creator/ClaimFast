import json

from src.results_store import (
    RETRYABLE_FAILED_OBSERVATION_KEY,
    ResultsStore,
    migrate_observations,
    result_key,
)


def _observation(record_id="DEV-01", prompt_id="A", run_number=1):
    return {
        "record_id": record_id,
        "prompt_id": prompt_id,
        "run_number": run_number,
        "raw_model_response": '{"claim_type":"own_damage"}',
        "parsed_json": {"claim_type": "own_damage"},
        "json_parse_valid": True,
        "json_valid": True,
        "validation_errors": [],
        "input_tokens": 12,
        "output_tokens": 3,
        "reasoning_tokens": 2,
        "latency_seconds": 0.25,
        "timestamp_utc": "2026-11-13T00:00:00+00:00",
    }


def _record(record_id="DEV-01", prompt_id="A", run=1):
    return {
        "split": "dev",
        "record_id": record_id,
        "prompt_id": prompt_id,
        "run": run,
        "raw_output": '{"claim_type":"own_damage"}',
        "parsed_json": {"claim_type": "own_damage"},
        "parse_ok": True,
        "schema_ok": True,
        "error": None,
        "input_tokens": 12,
        "output_tokens": 3,
        "reasoning_tokens": 2,
        "latency_seconds": 0.25,
        "timestamp_utc": "2026-11-13T00:00:00+00:00",
    }


def test_atomic_save_replaces_file_with_complete_pretty_json(tmp_path, monkeypatch):
    path = tmp_path / "raw_responses.json"
    store = ResultsStore(path, {"model": "test"})
    store.add_record(_record())
    replaces = []
    real_replace = __import__("os").replace

    def checked_replace(source, destination):
        source = tmp_path / source if not str(source).startswith("/") else source
        assert source.exists()
        assert source.parent == path.parent
        replaces.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr("src.results_store.os.replace", checked_replace)
    store.save()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["meta"] == {"model": "test"}
    assert result_key(_record()) in saved["records"]
    assert len(replaces) == 1
    assert not list(tmp_path.glob("*.tmp"))
    assert "\n  \"meta\"" in path.read_text(encoding="utf-8")


def test_resume_loads_records_and_skips_existing_key(tmp_path):
    path = tmp_path / "raw_responses.json"
    store = ResultsStore(path, {"model": "test"})
    store.add_record(_record())
    store.save()

    resumed = ResultsStore.load(path)

    assert resumed.has_key("dev|DEV-01|A|1")
    assert not resumed.has_key("dev|DEV-01|A|2")


def test_migration_preserves_fields_and_rejects_duplicate_observation(tmp_path):
    store = ResultsStore(tmp_path / "raw_responses.json", {"model": "test"})
    source = [_observation(), _observation("DEV-01", "B-k3", 2)]

    migrated = migrate_observations(store, source, split="dev")

    assert migrated == 2
    assert len(store.records) == 2
    record = store.records["dev|DEV-01|A|1"]
    assert record["raw_output"] == source[0]["raw_model_response"]
    assert record["parsed_json"] == source[0]["parsed_json"]
    assert record["parse_ok"] is True
    assert record["schema_ok"] is True
    assert record["error"] is None
    assert record["reasoning_tokens"] == 2

    try:
        migrate_observations(store, [_observation()], split="dev")
    except ValueError as error:
        assert "Duplicate result key" in str(error)
    else:
        raise AssertionError("Duplicate migration key was accepted.")


def test_result_store_rejects_invalid_reasoning_token_count(tmp_path):
    store = ResultsStore(tmp_path / "raw_responses.json", {"model": "test"})
    record = _record()
    record["reasoning_tokens"] = "three"

    try:
        store.add_record(record)
    except ValueError as error:
        assert "reasoning token count" in str(error)
    else:
        raise AssertionError("Invalid reasoning token count was accepted.")


def test_retry_replacement_is_limited_and_never_overwrites_success(tmp_path):
    store = ResultsStore(tmp_path / "results.json", {"model": "test"})
    target_failed = _record("DEV-18", "C")
    target_failed.update(parse_ok=False, schema_ok=False, error="HTTP 500")
    store.add_record(target_failed)

    retry_success = _record("DEV-18", "C")
    assert result_key(retry_success) == RETRYABLE_FAILED_OBSERVATION_KEY
    store.replace_retryable_failed_record(retry_success)
    assert store.records[RETRYABLE_FAILED_OBSERVATION_KEY] == retry_success

    try:
        store.replace_retryable_failed_record(target_failed)
    except ValueError as error:
        assert "unsuccessful result" in str(error)
    else:
        raise AssertionError("A successful result was overwritten.")

    unrelated = _record("DEV-17", "C")
    try:
        store.replace_retryable_failed_record(unrelated)
    except ValueError as error:
        assert "not retryable" in str(error)
    else:
        raise AssertionError("An unrelated observation was replaceable.")

    assert store.records[RETRYABLE_FAILED_OBSERVATION_KEY] == retry_success
