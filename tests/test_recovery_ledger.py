import json
import multiprocessing
import os

import pytest

import src.recovery_ledger as recovery_module
from src.recovery_ledger import (
    RecoveryLedger,
    build_scoreable_results,
    resolve_results,
    validate_recovery_events,
)
from src.results_store import RECORD_FIELDS

KEY = "dev|DEV-01|A|1"
OTHER_KEY = "dev|DEV-02|A|1"
ACTOR = "synthetic-test"
REFERENCE = "fixture-001"
RID_1 = "00000000-0000-0000-0000-000000000001"
RID_2 = "00000000-0000-0000-0000-000000000002"
SID_1 = "10000000-0000-0000-0000-000000000001"


def _result(
    *,
    record_id="DEV-01",
    parse_ok=False,
    schema_ok=False,
    parsed_json=None,
    error="APIConnectionError",
    timestamp="2026-11-14T00:00:00+00:00",
):
    record = {
        "split": "dev",
        "record_id": record_id,
        "prompt_id": "A",
        "run": 1,
        "raw_output": None,
        "parsed_json": parsed_json,
        "parse_ok": parse_ok,
        "schema_ok": schema_ok,
        "error": error,
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "latency_seconds": None,
        "timestamp_utc": timestamp,
    }
    assert set(record) == RECORD_FIELDS
    return record


def _success_result(**overrides):
    values = {
        "parse_ok": True,
        "schema_ok": True,
        "parsed_json": {"claim_type": "own_damage"},
        "error": None,
        "timestamp": "2026-11-14T00:00:02+00:00",
    }
    values.update(overrides)
    return _result(**values)


def _start(
    ledger,
    key=KEY,
    recovery_id=RID_1,
    timestamp="2026-11-14T00:00:01+00:00",
):
    return ledger.start_recovery(
        key,
        actor=ACTOR,
        reference=REFERENCE,
        recovery_id=recovery_id,
        timestamp_utc=timestamp,
    )


def _complete(
    ledger,
    recovery_id,
    result=None,
    timestamp="2026-11-14T00:00:02+00:00",
):
    return ledger.complete_recovery(
        recovery_id,
        _success_result() if result is None else result,
        actor=ACTOR,
        reference=REFERENCE,
        timestamp_utc=timestamp,
    )


def _fail_response(
    ledger,
    recovery_id,
    timestamp="2026-11-14T00:00:02+00:00",
):
    return ledger.record_unsuccessful_response(
        recovery_id,
        actor=ACTOR,
        reference=REFERENCE,
        timestamp_utc=timestamp,
    )


def _adjudicate(ledger, recovery_id, timestamp="2026-11-14T00:00:02+00:00"):
    return ledger.adjudicate_recovery(
        recovery_id,
        actor=ACTOR,
        reference="review-001",
        timestamp_utc=timestamp,
    )


def _select(
    ledger,
    recovery_id=RID_1,
    key=KEY,
    selection_id=SID_1,
    timestamp="2026-11-14T00:00:03+00:00",
):
    return ledger.select_recovery_for_evaluation(
        key,
        recovery_id,
        actor=ACTOR,
        reference="evaluation-review-001",
        selection_id=selection_id,
        timestamp_utc=timestamp,
    )


def _race_start_worker(path, barrier, output, recovery_id):
    try:
        barrier.wait(timeout=15)
        _start(RecoveryLedger(path), recovery_id=recovery_id)
    except ValueError:
        output.put(("rejected", recovery_id))
    except Exception as error:
        output.put((f"unexpected:{type(error).__name__}", recovery_id))
    else:
        output.put(("started", recovery_id))


def _hold_exclusive_lock(path, acquired, release):
    ledger = RecoveryLedger(path)
    with ledger._locked(exclusive=True):
        acquired.set()
        if not release.wait(15):
            raise RuntimeError("Lock test release timed out.")


def _public_mutation_lock_worker(path, output):
    real_flock = recovery_module.fcntl.flock
    real_read = RecoveryLedger._read_events_unlocked

    def observe_flock(fd, operation):
        if operation == recovery_module.fcntl.LOCK_EX:
            try:
                real_flock(
                    fd,
                    recovery_module.fcntl.LOCK_EX
                    | recovery_module.fcntl.LOCK_NB,
                )
            except BlockingIOError:
                output.put("blocked_on_exclusive_lock")
            else:
                real_flock(fd, recovery_module.fcntl.LOCK_UN)
                output.put("exclusive_lock_was_not_blocked")
        result = real_flock(fd, operation)
        if operation == recovery_module.fcntl.LOCK_EX:
            output.put("exclusive_lock_acquired")
        return result

    def observe_transaction_read(ledger):
        output.put("transaction_read_entered")
        return real_read(ledger)

    recovery_module.fcntl.flock = observe_flock
    RecoveryLedger._read_events_unlocked = observe_transaction_read
    output.put("about_to_invoke")
    try:
        _start(RecoveryLedger(path), recovery_id=RID_1)
    except Exception as error:
        output.put(("mutation_failed", type(error).__name__))
    else:
        output.put("mutation_completed")


def test_started_completed_and_explicit_selection_survive_reload(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    _complete(ledger, RID_1)
    _select(ledger)

    events = RecoveryLedger(ledger.path).load_events()
    resolution = resolve_results({KEY: _result()}, events)[KEY]

    assert [event["event_type"] for event in events] == [
        "recovery_started",
        "recovery_completed",
        "recovery_selected_for_evaluation",
    ]
    assert resolution.evaluation_selection_status == "recovery_selected"
    assert resolution.scoreable_result["record_id"] == "DEV-01"


def test_observed_unsuccessful_response_persists_and_reloads_exact_schema(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)

    event = _fail_response(ledger, RID_1)
    reloaded = RecoveryLedger(ledger.path).load_events()
    resolution = resolve_results({KEY: _result()}, reloaded)[KEY]

    assert set(event) == {
        "event_type",
        "event_version",
        "recovery_id",
        "observation_key",
        "timestamp_utc",
        "provenance",
    }
    assert event["event_type"] == "recovery_response_failed"
    assert event["event_version"] == 1
    assert reloaded[-1] == event
    assert resolution.successful_recovery_ids == ()
    assert resolution.provider_outcome_uncertainty == (RID_1,)
    assert resolution.scoreable is False
    assert resolution.recovery_attempt_diagnostics[0].outcome == (
        "response_observed_unsuccessful"
    )
    assert (
        resolution.recovery_attempt_diagnostics[0]
        .provider_processing_billing_uncertain
        is True
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("event_version", 2, "event version is invalid"),
        ("event_version", True, "event version is invalid"),
        ("provenance", {"actor": ACTOR, "reference": ""}, "provenance field"),
        ("unexpected", "extra", "invalid schema"),
    ],
)
def test_malformed_response_failure_event_is_rejected_without_append(
    tmp_path, field, value, message
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    before = ledger.path.read_bytes()
    event = {
        "event_type": "recovery_response_failed",
        "event_version": 1,
        "recovery_id": RID_1,
        "observation_key": KEY,
        "timestamp_utc": "2026-11-14T00:00:02+00:00",
        "provenance": {"actor": ACTOR, "reference": REFERENCE},
    }
    event[field] = value

    with pytest.raises(ValueError, match=message):
        ledger._append_event(event)

    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize(
    ("recovery_id", "message"),
    [
        ("not-a-uuid", "UUID string"),
        ("A0000000-0000-0000-0000-000000000001", "canonical lowercase"),
    ],
)
def test_response_failure_rejects_malformed_recovery_ids_without_appending(
    tmp_path, recovery_id, message
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    before = ledger.path.read_bytes()
    event = {
        "event_type": "recovery_response_failed",
        "event_version": 1,
        "recovery_id": recovery_id,
        "observation_key": KEY,
        "timestamp_utc": "2026-11-14T00:00:02+00:00",
        "provenance": {"actor": ACTOR, "reference": REFERENCE},
    }

    with pytest.raises(ValueError, match=message):
        ledger._append_event(event)

    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize(
    ("recovery_id", "observation_key", "message"),
    [
        (RID_2, KEY, "no corresponding start"),
        (RID_1, OTHER_KEY, "observation key changed"),
    ],
)
def test_response_failure_requires_matching_started_recovery(
    tmp_path, recovery_id, observation_key, message
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    before = ledger.path.read_bytes()
    event = {
        "event_type": "recovery_response_failed",
        "event_version": 1,
        "recovery_id": recovery_id,
        "observation_key": observation_key,
        "timestamp_utc": "2026-11-14T00:00:02+00:00",
        "provenance": {"actor": ACTOR, "reference": REFERENCE},
    }

    with pytest.raises(ValueError, match=message):
        ledger._append_event(event)

    assert ledger.path.read_bytes() == before


def test_response_failure_timestamp_cannot_precede_start(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="precedes its start"):
        _fail_response(ledger, RID_1, timestamp="2026-11-14T00:00:00+00:00")

    assert ledger.path.read_bytes() == before


def test_duplicate_response_failure_is_rejected_without_append(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    _fail_response(ledger, RID_1)
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="recorded more than once"):
        _fail_response(ledger, RID_1)

    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize("prior_event", ["completion", "adjudication"])
def test_response_failure_after_completion_or_adjudication_is_rejected(
    tmp_path, prior_event
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    if prior_event == "completion":
        _complete(ledger, RID_1)
    else:
        _adjudicate(ledger, RID_1)
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="more than one terminal event"):
        _fail_response(ledger, RID_1, timestamp="2026-11-14T00:00:03+00:00")

    assert ledger.path.read_bytes() == before


def test_completion_and_selection_after_response_failure_are_rejected(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    _fail_response(ledger, RID_1)
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="cannot be completed after"):
        _complete(ledger, RID_1, timestamp="2026-11-14T00:00:03+00:00")
    assert ledger.path.read_bytes() == before

    with pytest.raises(ValueError, match="completed recovery"):
        _select(ledger, timestamp="2026-11-14T00:00:03+00:00")
    assert ledger.path.read_bytes() == before


def test_failed_response_requires_adjudication_and_new_recovery_id(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger, recovery_id=RID_1)
    _fail_response(ledger, RID_1)
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="uncertain recovery blocks"):
        _start(
            ledger,
            recovery_id=RID_2,
            timestamp="2026-11-14T00:00:03+00:00",
        )
    assert ledger.path.read_bytes() == before

    _adjudicate(ledger, RID_1, timestamp="2026-11-14T00:00:03+00:00")
    _start(
        ledger,
        recovery_id=RID_2,
        timestamp="2026-11-14T00:00:04+00:00",
    )
    _complete(ledger, RID_2, timestamp="2026-11-14T00:00:05+00:00")
    _select(
        ledger,
        recovery_id=RID_2,
        selection_id=SID_1,
        timestamp="2026-11-14T00:00:06+00:00",
    )

    resolution = resolve_results({KEY: _result()}, ledger.load_events())[KEY]
    attempt_diagnostics = {
        attempt.recovery_id: attempt
        for attempt in resolution.recovery_attempt_diagnostics
    }

    assert resolution.provider_outcome_uncertainty == (RID_1,)
    assert resolution.successful_recovery_ids == (RID_2,)
    assert resolution.evaluation_selection_status == "recovery_selected"
    assert resolution.scoreable is True
    assert attempt_diagnostics[RID_1].outcome == "response_observed_unsuccessful"
    assert attempt_diagnostics[RID_1].adjudicated is True
    assert attempt_diagnostics[RID_1].provider_processing_billing_uncertain is True
    assert attempt_diagnostics[RID_2].outcome == "successful_completion"
    assert attempt_diagnostics[RID_2].provider_processing_billing_uncertain is False


def test_repeated_adjudication_after_failed_response_is_rejected_without_append(
    tmp_path,
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    _fail_response(ledger, RID_1)
    _adjudicate(ledger, RID_1, timestamp="2026-11-14T00:00:03+00:00")
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="more than one terminal event"):
        _adjudicate(ledger, RID_1, timestamp="2026-11-14T00:00:04+00:00")

    assert ledger.path.read_bytes() == before


def test_successful_original_remains_selected_with_failed_recovery(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    original = _success_result()
    _start(ledger)
    _fail_response(ledger, RID_1)

    resolution = resolve_results({KEY: original}, ledger.load_events())[KEY]

    assert resolution.scoreable_result == original
    assert resolution.scoreable_source == "original"
    assert resolution.scoreable is True
    assert resolution.successful_recovery_ids == ()
    assert resolution.provider_outcome_uncertainty == (RID_1,)
    assert resolution.recovery_attempt_diagnostics[0].outcome == (
        "response_observed_unsuccessful"
    )


def test_invalid_event_schema_is_rejected(tmp_path):
    path = tmp_path / "recovery.jsonl"
    event = _start(RecoveryLedger(tmp_path / "unused.jsonl"))
    event["unexpected"] = "value"
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid schema"):
        RecoveryLedger(path).load_events()


@pytest.mark.parametrize(
    "observation_key",
    [
        "dev|DEV-01|A",
        "dev|DEV-01|A|1|extra",
        "dev||A|1",
        "dev|DEV-01|A|0",
        "dev|DEV-01|A|01",
        "dev|DEV-01|A|one",
    ],
)
def test_observation_key_requires_exact_four_part_identity(
    tmp_path, observation_key
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")

    with pytest.raises(ValueError, match="observation key has an invalid format"):
        _start(ledger, key=observation_key)

    assert ledger.load_events() == []


def test_recovery_result_identity_must_match_started_observation(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="identity does not match"):
        _complete(ledger, RID_1, _success_result(record_id="DEV-02"))

    assert ledger.path.read_bytes() == before


def test_duplicate_recovery_ids_are_rejected_without_appending(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    original = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="started more than once"):
        _start(ledger, OTHER_KEY, RID_1)

    assert ledger.path.read_bytes() == original


def test_recovery_referencing_unknown_original_key_is_rejected(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger, OTHER_KEY)
    originals = {KEY: _result()}
    before = json.loads(json.dumps(originals))

    with pytest.raises(ValueError, match="unknown original key"):
        build_scoreable_results(originals, ledger.load_events())

    assert originals == before
    assert tuple(originals) == (KEY,)


def test_original_failure_is_preserved_and_excluded_without_selection(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    original = _result()
    before = json.loads(json.dumps(original))
    _start(ledger)
    _complete(ledger, RID_1)

    scoreable = build_scoreable_results({KEY: original}, ledger.load_events())

    assert original == before
    assert scoreable.records == ()
    assert scoreable.diagnostics[KEY].evaluation_selection_status == (
        "recovery_selection_required"
    )


def test_one_successful_recovery_requires_and_honors_explicit_selection(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    _complete(ledger, RID_1)
    without_decision = resolve_results({KEY: _result()}, ledger.load_events())[KEY]
    assert without_decision.scoreable_result is None

    _select(ledger)
    scoreable = build_scoreable_results({KEY: _result()}, ledger.load_events())

    assert len(scoreable.records) == 1
    assert scoreable.records[0]["parse_ok"] is True
    assert scoreable.diagnostics[KEY].scoreable_source == "recovery"


def test_uncertain_prior_request_and_evaluation_selection_are_independent(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger, recovery_id=RID_1)
    before = ledger.path.read_bytes()
    with pytest.raises(ValueError, match="uncertain recovery blocks"):
        _start(
            ledger,
            recovery_id=RID_2,
            timestamp="2026-11-14T00:00:02+00:00",
        )
    assert ledger.path.read_bytes() == before

    _adjudicate(ledger, RID_1)
    _start(
        ledger,
        recovery_id=RID_2,
        timestamp="2026-11-14T00:00:03+00:00",
    )
    _complete(
        ledger,
        RID_2,
        timestamp="2026-11-14T00:00:04+00:00",
    )

    unresolved = build_scoreable_results({KEY: _result()}, ledger.load_events())
    assert unresolved.records == ()
    assert unresolved.diagnostics[KEY].provider_outcome_uncertainty == (RID_1,)
    assert unresolved.diagnostics[KEY].evaluation_selection_status == (
        "recovery_selection_required"
    )

    _select(
        ledger,
        recovery_id=RID_2,
        selection_id=SID_1,
        timestamp="2026-11-14T00:00:05+00:00",
    )
    selected = build_scoreable_results({KEY: _result()}, ledger.load_events())

    assert len(selected.records) == 1
    assert selected.diagnostics[KEY].scoreable_source == "recovery"
    assert selected.diagnostics[KEY].provider_outcome_uncertainty == (RID_1,)


def test_successful_original_is_preserved_and_selection_cannot_override_it(
    tmp_path,
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    original = _success_result()
    _start(ledger)
    _complete(ledger, RID_1)
    _select(ledger)

    scoreable = build_scoreable_results({KEY: original}, ledger.load_events())

    assert scoreable.records == (original,)
    assert scoreable.diagnostics[KEY].evaluation_selection_status == (
        "original_selected"
    )
    assert scoreable.diagnostics[KEY].scoreable_source == "original"


def test_successful_original_plus_incomplete_recovery_keeps_both_dimensions(
    tmp_path,
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    original = _success_result()
    _start(ledger)

    scoreable = build_scoreable_results({KEY: original}, ledger.load_events())
    resolution = scoreable.diagnostics[KEY]

    assert scoreable.records == (original,)
    assert resolution.provider_outcome_uncertainty == (RID_1,)
    assert resolution.evaluation_selection_status == "original_selected"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parse_ok", False),
        ("schema_ok", False),
        ("parsed_json", None),
        ("error", "APIConnectionError"),
    ],
)
def test_completion_rejects_results_that_fail_success_predicate(
    tmp_path, field, value
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    before = ledger.path.read_bytes()
    result = _success_result()
    result[field] = value

    with pytest.raises(ValueError, match="successful-result predicate"):
        _complete(ledger, RID_1, result)

    assert ledger.path.read_bytes() == before
    resolution = build_scoreable_results({KEY: _result()}, ledger.load_events())
    assert resolution.records == ()
    assert resolution.diagnostics[KEY].provider_outcome_uncertainty == (RID_1,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run", True),
        ("parse_ok", 1),
        ("parsed_json", []),
        ("error", 1),
    ],
)
def test_malformed_completion_result_is_rejected_without_appending(
    tmp_path, field, value
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    before = ledger.path.read_bytes()
    result = _success_result()
    result[field] = value

    with pytest.raises(ValueError):
        _complete(ledger, RID_1, result)

    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize(
    ("action", "actor", "reference"),
    [
        ("start", "", REFERENCE),
        ("start", ACTOR, ""),
        ("start", "actor with spaces", REFERENCE),
        ("adjudicate", "", "review-001"),
        ("adjudicate", ACTOR, ""),
        ("adjudicate", "actor with spaces", "review-001"),
    ],
)
def test_invalid_actor_or_reference_provenance_is_rejected(
    tmp_path, action, actor, reference
):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    if action == "adjudicate":
        _start(ledger)
        before = ledger.path.read_bytes()
        with pytest.raises(ValueError, match="provenance field"):
            ledger.adjudicate_recovery(
                RID_1,
                actor=actor,
                reference=reference,
                timestamp_utc="2026-11-14T00:00:02+00:00",
            )
    else:
        with pytest.raises(ValueError, match="provenance field"):
            ledger.start_recovery(
                KEY,
                actor=actor,
                reference=reference,
                recovery_id=RID_1,
                timestamp_utc="2026-11-14T00:00:01+00:00",
            )

    if action == "adjudicate":
        assert ledger.path.read_bytes() == before
    assert len(ledger.load_events()) == (1 if action == "adjudicate" else 0)


def test_selection_rejects_unknown_incomplete_or_wrong_key_recovery(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    with pytest.raises(ValueError, match="unknown recovery"):
        _select(ledger)

    _start(ledger)
    with pytest.raises(ValueError, match="completed recovery"):
        _select(ledger)

    event = {
        "event_type": "recovery_selected_for_evaluation",
        "selection_id": SID_1,
        "observation_key": OTHER_KEY,
        "recovery_id": RID_1,
        "timestamp_utc": "2026-11-14T00:00:03+00:00",
        "provenance": {"actor": ACTOR, "reference": "evaluation-review-001"},
    }
    with pytest.raises(ValueError, match="observation key changed"):
        ledger._append_event(event)


def test_selection_must_not_predate_completion(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    _complete(ledger, RID_1)
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="precedes recovery completion"):
        _select(ledger, timestamp="2026-11-14T00:00:01+00:00")

    assert ledger.path.read_bytes() == before


def test_duplicate_or_contradictory_selection_decisions_are_rejected(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger, recovery_id=RID_1)
    _complete(ledger, RID_1)
    _start(
        ledger,
        recovery_id=RID_2,
        timestamp="2026-11-14T00:00:03+00:00",
    )
    _complete(
        ledger,
        RID_2,
        timestamp="2026-11-14T00:00:04+00:00",
    )
    _select(ledger, recovery_id=RID_1, timestamp="2026-11-14T00:00:05+00:00")
    before = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="already has an evaluation selection"):
        _select(
            ledger,
            recovery_id=RID_2,
            selection_id="10000000-0000-0000-0000-000000000002",
            timestamp="2026-11-14T00:00:06+00:00",
        )

    assert ledger.path.read_bytes() == before


def test_multiple_successes_conflict_even_with_explicit_selection(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger, recovery_id=RID_1)
    _complete(ledger, RID_1)
    _select(ledger, recovery_id=RID_1)
    _start(
        ledger,
        recovery_id=RID_2,
        timestamp="2026-11-14T00:00:04+00:00",
    )
    _complete(
        ledger,
        RID_2,
        timestamp="2026-11-14T00:00:05+00:00",
    )

    scoreable = build_scoreable_results({KEY: _result()}, ledger.load_events())
    resolution = scoreable.diagnostics[KEY]

    assert scoreable.records == ()
    assert resolution.scoreable_result is None
    assert resolution.evaluation_selection_status == "conflict_requires_review"
    assert resolution.conflicts_requiring_review == (RID_1, RID_2)


def test_completion_without_start_is_rejected(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    with pytest.raises(ValueError, match="without a start"):
        _complete(ledger, RID_1)


def test_duplicate_completion_is_rejected(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    _complete(ledger, RID_1)

    with pytest.raises(ValueError, match="more than one terminal"):
        _complete(ledger, RID_1)


def test_adjudication_after_completion_is_rejected(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    _complete(ledger, RID_1)

    with pytest.raises(ValueError, match="more than one terminal"):
        _adjudicate(ledger, RID_1)


def test_repeated_adjudication_is_rejected(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    _adjudicate(ledger, RID_1)

    with pytest.raises(ValueError, match="more than one terminal"):
        _adjudicate(ledger, RID_1)


def test_invalid_adjudication_decision_is_rejected_without_appending(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    before = ledger.path.read_bytes()
    event = {
        "event_type": "recovery_adjudicated",
        "recovery_id": RID_1,
        "observation_key": KEY,
        "timestamp_utc": "2026-11-14T00:00:02+00:00",
        "provenance": {"actor": ACTOR, "reference": "review-001"},
        "decision": "assume_not_processed",
    }

    with pytest.raises(ValueError, match="adjudication decision is invalid"):
        ledger._append_event(event)

    assert ledger.path.read_bytes() == before


def test_non_monotonic_timestamps_across_recoveries_are_allowed(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger, recovery_id=RID_1, timestamp="2026-11-14T00:00:10+00:00")
    _complete(
        ledger,
        RID_1,
        timestamp="2026-11-14T00:00:12+00:00",
    )
    _start(ledger, recovery_id=RID_2, timestamp="2026-11-14T00:00:01+00:00")
    _complete(
        ledger,
        RID_2,
        timestamp="2026-11-14T00:00:02+00:00",
    )

    assert len(ledger.load_events()) == 4


def test_terminal_event_key_mismatch_is_rejected(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    _start(ledger)
    event = {
        "event_type": "recovery_adjudicated",
        "recovery_id": RID_1,
        "observation_key": OTHER_KEY,
        "timestamp_utc": "2026-11-14T00:00:02+00:00",
        "provenance": {"actor": ACTOR, "reference": "review-001"},
        "decision": "authorize_further_attempt",
    }

    with pytest.raises(ValueError, match="observation key changed"):
        ledger._append_event(event)


def test_malformed_or_incomplete_final_line_is_rejected(tmp_path):
    path = tmp_path / "recovery.jsonl"
    path.write_bytes(b'{"event_type":"recovery_started"}')

    with pytest.raises(ValueError, match="incomplete final"):
        RecoveryLedger(path).load_events()


def test_adjudication_and_restart_preserve_history(tmp_path):
    path = tmp_path / "recovery.jsonl"
    ledger = RecoveryLedger(path)
    _start(ledger)
    original = path.read_bytes()
    _adjudicate(ledger, RID_1)

    resumed = RecoveryLedger(path)
    events = resumed.load_events()

    assert path.read_bytes().startswith(original)
    assert [event["event_type"] for event in events] == [
        "recovery_started",
        "recovery_adjudicated",
    ]


def test_scoreable_results_are_unique_and_deterministically_ordered(tmp_path):
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    original_1 = _success_result()
    original_2 = _result(record_id="DEV-02")
    _start(ledger, OTHER_KEY, RID_2)
    _complete(ledger, RID_2, _success_result(record_id="DEV-02"))
    _select(ledger, RID_2, OTHER_KEY)

    forward = build_scoreable_results(
        {KEY: original_1, OTHER_KEY: original_2},
        ledger.load_events(),
    )
    reverse = build_scoreable_results(
        {OTHER_KEY: original_2, KEY: original_1},
        ledger.load_events(),
    )

    assert len(forward.records) == 2
    assert [record["record_id"] for record in forward.records] == [
        "DEV-01",
        "DEV-02",
    ]
    assert forward.records == reverse.records
    assert list(forward.diagnostics) == [KEY, OTHER_KEY]


def test_load_events_requests_a_shared_os_lock(tmp_path, monkeypatch):
    if recovery_module.fcntl is None:
        pytest.skip("fcntl locks are unavailable on this platform")
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    real_flock = recovery_module.fcntl.flock
    operations = []

    def observe_flock(fd, operation):
        operations.append(operation)
        return real_flock(fd, operation)

    monkeypatch.setattr(recovery_module.fcntl, "flock", observe_flock)
    assert ledger.load_events() == []

    assert operations[0] == recovery_module.fcntl.LOCK_SH
    assert operations[-1] == recovery_module.fcntl.LOCK_UN
    assert ledger.lock_path.exists()


def test_path_spellings_share_canonical_lock_identity(tmp_path):
    directory = tmp_path / "ledger"
    directory.mkdir()
    canonical_spelling = directory / "recovery.jsonl"
    aliased_spelling = directory / "unused" / ".." / "recovery.jsonl"

    canonical = RecoveryLedger(canonical_spelling)
    aliased = RecoveryLedger(aliased_spelling)

    assert canonical.path.is_absolute()
    assert aliased.path == canonical.path
    assert aliased.lock_path == canonical.lock_path


def test_ledger_path_properties_cannot_be_reassigned(tmp_path):
    path = tmp_path / "recovery.jsonl"
    ledger = RecoveryLedger(path)
    original_lock_path = ledger.lock_path

    with pytest.raises(AttributeError):
        ledger.path = tmp_path / "other.jsonl"
    with pytest.raises(AttributeError):
        ledger.lock_path = tmp_path / "other.jsonl.lock"

    assert ledger.path == path.resolve()
    assert ledger.lock_path == original_lock_path
    _start(ledger)
    assert len(RecoveryLedger(path).load_events()) == 1


def test_parent_directory_symlink_shares_lock_identity(tmp_path):
    target_directory = tmp_path / "actual"
    target_directory.mkdir()
    alias_directory = tmp_path / "alias"
    try:
        alias_directory.symlink_to(target_directory, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"Directory symlinks are unavailable: {error}")

    direct = RecoveryLedger(target_directory / "recovery.jsonl")
    through_alias = RecoveryLedger(alias_directory / "recovery.jsonl")

    assert through_alias.path == direct.path
    assert through_alias.lock_path == direct.lock_path


def test_ledger_file_symlink_resolves_to_target_lock_identity(tmp_path):
    target = tmp_path / "target.jsonl"
    target.write_text("", encoding="utf-8")
    alias = tmp_path / "alias.jsonl"
    try:
        alias.symlink_to(target)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"File symlinks are unavailable: {error}")

    direct = RecoveryLedger(target)
    through_alias = RecoveryLedger(alias)
    _start(through_alias)

    assert through_alias.path == direct.path
    assert through_alias.lock_path == direct.lock_path
    assert len(direct.load_events()) == 1


def test_replaced_ledger_path_symlink_is_rejected(tmp_path):
    ledger_path = tmp_path / "recovery.jsonl"
    other_path = tmp_path / "other.jsonl"
    other_path.write_text("", encoding="utf-8")
    ledger = RecoveryLedger(ledger_path)
    try:
        ledger_path.symlink_to(other_path)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"File symlinks are unavailable: {error}")

    with pytest.raises(ValueError, match="regular file with one hard link"):
        ledger.load_events()


def test_hard_linked_ledger_is_rejected(tmp_path):
    target = tmp_path / "target.jsonl"
    target.write_text("", encoding="utf-8")
    alias = tmp_path / "hardlink.jsonl"
    try:
        os.link(target, alias)
    except OSError as error:
        pytest.skip(f"Hard links are unavailable: {error}")

    with pytest.raises(ValueError, match="regular file with one hard link"):
        RecoveryLedger(target)


def test_replacing_existing_ledger_with_regular_file_is_rejected(tmp_path):
    path = tmp_path / "recovery.jsonl"
    path.write_text("", encoding="utf-8")
    ledger = RecoveryLedger(path)
    replacement = tmp_path / "replacement.jsonl"
    replacement_contents = b"replacement must remain untouched\n"
    replacement.write_bytes(replacement_contents)
    os.replace(replacement, path)

    with pytest.raises(ValueError, match="device/inode identity changed"):
        ledger.load_events()
    with pytest.raises(ValueError, match="device/inode identity changed"):
        _start(ledger)

    assert path.read_bytes() == replacement_contents


def test_deleting_and_recreating_ledger_is_rejected(tmp_path):
    path = tmp_path / "recovery.jsonl"
    path.write_text("", encoding="utf-8")
    ledger = RecoveryLedger(path)

    with path.open("rb"):
        path.unlink()
        path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="device/inode identity changed"):
        ledger.load_events()
    with pytest.raises(ValueError, match="device/inode identity changed"):
        _start(ledger)


def test_initially_absent_ledger_is_created_and_used_normally(tmp_path):
    path = tmp_path / "recovery.jsonl"
    ledger = RecoveryLedger(path)

    assert not path.exists()
    _start(ledger)

    assert len(ledger.load_events()) == 1


def test_instances_created_before_ledger_share_first_created_identity(tmp_path):
    path = tmp_path / "recovery.jsonl"
    first = RecoveryLedger(path)
    second = RecoveryLedger(path)

    _start(first, KEY, RID_1)
    _start(second, OTHER_KEY, RID_2)

    events = RecoveryLedger(path).load_events()
    assert [event["recovery_id"] for event in events] == [RID_1, RID_2]
    assert first.path == second.path
    assert first.lock_path == second.lock_path


def test_exclusive_lock_blocks_other_process_lock_acquisition(tmp_path):
    if recovery_module.fcntl is None:
        pytest.skip("fcntl locks are unavailable on this platform")
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_exclusive_lock,
        args=(str(ledger.path), acquired, release),
    )
    process.start()
    try:
        assert acquired.wait(15)
        lock_fd = os.open(ledger.lock_path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                recovery_module.fcntl.flock(
                    lock_fd,
                    recovery_module.fcntl.LOCK_SH | recovery_module.fcntl.LOCK_NB,
                )
        finally:
            os.close(lock_fd)
    finally:
        release.set()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join()
    assert process.exitcode == 0


def test_public_mutation_waits_for_exclusive_transaction_lock(tmp_path):
    if recovery_module.fcntl is None:
        pytest.skip("fcntl locks are unavailable on this platform")
    path = tmp_path / "recovery.jsonl"
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    output = context.Queue()
    holder = context.Process(
        target=_hold_exclusive_lock,
        args=(str(path), acquired, release),
    )
    mutator = context.Process(
        target=_public_mutation_lock_worker,
        args=(str(path), output),
    )
    holder.start()
    try:
        assert acquired.wait(15)
        mutator.start()
        assert output.get(timeout=15) == "about_to_invoke"
        assert output.get(timeout=15) == "blocked_on_exclusive_lock"

        release.set()
        assert output.get(timeout=15) == "exclusive_lock_acquired"
        assert output.get(timeout=15) == "transaction_read_entered"
        assert output.get(timeout=15) == "mutation_completed"
    finally:
        release.set()
        for process in (holder, mutator):
            if process.pid is not None:
                process.join(15)
                if process.is_alive():
                    process.terminate()
                    process.join()

    assert holder.exitcode == 0
    assert mutator.exitcode == 0
    events = RecoveryLedger(path).load_events()
    validate_recovery_events(events)
    assert [event["event_type"] for event in events] == ["recovery_started"]
    assert events[0]["recovery_id"] == RID_1


def test_two_processes_cannot_start_same_uncertain_observation(tmp_path):
    if recovery_module.fcntl is None:
        pytest.skip("fcntl locks are unavailable on this platform")
    path = tmp_path / "recovery.jsonl"
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    output = context.Queue()
    processes = [
        context.Process(
            target=_race_start_worker,
            args=(str(path), barrier, output, recovery_id),
        )
        for recovery_id in (RID_1, RID_2)
    ]
    for process in processes:
        process.start()
    try:
        barrier.wait(timeout=15)
        outcomes = [output.get(timeout=15) for _ in processes]
    finally:
        for process in processes:
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join()

    assert all(process.exitcode == 0 for process in processes)
    assert sorted(result for result, _ in outcomes) == ["rejected", "started"]
    events = RecoveryLedger(path).load_events()
    validate_recovery_events(events)
    assert len(events) == 1
    assert events[0]["event_type"] == "recovery_started"
    assert RecoveryLedger(path).lock_path.exists()
