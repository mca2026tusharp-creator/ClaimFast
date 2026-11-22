"""Offline recovery orchestration around an injected provider callable."""

from collections.abc import Collection
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from src.prompt_helpers import validate_model_output
from src.recovery_ledger import RecoveryLedger, resolve_results
from src.results_store import ResultsStore, result_key


@dataclass(frozen=True)
class RecoveryRequest:
    observation_key: str
    recovery_id: str
    payload: str


@dataclass(frozen=True)
class ProviderResponse:
    raw_output: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    latency_seconds: float | None = None


class RecoveryProvider(Protocol):
    def __call__(self, request: RecoveryRequest) -> ProviderResponse: ...


class ApprovalVerifier(Protocol):
    def __call__(self, observation_key: str, actor: str, reference: str) -> bool: ...


class UnsuccessfulProviderResponse(Exception):
    """Signal that an unsuccessful response was actually received.

    The integration trusts the injected provider to raise this only after
    receipt; it does not independently verify transport-level receipt.
    """


@dataclass(frozen=True)
class RecoveryOutcome:
    recovery_id: str
    outcome: Literal[
        "successful_completion",
        "response_observed_unsuccessful",
    ]
    event: dict[str, Any]


def recover_observation(
    *,
    ledger: RecoveryLedger,
    original_store: ResultsStore,
    observation_key: str,
    approved_observation_keys: Collection[str],
    request_payload: str,
    provider: RecoveryProvider,
    approval_verifier: ApprovalVerifier,
    actor: str,
    approval_reference: str,
    recovery_id: str | None = None,
) -> RecoveryOutcome:
    """Start one explicitly approved recovery and invoke the provider at most once."""
    if isinstance(approved_observation_keys, (str, bytes)):
        raise ValueError("Approved observation keys must be a collection of keys.")
    if observation_key not in frozenset(approved_observation_keys):
        raise PermissionError("Observation key is not explicitly approved.")
    if not isinstance(request_payload, str) or not request_payload:
        raise ValueError("Recovery request payload must be a non-empty string.")
    if not isinstance(actor, str) or not actor:
        raise ValueError("Recovery actor provenance is required.")
    if not isinstance(approval_reference, str) or not approval_reference:
        raise ValueError("Durable approval reference is required.")
    if not callable(approval_verifier):
        raise ValueError("An approval-verification workflow is required.")
    if not callable(provider):
        raise ValueError("An injected recovery provider is required.")

    original_records = deepcopy(original_store.records)
    original = original_records.get(observation_key)
    if original is None or result_key(original) != observation_key:
        raise ValueError("Recovery requires an existing matching original result.")
    resolution = resolve_results(
        original_records,
        ledger.load_events(),
    )[observation_key]
    if resolution.scoreable_source == "original":
        raise ValueError("A successful original result is not eligible for recovery.")

    if approval_verifier(observation_key, actor, approval_reference) is not True:
        raise PermissionError("The approval workflow did not verify this recovery.")

    start = ledger.start_recovery(
        observation_key,
        actor=actor,
        reference=approval_reference,
        recovery_id=recovery_id,
    )
    started_recovery_id = start["recovery_id"]
    request = RecoveryRequest(
        observation_key=observation_key,
        recovery_id=started_recovery_id,
        payload=request_payload,
    )
    try:
        response = provider(request)
    except UnsuccessfulProviderResponse:
        event = ledger.record_unsuccessful_response(
            started_recovery_id,
            actor=actor,
            reference=approval_reference,
        )
        return RecoveryOutcome(
            started_recovery_id,
            "response_observed_unsuccessful",
            event,
        )

    if not isinstance(response, ProviderResponse):
        raise TypeError("Recovery provider returned an unsupported response type.")
    if response.raw_output is not None and not isinstance(response.raw_output, str):
        raise TypeError("Recovery response content must be a string or null.")

    parts = observation_key.split("|")
    parse_ok, schema_ok, parsed_json, _ = validate_model_output(
        parts[2],
        response.raw_output,
    )
    if not parse_ok or not schema_ok or not isinstance(parsed_json, dict):
        event = ledger.record_unsuccessful_response(
            started_recovery_id,
            actor=actor,
            reference=approval_reference,
        )
        return RecoveryOutcome(
            started_recovery_id,
            "response_observed_unsuccessful",
            event,
        )

    result = {
        "split": parts[0],
        "record_id": parts[1],
        "prompt_id": parts[2],
        "run": int(parts[3]),
        "raw_output": response.raw_output,
        "parsed_json": parsed_json,
        "parse_ok": True,
        "schema_ok": True,
        "error": None,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "reasoning_tokens": response.reasoning_tokens,
        "latency_seconds": response.latency_seconds,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    event = ledger.complete_recovery(
        started_recovery_id,
        result,
        actor=actor,
        reference=approval_reference,
    )
    return RecoveryOutcome(
        started_recovery_id,
        "successful_completion",
        event,
    )
