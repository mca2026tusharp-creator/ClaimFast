"""Build recovery-resolution diagnostics without producing evaluation metrics."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.recovery_ledger import ScoreableResults, build_scoreable_results


@dataclass(frozen=True)
class RecoveryDiagnosticSnapshot:
    scoreable_results: ScoreableResults
    selection_events: dict[str, dict[str, Any]]
    original_recovery_discrepancies: dict[str, tuple[str, ...]]


def build_recovery_diagnostic_snapshot(
    original_records: Mapping[str, dict[str, Any]],
    events: Sequence[dict[str, Any]],
) -> RecoveryDiagnosticSnapshot:
    """Preserve resolver output, selection provenance, and D10 discrepancies."""
    scoreable_results = build_scoreable_results(original_records, events)
    selection_events = {
        event["observation_key"]: deepcopy(event)
        for event in events
        if event["event_type"] == "recovery_selected_for_evaluation"
    }

    discrepancies: dict[str, tuple[str, ...]] = {}
    for key, original in original_records.items():
        if not (
            original.get("parse_ok") is True
            and original.get("schema_ok") is True
            and isinstance(original.get("parsed_json"), dict)
            and original.get("error") is None
        ):
            continue
        different_recoveries = tuple(sorted(
            event["recovery_id"]
            for event in events
            if event["event_type"] == "recovery_completed"
            and event["observation_key"] == key
            and event["result"]["parsed_json"] != original["parsed_json"]
        ))
        if different_recoveries:
            discrepancies[key] = different_recoveries

    return RecoveryDiagnosticSnapshot(
        scoreable_results=scoreable_results,
        selection_events=selection_events,
        original_recovery_discrepancies=discrepancies,
    )
