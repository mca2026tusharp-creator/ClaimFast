import pytest

from src.recovery_evaluation import build_recovery_diagnostic_snapshot
from src.recovery_ledger import RecoveryLedger
from src.results_store import ResultsStore

KEY = "dev|DEV-01|A|1"
ACTOR = "synthetic-reviewer"
REFERENCE = "evaluation-reference"
RID = "00000000-0000-0000-0000-000000000001"
RID_2 = "00000000-0000-0000-0000-000000000002"


def _result(parsed_json, error=None):
    return {
        "split": "dev",
        "record_id": "DEV-01",
        "prompt_id": "A",
        "run": 1,
        "raw_output": None,
        "parsed_json": parsed_json,
        "parse_ok": parsed_json is not None,
        "schema_ok": parsed_json is not None,
        "error": error,
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "latency_seconds": None,
        "timestamp_utc": "2026-11-14T00:00:00+00:00",
    }


def test_different_recovery_is_flagged_while_successful_original_stays_scoreable(
    tmp_path,
):
    store = ResultsStore(tmp_path / "original.json")
    store.add_record(_result({"claim_type": "theft"}))
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    ledger.start_recovery(
        KEY,
        actor=ACTOR,
        reference=REFERENCE,
        recovery_id=RID,
    )
    ledger.complete_recovery(
        RID,
        _result({"claim_type": "own_damage"}),
        actor=ACTOR,
        reference=REFERENCE,
    )

    snapshot = build_recovery_diagnostic_snapshot(
        store.records,
        ledger.load_events(),
    )

    assert snapshot.scoreable_results.records == (_result({"claim_type": "theft"}),)
    assert snapshot.original_recovery_discrepancies == {KEY: (RID,)}
    assert snapshot.selection_events == {}
    assert snapshot.scoreable_results.diagnostics[KEY].scoreable_source == "original"


def test_selection_provenance_and_resolver_diagnostics_are_preserved(tmp_path):
    store = ResultsStore(tmp_path / "original.json")
    store.add_record(_result(None, error="synthetic original failure"))
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    ledger.start_recovery(
        KEY,
        actor=ACTOR,
        reference=REFERENCE,
        recovery_id=RID,
    )
    ledger.complete_recovery(
        RID,
        _result({"claim_type": "own_damage"}),
        actor=ACTOR,
        reference=REFERENCE,
    )
    selection = ledger.select_recovery_for_evaluation(
        KEY,
        RID,
        actor=ACTOR,
        reference="selection-evidence",
    )

    snapshot = build_recovery_diagnostic_snapshot(
        store.records,
        ledger.load_events(),
    )

    assert snapshot.scoreable_results.records == (_result({"claim_type": "own_damage"}),)
    assert snapshot.selection_events[KEY] == selection
    diagnostic = snapshot.scoreable_results.diagnostics[KEY]
    assert diagnostic.evaluation_selection_id == selection["selection_id"]
    assert diagnostic.scoreable_source == "recovery"
    assert diagnostic.conflicts_requiring_review == ()


def test_unselected_recovery_is_excluded_from_diagnostic_snapshot(tmp_path):
    store = ResultsStore(tmp_path / "original.json")
    store.add_record(_result(None, error="synthetic original failure"))
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    ledger.start_recovery(KEY, actor=ACTOR, reference=REFERENCE, recovery_id=RID)
    ledger.complete_recovery(
        RID,
        _result({"claim_type": "own_damage"}),
        actor=ACTOR,
        reference=REFERENCE,
    )

    snapshot = build_recovery_diagnostic_snapshot(store.records, ledger.load_events())

    assert snapshot.scoreable_results.records == ()
    diagnostic = snapshot.scoreable_results.diagnostics[KEY]
    assert diagnostic.evaluation_selection_status == "recovery_selection_required"
    assert diagnostic.successful_recovery_ids == (RID,)
    assert snapshot.selection_events == {}


def test_conflicted_recoveries_stay_excluded_and_keep_selection_provenance(tmp_path):
    store = ResultsStore(tmp_path / "original.json")
    store.add_record(_result(None, error="synthetic original failure"))
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")
    for recovery_id, claim_type in (
        (RID, "own_damage"),
        (RID_2, "theft"),
    ):
        ledger.start_recovery(
            KEY,
            actor=ACTOR,
            reference=REFERENCE,
            recovery_id=recovery_id,
        )
        ledger.complete_recovery(
            recovery_id,
            _result({"claim_type": claim_type}),
            actor=ACTOR,
            reference=REFERENCE,
        )
    selection = ledger.select_recovery_for_evaluation(
        KEY,
        RID,
        actor=ACTOR,
        reference="selection-evidence",
    )

    snapshot = build_recovery_diagnostic_snapshot(store.records, ledger.load_events())

    assert snapshot.scoreable_results.records == ()
    diagnostic = snapshot.scoreable_results.diagnostics[KEY]
    assert diagnostic.evaluation_selection_status == "conflict_requires_review"
    assert diagnostic.conflicts_requiring_review == (RID, RID_2)
    assert snapshot.selection_events[KEY] == selection
    assert snapshot.selection_events[KEY]["provenance"]["reference"] == (
        "selection-evidence"
    )


def test_snapshot_cannot_return_scoreable_records_without_scoreable_builder(
    tmp_path,
    monkeypatch,
):
    store = ResultsStore(tmp_path / "original.json")
    store.add_record(_result(None, error="synthetic original failure"))
    ledger = RecoveryLedger(tmp_path / "recovery.jsonl")

    def reject_builder(original_records, events):
        raise RuntimeError("scoreable-results boundary was called")

    monkeypatch.setattr(
        "src.recovery_evaluation.build_scoreable_results",
        reject_builder,
    )

    with pytest.raises(RuntimeError, match="boundary was called"):
        build_recovery_diagnostic_snapshot(store.records, ledger.load_events())
