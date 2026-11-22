import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from groq import APIStatusError

import src.run_experiment as runner
from src import config
from src.experiment_runtime import load_retry_state, run_pending
from src.prompt_helpers import build_results_meta
from src.results_store import ResultsStore


def _item(prompt_id="A", run=1):
    return {
        "split": "dev",
        "record_id": "DEV-01",
        "prompt_id": prompt_id,
        "run": run,
        "prompt_text": "Rendered test prompt.",
    }


def _response():
    content = json.dumps({
        "claim_type": "own_damage",
        "urgency": "medium",
        "policy_number": None,
        "incident_date": None,
        "location": None,
        "vehicle_reg_no": None,
        "estimated_amount_inr": None,
        "injury_reported": None,
        "police_report_filed": None,
    })
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=content, reasoning="do not persist this reasoning text"
        ))],
        usage=SimpleNamespace(
            prompt_tokens=20,
            completion_tokens=5,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
    )


def _status_error(status, headers=None):
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://api.groq.com"),
        headers=headers,
    )
    return APIStatusError(f"status {status}", response=response, body={})


def _meta(hash_value="new"):
    return {
        "model": "test-model",
        "temperature": 0,
        "max_tokens": 10,
        "reasoning_effort": "low",
        "reasoning_format": "hidden",
        "prompt_sha256": {
            prompt_id: hash_value for prompt_id in runner.PROMPT_FILES
        },
        "created_utc": "2026-11-13T00:00:00+00:00",
    }


def _setup_cli(monkeypatch, results_path: Path, *, records=None):
    monkeypatch.setattr(runner, "RESULTS_PATH", results_path)
    monkeypatch.setattr(runner, "build_results_meta", lambda: _meta())
    monkeypatch.setattr(
        runner,
        "load_records",
        lambda split: records if records is not None else [{
            "split": split,
            "record_id": "DEV-01",
            "message": "Synthetic claim message.",
        }],
    )
    monkeypatch.setattr(
        runner,
        "load_prompts",
        lambda prompt_ids: {"A": "Prompt: {{CLAIM_MESSAGE}}"},
    )


def test_runner_skips_keys_already_in_results(tmp_path):
    store = ResultsStore(tmp_path / "results.json", {"model": "test"})
    item = _item()
    existing = {
        "split": "dev",
        "record_id": "DEV-01",
        "prompt_id": "A",
        "run": 1,
        "raw_output": None,
        "parsed_json": None,
        "parse_ok": False,
        "schema_ok": False,
        "error": "HTTP 500",
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "latency_seconds": 0.1,
        "timestamp_utc": "2026-11-12T00:00:00+00:00",
    }
    store.add_record(existing)
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _response()

    assert run_pending(
        [item],
        Client(),
        store,
        output=lambda _: None,
        sleep=lambda _: None,
        retry_log_path=tmp_path / "retry_log.jsonl",
    ) == 0
    assert calls == []
    assert len(store.records) == 1


def test_retry_selection_allows_only_the_exact_failed_key(
    tmp_path, monkeypatch, capsys
):
    results_path = tmp_path / "development-results.json"
    records = [
        {"split": "dev", "record_id": f"DEV-{number:02d}", "message": "Synthetic."}
        for number in range(1, 21)
    ]
    target_key = "dev|DEV-18|C|1"
    missing_keys = {
        "dev|DEV-13|B-k8|1",
        "dev|DEV-20|C|1",
    }
    expected_keys = set(runner.plan_expected_keys("dev"))
    assert target_key in expected_keys
    assert missing_keys.issubset(expected_keys)

    def stored_result(key, successful):
        split, record_id, prompt_id, run = key.split("|")
        return {
            "split": split,
            "record_id": record_id,
            "prompt_id": prompt_id,
            "run": int(run),
            "raw_output": "synthetic result",
            "parsed_json": {},
            "parse_ok": successful,
            "schema_ok": successful,
            "error": None if successful else "HTTP 500",
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "latency_seconds": 0.1,
            "timestamp_utc": "2026-11-12T00:00:00+00:00",
        }

    store = ResultsStore(results_path, _meta())
    for key in sorted(expected_keys - missing_keys):
        store.add_record(stored_result(key, key != target_key))
    assert len(store.records) == 98
    store.save()
    original_bytes = results_path.read_bytes()

    _setup_cli(monkeypatch, results_path, records=records)
    monkeypatch.setattr(
        runner,
        "load_prompts",
        lambda _: {
            prompt_id: "Prompt: {{CLAIM_MESSAGE}}"
            for prompt_id in ("A", "B-k3", "B-k5", "B-k8", "C")
        },
    )
    monkeypatch.setattr(
        runner,
        "create_groq_client",
        lambda **_: pytest.fail("A dry run must not create a provider client."),
    )
    selected_keys = []
    should_queue = runner._should_queue_key

    def record_selection(key, store, retry_failed_key, exclude_key):
        selected = should_queue(key, store, retry_failed_key, exclude_key)
        if selected:
            selected_keys.append(key)
        return selected

    monkeypatch.setattr(runner, "_should_queue_key", record_selection)

    assert runner.main([
        "--split", "dev",
        "--prompts", "A", "B-k3", "B-k5", "B-k8", "C",
        "--runs", "1",
        "--dry-run",
    ]) == 0
    assert "Total planned calls: 2" in capsys.readouterr().out
    assert set(selected_keys) == missing_keys
    selected_keys.clear()

    assert runner.main([
        "--split", "dev",
        "--prompts", "A", "B-k3", "B-k5", "B-k8", "C",
        "--runs", "1",
        "--retry-failed-key", target_key,
        "--dry-run",
    ]) == 0
    assert "Total planned calls: 1" in capsys.readouterr().out
    assert selected_keys == [target_key]
    assert not (missing_keys & set(selected_keys))
    assert results_path.read_bytes() == original_bytes
    assert ResultsStore.load(results_path).records == store.records


def test_runner_retries_429_then_saves_only_final_result(tmp_path, monkeypatch):
    from src import config

    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 2)
    store = ResultsStore(tmp_path / "results.json", {"model": "test"})
    delays = []
    calls = []
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]

    def sleep(seconds):
        delays.append(seconds)
        now[0] += timedelta(seconds=seconds)

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    if len(calls) <= 2:
                        raise _status_error(429)
                    return _response()

    original_add = store.add_record
    adds_during_call = []

    def observe_add(record):
        adds_during_call.append(len(calls))
        original_add(record)

    store.add_record = observe_add
    assert run_pending(
        [_item()],
        Client(),
        store,
        output=lambda _: None,
        sleep=sleep,
        retry_log_path=tmp_path / "retry_log.jsonl",
        utc_now=lambda: now[0],
    ) == 0

    assert len(calls) == 3
    assert delays == [5, 10]
    assert all(call["reasoning_effort"] == "low" for call in calls)
    assert all(call["reasoning_format"] == "hidden" for call in calls)
    assert adds_during_call == [3]
    saved = ResultsStore.load(store.path)
    assert saved.records["dev|DEV-01|A|1"]["error"] is None
    assert saved.records["dev|DEV-01|A|1"]["input_tokens"] == 20
    result = saved.records["dev|DEV-01|A|1"]
    assert result["reasoning_tokens"] == 3
    assert "reasoning" not in result
    assert "do not persist this reasoning text" not in json.dumps(result)


def test_429_retry_event_captures_only_sanitized_allowlisted_metadata(tmp_path):
    headers = {
        "Retry-After": "17",
        "X-RateLimit-Remaining-Requests": "12",
        "X-RateLimit-Remaining-Tokens": "8400",
        "X-RateLimit-Reset-Requests": "1m0s",
        "X-RateLimit-Reset-Tokens": "42s",
        "X-Request-Id": "req_test-001",
        "Authorization": "Bearer do-not-store-this",
        "X-Unrelated": "unrelated-header-value",
    }
    error = APIStatusError(
        "synthetic 429 body must not be stored",
        response=httpx.Response(
            429,
            request=httpx.Request("POST", "https://api.groq.com"),
            headers=headers,
        ),
        body={"message": "synthetic body marker"},
    )
    retry_path = tmp_path / "retry_log.jsonl"

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise error

    assert run_pending(
        [_item()],
        Client(),
        ResultsStore(tmp_path / "results.json", {"model": "test"}),
        max_calls=1,
        output=lambda _: None,
        sleep=lambda _: None,
        retry_log_path=retry_path,
    ) == 0

    event = json.loads(retry_path.read_text(encoding="utf-8"))
    assert event["http_status"] == 429
    assert event["error_class"] == "rate_limited"
    assert event["retry_after_raw"] == "17"
    assert event["retry_after_seconds"] == 17
    assert event["wait_seconds"] == 17
    assert event["wait_source"] == "header"
    assert event["rate_limit_headers"] == {
        "x-ratelimit-remaining-requests": "12",
        "x-ratelimit-remaining-tokens": "8400",
        "x-ratelimit-reset-requests": "1m0s",
        "x-ratelimit-reset-tokens": "42s",
    }
    assert event["request_id"] == "req_test-001"
    serialized = json.dumps(event)
    assert "do-not-store-this" not in serialized
    assert "unrelated-header-value" not in serialized
    assert "synthetic body marker" not in serialized
    assert load_retry_state(retry_path)["dev|DEV-01|A|1"].count == 1


def test_non_429_error_is_saved_and_runner_continues(tmp_path):
    store = ResultsStore(tmp_path / "results.json", {"model": "test"})
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    if len(calls) == 1:
                        raise _status_error(500)
                    return _response()

    assert run_pending(
        [_item("A", 1), _item("B-k3", 2)],
        Client(),
        store,
        output=lambda _: None,
        sleep=lambda _: None,
        retry_log_path=tmp_path / "retry_log.jsonl",
    ) == 0
    assert len(calls) == 2
    assert store.records["dev|DEV-01|A|1"]["error"] == "HTTP 500"
    assert store.records["dev|DEV-01|A|1"]["reasoning_tokens"] is None
    assert store.records["dev|DEV-01|B-k3|2"]["schema_ok"] is True


def test_results_metadata_records_reasoning_settings():
    meta = build_results_meta()

    assert meta["reasoning_effort"] == "low"
    assert meta["reasoning_format"] == "hidden"


def test_runs_default_comes_from_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_PER_PROMPT", 4)
    _setup_cli(monkeypatch, tmp_path / "raw_responses.json")

    assert runner.main(["--split", "dev", "--prompts", "A", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Runs per prompt: 4" in output
    assert "Total planned calls: 4" in output

    assert runner.main([
        "--split", "dev", "--prompts", "A", "--runs", "2", "--dry-run"
    ]) == 0
    override_output = capsys.readouterr().out
    assert "Runs per prompt: 2" in override_output
    assert "Total planned calls: 2" in override_output


def test_exclude_key_skips_only_that_pending_observation(
    tmp_path, monkeypatch, capsys
):
    results_path = tmp_path / "results.json"
    retry_path = tmp_path / "retry_log.jsonl"
    excluded_key = "dev|DEV-01|A|1"
    existing_key = "dev|DEV-03|A|1"
    existing_result = {
        "split": "dev",
        "record_id": "DEV-03",
        "prompt_id": "A",
        "run": 1,
        "raw_output": None,
        "parsed_json": None,
        "parse_ok": False,
        "schema_ok": False,
        "error": "Previously stored result",
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "latency_seconds": 0.1,
        "timestamp_utc": "2026-11-12T00:00:00+00:00",
    }
    initial_store = ResultsStore(results_path, _meta())
    initial_store.add_record(existing_result)
    initial_store.save()
    _setup_cli(
        monkeypatch,
        results_path,
        records=[
            {"split": "dev", "record_id": "DEV-01", "message": "Excluded claim."},
            {"split": "dev", "record_id": "DEV-02", "message": "Eligible claim one."},
            {"split": "dev", "record_id": "DEV-03", "message": "Stored claim."},
            {"split": "dev", "record_id": "DEV-04", "message": "Eligible claim two."},
        ],
    )
    monkeypatch.setattr(runner, "RETRY_LOG_PATH", retry_path)
    monkeypatch.setattr(config, "DAILY_TOKEN_LIMIT", None)
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _response()

    monkeypatch.setattr(runner, "create_groq_client", lambda **_: Client())

    assert runner.main([
        "--split", "dev",
        "--prompts", "A",
        "--runs", "1",
        "--exclude-key", excluded_key,
        "--no-daily-limit",
        "--yes",
    ]) == 0

    assert [call["messages"][0]["content"] for call in calls] == [
        "Prompt: Eligible claim one.",
        "Prompt: Eligible claim two.",
    ]
    output = capsys.readouterr().out
    assert f"Excluded observation key: {excluded_key}" in output
    final_store = ResultsStore.load(results_path)
    assert not final_store.has_key(excluded_key)
    assert final_store.has_key("dev|DEV-02|A|1")
    assert final_store.has_key("dev|DEV-04|A|1")
    assert final_store.records[existing_key] == existing_result


def test_live_run_refuses_unset_daily_limit_without_override(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(config, "DAILY_TOKEN_LIMIT", None)
    _setup_cli(monkeypatch, tmp_path / "raw_responses.json")
    client_created = []
    monkeypatch.setattr(
        runner,
        "create_groq_client",
        lambda **_: client_created.append(True),
    )

    assert runner.main(["--split", "dev", "--prompts", "A", "--yes"]) == 1
    assert client_created == []
    assert "DAILY_TOKEN_LIMIT is not configured" in capsys.readouterr().err


def test_no_daily_limit_allows_fake_client_run(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DAILY_TOKEN_LIMIT", None)
    _setup_cli(monkeypatch, tmp_path / "raw_responses.json")

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _response()

    monkeypatch.setattr(runner, "create_groq_client", lambda **_: Client())
    monkeypatch.setattr(
        runner,
        "RETRY_LOG_PATH",
        tmp_path / "retry_log.jsonl",
    )

    assert runner.main([
        "--split", "dev", "--prompts", "A", "--no-daily-limit", "--yes"
    ]) == 0
    saved = ResultsStore.load(tmp_path / "raw_responses.json")
    assert saved.has_key("dev|DEV-01|A|1")


def test_pre_call_estimate_stops_before_exceeding_limit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "DAILY_TOKEN_LIMIT", 15)
    monkeypatch.setattr(config, "MAX_TOKENS", 10)
    store = ResultsStore(tmp_path / "results.json", {"model": "test"})
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _response()

    messages = []
    assert run_pending(
        [_item()],
        Client(),
        store,
        output=messages.append,
        retry_log_path=tmp_path / "retry_log.jsonl",
    ) == 0
    assert calls == []
    assert any("today (UTC," in message for message in messages)
    assert "dev|DEV-01|A|1" not in store.records


def test_missing_stored_usage_is_counted_conservatively(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DAILY_TOKEN_LIMIT", 30)
    monkeypatch.setattr(config, "MAX_TOKENS", 10)
    store = ResultsStore(tmp_path / "results.json", {"model": "test"})
    missing_usage = {
        "split": "dev",
        "record_id": "DEV-02",
        "prompt_id": "A",
        "run": 1,
        "raw_output": None,
        "parsed_json": None,
        "parse_ok": False,
        "schema_ok": False,
        "error": "HTTP 500",
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "latency_seconds": 0.1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    store.add_record(missing_usage)
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _response()

    messages = []
    prompt_map = {
        "dev|DEV-02|A|1": "x" * 40,
        "dev|DEV-01|A|1": "Rendered test prompt.",
    }
    assert run_pending(
        [_item()],
        Client(),
        store,
        output=messages.append,
        retry_log_path=tmp_path / "retry_log.jsonl",
        prompt_text_by_key=prompt_map,
    ) == 0
    assert calls == []
    assert any("counted conservatively" in message for message in messages)


def test_retry_budget_count_survives_simulated_restart(tmp_path):
    retry_path = tmp_path / "retry_log.jsonl"
    results_path = tmp_path / "results.json"
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]

    def sleep(seconds):
        now[0] += timedelta(seconds=seconds)

    class AlwaysRateLimited:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise _status_error(429)

    for expected_attempt in (1, 2):
        store = ResultsStore.load(results_path, {"model": "test"})
        assert run_pending(
            [_item()],
            AlwaysRateLimited(),
            store,
            max_calls=1,
            output=lambda _: None,
            sleep=sleep,
            retry_log_path=retry_path,
            utc_now=lambda: now[0],
        ) == 0
        events = [
            json.loads(line)
            for line in retry_path.read_text(encoding="utf-8").splitlines()
        ]
        assert events[-1]["key"] == "dev|DEV-01|A|1"
        assert events[-1]["attempt"] == expected_attempt
        assert events[-1]["wait_seconds"] == (
            5 if expected_attempt == 1 else 10
        )
        assert not store.records


def test_load_retry_state_accepts_legacy_retry_log_schema(tmp_path):
    retry_path = tmp_path / "retry_log.jsonl"
    retry_path.write_text(json.dumps({
        "key": "dev|DEV-01|A|1",
        "attempt": 1,
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        "wait_seconds": 5,
    }) + "\n", encoding="utf-8")

    state = load_retry_state(retry_path)

    assert state["dev|DEV-01|A|1"].count == 1
    assert state["dev|DEV-01|A|1"].deadline == datetime(
        2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc
    )


def test_retry_deadline_is_enforced_after_process_restart(tmp_path):
    retry_path = tmp_path / "retry_log.jsonl"
    first_attempt_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = first_attempt_at + timedelta(seconds=60)
    retry_path.write_text(json.dumps({
        "key": "dev|DEV-01|A|1",
        "attempt": 1,
        "timestamp_utc": first_attempt_at.isoformat(),
        "wait_seconds": 60,
    }) + "\n", encoding="utf-8")

    retry_state = load_retry_state(retry_path)
    now = [first_attempt_at + timedelta(seconds=10)]
    sleeps = []
    calls = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += timedelta(seconds=seconds)

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    assert now[0] >= deadline
                    calls.append(kwargs)
                    return _response()

    assert run_pending(
        [_item()],
        Client(),
        ResultsStore(tmp_path / "results.json", {"model": "test"}),
        max_calls=1,
        output=lambda _: None,
        sleep=sleep,
        retry_log_path=retry_path,
        retry_state=retry_state,
        utc_now=lambda: now[0],
    ) == 0

    assert sleeps == [50]
    assert len(calls) == 1


def test_retry_state_restores_latest_deadline_for_observation(tmp_path):
    retry_path = tmp_path / "retry_log.jsonl"
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    latest = first + timedelta(seconds=20)
    events = [
        {
            "key": "dev|DEV-01|A|1",
            "attempt": 1,
            "timestamp_utc": first.isoformat(),
            "wait_seconds": 5,
        },
        {
            "key": "dev|DEV-01|A|1",
            "attempt": 2,
            "timestamp_utc": latest.isoformat(),
            "wait_seconds": 60,
        },
    ]
    retry_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    progress = load_retry_state(retry_path)["dev|DEV-01|A|1"]

    assert progress.count == 2
    assert progress.deadline == latest + timedelta(seconds=60)


def test_malformed_retry_timestamp_stops_before_request(tmp_path):
    retry_path = tmp_path / "retry_log.jsonl"
    retry_path.write_text(json.dumps({
        "key": "dev|DEV-01|A|1",
        "attempt": 1,
        "timestamp_utc": "not-a-timestamp",
        "wait_seconds": 60,
    }) + "\n", encoding="utf-8")
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _response()

    with pytest.raises(ValueError, match="invalid timestamp"):
        run_pending(
            [_item()],
            Client(),
            ResultsStore(tmp_path / "results.json", {"model": "test"}),
            retry_log_path=retry_path,
        )
    assert calls == []


def test_missing_retry_wait_metadata_stops_before_request(tmp_path):
    retry_path = tmp_path / "retry_log.jsonl"
    retry_path.write_text(json.dumps({
        "key": "dev|DEV-01|A|1",
        "attempt": 1,
        "timestamp_utc": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
    }) + "\n", encoding="utf-8")
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _response()

    with pytest.raises(ValueError, match="invalid schema"):
        run_pending(
            [_item()],
            Client(),
            ResultsStore(tmp_path / "results.json", {"model": "test"}),
            retry_log_path=retry_path,
        )
    assert calls == []


def test_unknown_retry_wait_fails_closed_before_request(tmp_path):
    retry_path = tmp_path / "retry_log.jsonl"
    retry_path.write_text(json.dumps({
        "key": "dev|DEV-01|A|1",
        "attempt": 1,
        "timestamp_utc": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        "wait_seconds": None,
    }) + "\n", encoding="utf-8")
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _response()

    with pytest.raises(ValueError, match="no usable retry deadline"):
        run_pending(
            [_item()],
            Client(),
            ResultsStore(tmp_path / "results.json", {"model": "test"}),
            retry_log_path=retry_path,
        )
    assert calls == []


def test_malformed_retry_history_stops_before_request(tmp_path):
    retry_path = tmp_path / "retry_log.jsonl"
    retry_path.write_text('{"key": "partial"}\n', encoding="utf-8")
    calls = []

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return _response()

    with pytest.raises(ValueError, match="invalid schema"):
        run_pending(
            [_item()],
            Client(),
            ResultsStore(tmp_path / "results.json", {"model": "test"}),
            retry_log_path=retry_path,
        )
    assert calls == []


def test_unparseable_retry_after_stops_but_absent_header_uses_schedule(
    tmp_path
):
    store = ResultsStore(tmp_path / "results.json", {"model": "test"})
    retry_path = tmp_path / "retry_log.jsonl"
    calls = []

    class Unparseable:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    raise _status_error(429, {
                        "Retry-After": "not-a-date",
                        "X-RateLimit-Remaining-Requests": "Bearer synthetic-marker",
                        "X-RateLimit-Reset-Requests": "Bearer synthetic-marker",
                        "X-Request-Id": "sk-syntheticmarker0000",
                    })

    messages = []
    assert run_pending(
        [_item()],
        Unparseable(),
        store,
        output=messages.append,
        sleep=lambda _: None,
        retry_log_path=retry_path,
    ) == 1
    assert len(calls) == 1
    assert store.records["dev|DEV-01|A|1"]["error"] == (
        "HTTP 429; unparseable Retry-After header"
    )
    event = json.loads(retry_path.read_text(encoding="utf-8"))
    assert event["wait_seconds"] is None
    assert event["http_status"] == 429
    assert event["error_class"] == "rate_limited"
    assert event["retry_after_raw"] == "not-a-date"
    assert event["retry_after_seconds"] is None
    assert event["wait_source"] == "header"
    assert event["rate_limit_headers"] == {}
    assert event["request_id"] is None
    assert "synthetic-marker" not in json.dumps(event)
    assert "syntheticmarker" not in json.dumps(event)
    assert any("stopped safely" in message for message in messages)

    absent_store = ResultsStore(tmp_path / "absent-results.json", {"model": "test"})
    absent_retry_path = tmp_path / "absent-retry.jsonl"

    class AbsentHeader:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise _status_error(429)

    assert run_pending(
        [_item()],
        AbsentHeader(),
        absent_store,
        max_calls=1,
        output=lambda _: None,
        sleep=lambda _: None,
        retry_log_path=absent_retry_path,
    ) == 0
    absent_event = json.loads(absent_retry_path.read_text(encoding="utf-8"))
    assert absent_event["wait_seconds"] == 5
    assert absent_event["retry_after_raw"] is None
    assert absent_event["retry_after_seconds"] is None
    assert absent_event["wait_source"] == "fallback"


def test_refresh_meta_refuses_when_results_exist(tmp_path, monkeypatch, capsys):
    path = tmp_path / "raw_responses.json"
    store = ResultsStore(path, _meta("old"))
    store.add_record({
        "split": "dev",
        "record_id": "DEV-01",
        "prompt_id": "A",
        "run": 1,
        "raw_output": None,
        "parsed_json": None,
        "parse_ok": False,
        "schema_ok": False,
        "error": "HTTP 500",
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "latency_seconds": 0.1,
        "timestamp_utc": "2026-11-13T00:00:00+00:00",
    })
    store.save()
    _setup_cli(monkeypatch, path)

    assert runner.main([
        "--split", "dev", "--prompts", "A", "--refresh-meta",
        "--dry-run", "--yes",
    ]) == 1
    assert "only when the results store has zero records" in capsys.readouterr().err


def test_refresh_meta_updates_empty_store_and_dry_run_does_not_write(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "raw_responses.json"
    original = ResultsStore(path, _meta("old"))
    original.save()
    original_bytes = path.read_bytes()
    _setup_cli(monkeypatch, path, records=[])

    assert runner.main([
        "--split", "dev", "--prompts", "A", "--refresh-meta",
        "--dry-run", "--yes",
    ]) == 0
    assert path.read_bytes() == original_bytes
    preview = capsys.readouterr().out
    assert "A prompt_sha256: old -> new" in preview
    assert "not written" in preview

    assert runner.main([
        "--split", "dev", "--prompts", "A", "--refresh-meta", "--yes"
    ]) == 0
    refreshed = ResultsStore.load(path)
    assert refreshed.meta["prompt_sha256"] == {
        prompt_id: "new" for prompt_id in runner.PROMPT_FILES
    }
    assert not refreshed.records
    assert "Empty results metadata refreshed." in capsys.readouterr().out
