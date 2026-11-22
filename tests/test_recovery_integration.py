import copy
import json

import pytest

from src.recovery_integration import (
    ProviderResponse,
    UnsuccessfulProviderResponse,
    recover_observation,
)
from src.recovery_ledger import RecoveryLedger, resolve_results
from src.results_store import ResultsStore

KEY = "dev|DEV-01|A|1"
ACTOR = "synthetic-operator"
REFERENCE = "approval-001"
RID_1 = "00000000-0000-0000-0000-000000000001"
RID_2 = "00000000-0000-0000-0000-000000000002"
VALID_OUTPUT = json.dumps({
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


def _original_store(tmp_path):
    store = ResultsStore(tmp_path / "original.json")
    store.add_record({
        "split": "dev",
        "record_id": "DEV-01",
        "prompt_id": "A",
        "run": 1,
        "raw_output": None,
        "parsed_json": None,
        "parse_ok": False,
        "schema_ok": False,
        "error": "synthetic original failure",
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "latency_seconds": None,
        "timestamp_utc": "2026-11-14T00:00:00+00:00",
    })
    return store


def _recover(ledger, store, provider, **overrides):
    arguments = {
        "ledger": ledger,
        "original_store": store,
        "observation_key": KEY,
        "approved_observation_keys": {KEY},
        "request_payload": "synthetic prompt",
        "provider": provider,
        "approval_verifier": lambda key, actor, reference: True,
        "actor": ACTOR,
        "approval_reference": REFERENCE,
        "recovery_id": RID_1,
    }
    arguments.update(overrides)
    return recover_observation(**arguments)


def test_approved_recovery_persists_start_before_provider_and_preserves_original(
    tmp_path,
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    store = _original_store(tmp_path)
    original_before = copy.deepcopy(store.records)
    call_order = []

    def verify(key, actor, reference):
        assert ledger.load_events() == []
        call_order.append("approval")
        return True

    def provider(request):
        events = ledger.load_events()
        assert [event["event_type"] for event in events] == ["recovery_started"]
        assert request.observation_key == KEY
        assert request.recovery_id == RID_1
        assert request.payload == "synthetic prompt"
        call_order.append("provider")
        return ProviderResponse(VALID_OUTPUT, 12, 4, 2, 0.25)

    outcome = _recover(
        ledger,
        store,
        provider,
        approval_verifier=verify,
    )

    assert call_order == ["approval", "provider"]
    assert outcome.outcome == "successful_completion"
    assert outcome.event["event_type"] == "recovery_completed"
    assert store.records == original_before
    events = ledger.load_events()
    assert [event["event_type"] for event in events] == [
        "recovery_started",
        "recovery_completed",
    ]


def test_unapproved_observation_never_starts_or_calls_provider(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    provider_calls = []

    with pytest.raises(PermissionError, match="not explicitly approved"):
        _recover(
            ledger,
            _original_store(tmp_path),
            lambda request: provider_calls.append(request),
            approved_observation_keys=set(),
        )

    assert provider_calls == []
    assert ledger.load_events() == []


def test_missing_or_invalid_approval_provenance_cannot_start_recovery(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    provider_calls = []

    with pytest.raises(ValueError, match="reference"):
        _recover(
            ledger,
            _original_store(tmp_path),
            lambda request: provider_calls.append(request),
            approval_reference="",
        )
    with pytest.raises(ValueError, match="verification workflow"):
        _recover(
            ledger,
            _original_store(tmp_path),
            lambda request: provider_calls.append(request),
            approval_verifier=None,
        )

    assert provider_calls == []
    assert ledger.load_events() == []


def test_rejected_approval_does_not_start_or_call_provider(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    provider_calls = []

    with pytest.raises(PermissionError, match="did not verify"):
        _recover(
            ledger,
            _original_store(tmp_path),
            lambda request: provider_calls.append(request),
            approval_verifier=lambda key, actor, reference: False,
        )

    assert provider_calls == []
    assert ledger.load_events() == []


def test_approval_callback_exception_leaves_ledger_bytes_unchanged(tmp_path):
    ledger_path = tmp_path / "recovery.jsonl"
    ledger_path.write_bytes(b"")
    ledger = RecoveryLedger(ledger_path)
    original_bytes = ledger_path.read_bytes()
    provider_calls = []

    def failed_verifier(key, actor, reference):
        raise RuntimeError("approval service unavailable")

    with pytest.raises(RuntimeError, match="approval service unavailable"):
        _recover(
            ledger,
            _original_store(tmp_path),
            lambda request: provider_calls.append(request),
            approval_verifier=failed_verifier,
        )

    assert ledger.load_events() == []
    assert provider_calls == []
    assert ledger_path.read_bytes() == original_bytes


def test_received_unsuccessful_response_records_failure_not_completion(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")

    def provider(request):
        raise UnsuccessfulProviderResponse("do not persist this diagnostic")

    outcome = _recover(ledger, _original_store(tmp_path), provider)
    events = ledger.load_events()

    assert outcome.outcome == "response_observed_unsuccessful"
    assert [event["event_type"] for event in events] == [
        "recovery_started",
        "recovery_response_failed",
    ]
    assert set(events[1]) == {
        "event_type",
        "event_version",
        "recovery_id",
        "observation_key",
        "timestamp_utc",
        "provenance",
    }
    assert "do not persist" not in json.dumps(events)


def test_received_unusable_model_output_is_not_completed(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")

    outcome = _recover(
        ledger,
        _original_store(tmp_path),
        lambda request: ProviderResponse("{}"),
    )

    assert outcome.outcome == "response_observed_unsuccessful"
    assert [event["event_type"] for event in ledger.load_events()] == [
        "recovery_started",
        "recovery_response_failed",
    ]


@pytest.mark.parametrize(
    "provider_error",
    [TimeoutError, ConnectionError, RuntimeError],
)
def test_ambiguous_exception_stays_uncertain_and_is_not_retried(
    tmp_path,
    provider_error,
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    store = _original_store(tmp_path)
    provider_calls = []

    def provider(request):
        provider_calls.append(request)
        raise provider_error("synthetic ambiguous outcome")

    with pytest.raises(provider_error, match="ambiguous"):
        _recover(ledger, store, provider)

    with pytest.raises(ValueError, match="blocks another recovery"):
        _recover(ledger, store, provider, recovery_id=RID_2)

    events = ledger.load_events()
    resolution = resolve_results(store.records, events)[KEY]
    assert len(provider_calls) == 1
    assert [event["event_type"] for event in events] == ["recovery_started"]
    assert resolution.provider_outcome_uncertainty == (RID_1,)
    assert resolution.recovery_attempt_diagnostics[0].outcome == "unresolved_start"


def test_successful_original_is_not_eligible_for_recovery(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    store = _original_store(tmp_path)
    record = store.records[KEY]
    record.update({
        "raw_output": VALID_OUTPUT,
        "parsed_json": json.loads(VALID_OUTPUT),
        "parse_ok": True,
        "schema_ok": True,
        "error": None,
    })
    provider_calls = []
    verification_calls = []

    with pytest.raises(ValueError, match="successful original"):
        _recover(
            ledger,
            store,
            lambda request: provider_calls.append(request),
            approval_verifier=lambda *args: verification_calls.append(args) or True,
        )

    assert provider_calls == []
    assert verification_calls == []
    assert ledger.load_events() == []
