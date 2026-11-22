"""Append-only recovery events and deterministic result resolution."""

import json
import math
import os
import re
import stat
import threading
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from uuid import UUID, uuid4

from src.results_store import RECORD_FIELDS, result_key

try:
    import fcntl
except ImportError:  # pragma: no cover - this project targets macOS/POSIX
    fcntl = None

RECOVERY_LEDGER_PATH = (
    Path(__file__).resolve().parents[1] / "outputs" / "recovery_events.jsonl"
)

EVENT_FIELDS = {
    "recovery_started": {
        "event_type",
        "recovery_id",
        "observation_key",
        "timestamp_utc",
        "provenance",
    },
    "recovery_completed": {
        "event_type",
        "recovery_id",
        "observation_key",
        "timestamp_utc",
        "provenance",
        "result",
    },
    "recovery_adjudicated": {
        "event_type",
        "recovery_id",
        "observation_key",
        "timestamp_utc",
        "provenance",
        "decision",
    },
    "recovery_response_failed": {
        "event_type",
        "event_version",
        "recovery_id",
        "observation_key",
        "timestamp_utc",
        "provenance",
    },
    "recovery_selected_for_evaluation": {
        "event_type",
        "selection_id",
        "observation_key",
        "recovery_id",
        "timestamp_utc",
        "provenance",
    },
}

_PROVENANCE_FIELDS = {"actor", "reference"}
_RUN_KEY_RE = re.compile(r"^[1-9][0-9]*$")
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SECRET_MARKER_RE = re.compile(
    r"(?i)(?:gsk_[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{8,}|bearer)"
)
_EVALUATION_STATUSES = {
    "original_selected",
    "recovery_selected",
    "recovery_selection_required",
    "unresolved_original_failure",
    "conflict_requires_review",
}


@dataclass(frozen=True)
class RecoveryResolution:
    observation_key: str
    provider_outcome_uncertainty: tuple[str, ...]
    recovery_attempt_diagnostics: tuple["RecoveryAttemptDiagnostic", ...]
    evaluation_selection_status: Literal[
        "original_selected",
        "recovery_selected",
        "recovery_selection_required",
        "unresolved_original_failure",
        "conflict_requires_review",
    ]
    scoreable_result: dict[str, Any] | None
    scoreable_source: Literal["original", "recovery"] | None
    successful_recovery_ids: tuple[str, ...]
    conflicts_requiring_review: tuple[str, ...]
    evaluation_selection_id: str | None
    scoreable: bool
    diagnostic: str


@dataclass(frozen=True)
class RecoveryAttemptDiagnostic:
    recovery_id: str
    outcome: Literal[
        "unresolved_start",
        "response_observed_unsuccessful",
        "successful_completion",
    ]
    adjudicated: bool
    provider_processing_billing_uncertain: bool


@dataclass(frozen=True)
class ScoreableResults:
    records: tuple[dict[str, Any], ...]
    diagnostics: dict[str, RecoveryResolution]


class RecoveryLedgerLockError(RuntimeError):
    """The recovery ledger's required OS-level lock could not be acquired."""


class RecoveryLedgerIdentityError(ValueError):
    """The recovery ledger path no longer identifies its established file."""


def _validate_observation_key(key: Any) -> None:
    if not isinstance(key, str):
        raise ValueError("Recovery observation key must be a string.")
    parts = key.split("|")
    if (
        len(parts) != 4
        or any(not part for part in parts)
        or not _RUN_KEY_RE.fullmatch(parts[3])
    ):
        raise ValueError("Recovery observation key has an invalid format.")


def _parse_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Recovery timestamp must be a UTC string.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError("Recovery timestamp is invalid.") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Recovery timestamp must include a UTC offset.")
    return parsed.astimezone(timezone.utc)


def _validate_recovery_id(value: Any) -> None:
    if not isinstance(value, str):
        raise ValueError("Recovery ID must be a UUID string.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError("Recovery ID must be a UUID string.") from error
    if str(parsed) != value:
        raise ValueError("Recovery ID must use canonical lowercase UUID form.")


def _validate_provenance(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != _PROVENANCE_FIELDS:
        raise ValueError("Recovery provenance has an invalid schema.")
    for field in _PROVENANCE_FIELDS:
        item = value[field]
        if (
            not isinstance(item, str)
            or not _SAFE_IDENTIFIER_RE.fullmatch(item)
            or _SECRET_MARKER_RE.search(item)
        ):
            raise ValueError(f"Recovery provenance field {field!r} is invalid.")


def _validate_result_record(record: Any, expected_key: str) -> None:
    if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
        raise ValueError("Recovery result has an invalid result-record schema.")
    try:
        actual_key = result_key(record)
    except (KeyError, TypeError) as error:
        raise ValueError("Recovery result has an invalid observation identity.") from error
    if actual_key != expected_key:
        raise ValueError("Recovery result identity does not match its observation key.")

    for field in ("split", "record_id", "prompt_id"):
        if not isinstance(record[field], str) or not record[field]:
            raise ValueError(f"Recovery result field {field!r} is invalid.")
    if type(record["run"]) is not int or record["run"] < 1:
        raise ValueError("Recovery result run must be a positive integer.")
    if record["raw_output"] is not None and not isinstance(record["raw_output"], str):
        raise ValueError("Recovery result raw_output must be a string or null.")
    if record["parsed_json"] is not None and not isinstance(record["parsed_json"], dict):
        raise ValueError("Recovery result parsed_json must be an object or null.")
    if type(record["parse_ok"]) is not bool or type(record["schema_ok"]) is not bool:
        raise ValueError("Recovery result parse/schema flags must be booleans.")
    if record["error"] is not None and not isinstance(record["error"], str):
        raise ValueError("Recovery result error must be a string or null.")
    if isinstance(record["error"], str) and _SECRET_MARKER_RE.search(record["error"]):
        raise ValueError("Recovery result error contains a forbidden credential marker.")
    for field in ("input_tokens", "output_tokens", "reasoning_tokens"):
        value = record[field]
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"Recovery result field {field!r} is invalid.")
    latency = record["latency_seconds"]
    if latency is not None and (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or not math.isfinite(latency)
        or latency < 0
    ):
        raise ValueError("Recovery result latency_seconds is invalid.")
    _parse_utc_timestamp(record["timestamp_utc"])


def _validate_event(event: Any) -> None:
    if not isinstance(event, dict):
        raise ValueError("Recovery event must be a JSON object.")
    event_type = event.get("event_type")
    if (
        not isinstance(event_type, str)
        or event_type not in EVENT_FIELDS
        or set(event) != EVENT_FIELDS[event_type]
    ):
        raise ValueError("Recovery event has an invalid schema.")
    _validate_observation_key(event["observation_key"])
    _parse_utc_timestamp(event["timestamp_utc"])
    _validate_provenance(event["provenance"])
    if event_type == "recovery_selected_for_evaluation":
        _validate_recovery_id(event["selection_id"])
        _validate_recovery_id(event["recovery_id"])
    else:
        _validate_recovery_id(event["recovery_id"])
    if event_type == "recovery_completed":
        _validate_result_record(event["result"], event["observation_key"])
        if not _is_successful_result(event["result"]):
            raise ValueError(
                "Completed recovery result does not satisfy the "
                "successful-result predicate."
            )
    elif event_type == "recovery_response_failed":
        if type(event["event_version"]) is not int or event["event_version"] != 1:
            raise ValueError("Recovery response-failure event version is invalid.")
    elif event_type == "recovery_adjudicated":
        if event["decision"] != "authorize_further_attempt":
            raise ValueError("Recovery adjudication decision is invalid.")


def _reduce_events(
    events: Sequence[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    states: dict[str, dict[str, Any]] = {}
    states_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    selections: dict[str, dict[str, Any]] = {}
    selection_ids: set[str] = set()

    for event in events:
        _validate_event(event)
        event_type = event["event_type"]
        key = event["observation_key"]
        timestamp = _parse_utc_timestamp(event["timestamp_utc"])

        if event_type == "recovery_started":
            recovery_id = event["recovery_id"]
            if recovery_id in states:
                raise ValueError("Recovery ID was started more than once.")
            if any(
                state["completed"] is None and state["adjudicated"] is None
                for state in states_by_key[key]
            ):
                raise ValueError(
                    "An uncertain recovery blocks another recovery until adjudicated."
                )
            state = {
                "recovery_id": recovery_id,
                "observation_key": key,
                "started": event,
                "started_at": timestamp,
                "completed": None,
                "completed_at": None,
                "response_failed": None,
                "adjudicated": None,
            }
            states[recovery_id] = state
            states_by_key[key].append(state)
            continue

        if event_type == "recovery_selected_for_evaluation":
            selection_id = event["selection_id"]
            if selection_id in selection_ids:
                raise ValueError("Evaluation selection ID was used more than once.")
            if key in selections:
                raise ValueError(
                    "An observation already has an evaluation selection decision."
                )
            state = states.get(event["recovery_id"])
            if state is None:
                raise ValueError("Evaluation selection references an unknown recovery.")
            if state["observation_key"] != key:
                raise ValueError("Evaluation selection observation key changed.")
            if state["completed"] is None:
                raise ValueError(
                    "Evaluation selection requires a completed recovery."
                )
            if not _is_successful_result(state["completed"]["result"]):
                raise ValueError(
                    "Evaluation selection requires a usable recovery result."
                )
            if timestamp < state["completed_at"]:
                raise ValueError(
                    "Evaluation selection timestamp precedes recovery completion."
                )
            selection_ids.add(selection_id)
            selections[key] = event
            continue

        recovery_id = event["recovery_id"]
        state = states.get(recovery_id)
        if state is None:
            raise ValueError("Recovery event has no corresponding start event.")
        if state["observation_key"] != key:
            raise ValueError("Recovery event observation key changed.")
        if timestamp < state["started_at"]:
            raise ValueError("Recovery event timestamp precedes its start event.")
        if state["completed"] is not None or state["adjudicated"] is not None:
            raise ValueError("Recovery has more than one terminal event.")

        if event_type == "recovery_response_failed":
            if state["response_failed"] is not None:
                raise ValueError("Recovery response-failure event was recorded more than once.")
            state["response_failed"] = event
        elif event_type == "recovery_completed":
            if state["response_failed"] is not None:
                raise ValueError(
                    "A recovery cannot be completed after an observed unsuccessful response."
                )
            state["completed"] = event
            state["completed_at"] = timestamp
        else:
            state["adjudicated"] = event

    return states, selections


def validate_recovery_events(events: Sequence[dict[str, Any]]) -> None:
    """Validate event schemas and the append-only lifecycle sequence."""
    _reduce_events(events)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant {value}.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object contains a duplicate field.")
        result[key] = value
    return result


class RecoveryLedger:
    """Append-only recovery event ledger; it never edits the results store."""

    __slots__ = (
        "_canonical_path",
        "_canonical_lock_path",
        "_ledger_identity",
        "_identity_guard",
    )
    _IMMUTABLE_ATTRIBUTES = frozenset({
        "_canonical_path",
        "_canonical_lock_path",
        "_ledger_identity",
        "_identity_guard",
    })

    def __setattr__(self, name: str, value: Any) -> None:
        if (
            name in self._IMMUTABLE_ATTRIBUTES
            and hasattr(self, name)
        ):
            raise AttributeError("Recovery ledger identity is immutable.")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if name in self._IMMUTABLE_ATTRIBUTES:
            raise AttributeError("Recovery ledger identity is immutable.")
        object.__delattr__(self, name)

    def __init__(self, path: Path = RECOVERY_LEDGER_PATH):
        try:
            canonical_path = Path(path).resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise ValueError(
                "Recovery ledger path could not be resolved safely."
            ) from error
        self._canonical_path = canonical_path
        self._canonical_lock_path = canonical_path.with_name(
            f"{canonical_path.name}.lock"
        )
        self._identity_guard = threading.Lock()
        self._ledger_identity = self._path_identity_from_lstat(canonical_path)

    @property
    def path(self) -> Path:
        return self._canonical_path

    @property
    def lock_path(self) -> Path:
        return self._canonical_lock_path

    def load_events(self) -> list[dict[str, Any]]:
        with self._locked(exclusive=False):
            return self._read_events_unlocked()

    @staticmethod
    def _required_open_flags() -> tuple[int, int]:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory = getattr(os, "O_DIRECTORY", None)
        if (
            nofollow is None
            or directory is None
            or os.open not in os.supports_dir_fd
        ):
            raise RecoveryLedgerLockError(
                "The recovery ledger requires POSIX no-follow, "
                "directory-relative file operations."
            )
        return nofollow, directory

    def _open_parent_directory(self) -> int:
        nofollow, directory = self._required_open_flags()
        return os.open(
            self._canonical_path.parent,
            os.O_RDONLY
            | directory
            | nofollow
            | getattr(os, "O_CLOEXEC", 0),
        )

    @staticmethod
    def _path_identity_from_lstat(path: Path) -> tuple[int, int] | None:
        try:
            path_stat = path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ValueError(
                "Recovery ledger path identity could not be checked."
            ) from error
        if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
            raise ValueError(
                "Recovery ledger path must be a regular file with one hard link."
            )
        return path_stat.st_dev, path_stat.st_ino

    def _validate_ledger_fd(self, ledger_fd: int) -> None:
        ledger_stat = os.fstat(ledger_fd)
        if not stat.S_ISREG(ledger_stat.st_mode) or ledger_stat.st_nlink != 1:
            raise OSError(
                "Recovery ledger must be a regular file with one hard link."
            )
        opened_identity = ledger_stat.st_dev, ledger_stat.st_ino
        with self._identity_guard:
            if self._ledger_identity is None:
                object.__setattr__(self, "_ledger_identity", opened_identity)
            elif opened_identity != self._ledger_identity:
                raise RecoveryLedgerIdentityError(
                    "Recovery ledger device/inode identity changed."
                )

    def _validate_path_identity(self) -> None:
        current_identity = self._path_identity_from_lstat(self._canonical_path)
        with self._identity_guard:
            expected_identity = self._ledger_identity
        if expected_identity is not None and current_identity != expected_identity:
            raise RecoveryLedgerIdentityError(
                "Recovery ledger device/inode identity changed."
            )

    @contextmanager
    def _locked(self, *, exclusive: bool):
        if fcntl is None:
            raise RecoveryLedgerLockError(
                "The recovery ledger requires POSIX fcntl file locking."
            )
        nofollow, _ = self._required_open_flags()
        lock_fd = None
        try:
            self._canonical_path.parent.mkdir(parents=True, exist_ok=True)
            self._validate_path_identity()
            flags = (
                os.O_CREAT
                | os.O_RDWR
                | nofollow
                | getattr(os, "O_CLOEXEC", 0)
            )
            lock_fd = os.open(self._canonical_lock_path, flags, 0o600)
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                raise OSError("Recovery lock path is not a regular file.")
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(lock_fd, operation)
        except OSError as error:
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
            raise RecoveryLedgerLockError(
                "The recovery ledger OS-level lock could not be acquired."
            ) from error
        try:
            yield
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError as error:
                os.close(lock_fd)
                raise RecoveryLedgerLockError(
                    "The recovery ledger OS-level lock could not be released."
                ) from error
            else:
                os.close(lock_fd)

    def _read_events_unlocked(self) -> list[dict[str, Any]]:
        parent_fd = None
        ledger_fd = None
        try:
            nofollow, _ = self._required_open_flags()
            parent_fd = self._open_parent_directory()
            try:
                ledger_fd = os.open(
                    self._canonical_path.name,
                    os.O_RDONLY
                    | nofollow
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                with self._identity_guard:
                    expected_identity = self._ledger_identity
                if expected_identity is not None:
                    raise RecoveryLedgerIdentityError(
                        "Established recovery ledger is missing."
                    )
                return []
            self._validate_ledger_fd(ledger_fd)
            with os.fdopen(ledger_fd, "rb") as source:
                ledger_fd = None
                contents = source.read()
        except OSError as error:
            raise ValueError("Recovery ledger could not be read safely.") from error
        finally:
            if ledger_fd is not None:
                os.close(ledger_fd)
            if parent_fd is not None:
                os.close(parent_fd)
        if not contents:
            return []
        if not contents.endswith(b"\n"):
            raise ValueError("Recovery ledger has an incomplete final JSONL line.")

        events = []
        for line_number, raw_line in enumerate(contents.splitlines(), start=1):
            if not raw_line:
                raise ValueError(f"Recovery ledger line {line_number} is empty.")
            try:
                line = raw_line.decode("utf-8")
                event = json.loads(
                    line,
                    parse_constant=_reject_json_constant,
                    object_pairs_hook=_unique_object,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise ValueError(
                    f"Recovery ledger line {line_number} is malformed."
                ) from error
            events.append(event)
        validate_recovery_events(events)
        return events

    def start_recovery(
        self,
        observation_key: str,
        *,
        actor: str,
        reference: str,
        recovery_id: str | None = None,
        timestamp_utc: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_type": "recovery_started",
            "recovery_id": str(uuid4()) if recovery_id is None else recovery_id,
            "observation_key": observation_key,
            "timestamp_utc": (
                datetime.now(timezone.utc).isoformat()
                if timestamp_utc is None else timestamp_utc
            ),
            "provenance": {"actor": actor, "reference": reference},
        }
        return self._append_event(event)

    def complete_recovery(
        self,
        recovery_id: str,
        result: dict[str, Any],
        *,
        actor: str,
        reference: str,
        timestamp_utc: str | None = None,
    ) -> dict[str, Any]:
        events = self.load_events()
        states, _ = _reduce_events(events)
        state = states.get(recovery_id)
        if state is None:
            raise ValueError("Cannot complete a recovery without a start event.")
        event = {
            "event_type": "recovery_completed",
            "recovery_id": recovery_id,
            "observation_key": state["observation_key"],
            "timestamp_utc": (
                datetime.now(timezone.utc).isoformat()
                if timestamp_utc is None else timestamp_utc
            ),
            "provenance": {"actor": actor, "reference": reference},
            "result": deepcopy(result),
        }
        return self._append_event(event)

    def record_unsuccessful_response(
        self,
        recovery_id: str,
        *,
        actor: str,
        reference: str,
        timestamp_utc: str | None = None,
    ) -> dict[str, Any]:
        """Record an affirmatively observed unsuccessful provider response."""
        events = self.load_events()
        states, _ = _reduce_events(events)
        state = states.get(recovery_id)
        if state is None:
            raise ValueError(
                "Cannot record an unsuccessful response without a start event."
            )
        event = {
            "event_type": "recovery_response_failed",
            "event_version": 1,
            "recovery_id": recovery_id,
            "observation_key": state["observation_key"],
            "timestamp_utc": (
                datetime.now(timezone.utc).isoformat()
                if timestamp_utc is None else timestamp_utc
            ),
            "provenance": {"actor": actor, "reference": reference},
        }
        return self._append_event(event)

    def adjudicate_recovery(
        self,
        recovery_id: str,
        *,
        actor: str,
        reference: str,
        timestamp_utc: str | None = None,
    ) -> dict[str, Any]:
        events = self.load_events()
        states, _ = _reduce_events(events)
        state = states.get(recovery_id)
        if state is None:
            raise ValueError("Cannot adjudicate a recovery without a start event.")
        event = {
            "event_type": "recovery_adjudicated",
            "recovery_id": recovery_id,
            "observation_key": state["observation_key"],
            "timestamp_utc": (
                datetime.now(timezone.utc).isoformat()
                if timestamp_utc is None else timestamp_utc
            ),
            "provenance": {"actor": actor, "reference": reference},
            "decision": "authorize_further_attempt",
        }
        return self._append_event(event)

    def select_recovery_for_evaluation(
        self,
        observation_key: str,
        recovery_id: str,
        *,
        actor: str,
        reference: str,
        selection_id: str | None = None,
        timestamp_utc: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_type": "recovery_selected_for_evaluation",
            "selection_id": str(uuid4()) if selection_id is None else selection_id,
            "observation_key": observation_key,
            "recovery_id": recovery_id,
            "timestamp_utc": (
                datetime.now(timezone.utc).isoformat()
                if timestamp_utc is None else timestamp_utc
            ),
            "provenance": {"actor": actor, "reference": reference},
        }
        return self._append_event(event)

    def _append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._locked(exclusive=True):
            events = self._read_events_unlocked()
            _reduce_events([*events, event])
            parent_fd = None
            ledger_fd = None
            try:
                nofollow, _ = self._required_open_flags()
                parent_fd = self._open_parent_directory()
                flags = (
                    os.O_WRONLY
                    | os.O_APPEND
                    | nofollow
                    | getattr(os, "O_CLOEXEC", 0)
                )
                with self._identity_guard:
                    expected_identity = self._ledger_identity
                if expected_identity is None:
                    try:
                        ledger_fd = os.open(
                            self._canonical_path.name,
                            flags | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=parent_fd,
                        )
                    except FileExistsError as error:
                        raise RecoveryLedgerIdentityError(
                            "Recovery ledger appeared during locked creation."
                        ) from error
                    created = True
                else:
                    ledger_fd = os.open(
                        self._canonical_path.name,
                        flags,
                        dir_fd=parent_fd,
                    )
                    created = False
                self._validate_ledger_fd(ledger_fd)
                output = os.fdopen(
                    ledger_fd,
                    "a",
                    encoding="utf-8",
                    newline="\n",
                )
                ledger_fd = None
                with output:
                    output.write(
                        json.dumps(
                            event,
                            ensure_ascii=False,
                            sort_keys=True,
                            allow_nan=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    output.flush()
                    os.fsync(output.fileno())
                if created:
                    os.fsync(parent_fd)
            except RecoveryLedgerIdentityError:
                raise
            except (OSError, TypeError, ValueError) as error:
                raise ValueError("Recovery event could not be appended safely.") from error
            finally:
                if ledger_fd is not None:
                    os.close(ledger_fd)
                if parent_fd is not None:
                    os.close(parent_fd)
        return deepcopy(event)


def _is_successful_result(record: dict[str, Any]) -> bool:
    return bool(
        record.get("parse_ok") is True
        and record.get("schema_ok") is True
        and isinstance(record.get("parsed_json"), dict)
        and record.get("error") is None
    )


def resolve_results(
    original_records: Mapping[str, dict[str, Any]],
    events: Sequence[dict[str, Any]],
) -> dict[str, RecoveryResolution]:
    """Resolve evaluation selection separately from provider uncertainty."""
    originals: dict[str, dict[str, Any]] = {}
    for key, record in original_records.items():
        _validate_observation_key(key)
        _validate_result_record(record, key)
        originals[key] = record

    states, selections = _reduce_events(events)
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for state in states.values():
        key = state["observation_key"]
        if key not in originals:
            raise ValueError("Recovery event references an unknown original key.")
        by_key[key].append(state)

    resolutions = {}
    for key in sorted(originals):
        original = originals[key]
        recovery_states = by_key.get(key, [])
        successful_ids = tuple(sorted(
            state["recovery_id"]
            for state in recovery_states
            if state["completed"] is not None
            and _is_successful_result(state["completed"]["result"])
        ))
        uncertain_ids = tuple(sorted(
            state["recovery_id"]
            for state in recovery_states
            if state["completed"] is None
        ))
        attempt_diagnostics = tuple(
            RecoveryAttemptDiagnostic(
                recovery_id=state["recovery_id"],
                outcome=(
                    "successful_completion"
                    if state["completed"] is not None
                    else (
                        "response_observed_unsuccessful"
                        if state["response_failed"] is not None
                        else "unresolved_start"
                    )
                ),
                adjudicated=state["adjudicated"] is not None,
                provider_processing_billing_uncertain=(
                    state["completed"] is None
                ),
            )
            for state in sorted(
                recovery_states, key=lambda item: item["recovery_id"]
            )
        )
        original_successful = _is_successful_result(original)
        selection = selections.get(key)
        conflicts = successful_ids if len(successful_ids) >= 2 else ()

        if conflicts:
            status = "conflict_requires_review"
            scoreable = None
            scoreable_source = None
            diagnostic = "Multiple successful recoveries require explicit review."
        elif original_successful:
            status = "original_selected"
            scoreable = deepcopy(original)
            scoreable_source = "original"
            diagnostic = (
                "The successful original remains selected; recovery selections "
                "cannot replace it."
            )
        elif (
            selection is not None
            and len(successful_ids) == 1
            and selection["recovery_id"] == successful_ids[0]
        ):
            status = "recovery_selected"
            selected_state = next(
                state for state in recovery_states
                if state["recovery_id"] == selection["recovery_id"]
            )
            scoreable = deepcopy(selected_state["completed"]["result"])
            scoreable_source = "recovery"
            diagnostic = (
                "An explicit evaluation-selection event approved this recovery."
            )
        elif len(successful_ids) == 1:
            status = "recovery_selection_required"
            scoreable = None
            scoreable_source = None
            diagnostic = (
                "One successful recovery exists but has no evaluation-selection "
                "decision."
            )
        else:
            status = "unresolved_original_failure"
            scoreable = None
            scoreable_source = None
            diagnostic = "No successful original or explicitly selected recovery exists."

        if status not in _EVALUATION_STATUSES:
            raise AssertionError("Resolver produced an unknown status.")
        resolutions[key] = RecoveryResolution(
            observation_key=key,
            provider_outcome_uncertainty=uncertain_ids,
            evaluation_selection_status=status,
            scoreable_result=scoreable,
            scoreable_source=scoreable_source,
            successful_recovery_ids=successful_ids,
            conflicts_requiring_review=conflicts,
            evaluation_selection_id=(
                selection["selection_id"] if selection is not None else None
            ),
            scoreable=scoreable is not None,
            diagnostic=diagnostic,
            recovery_attempt_diagnostics=attempt_diagnostics,
        )
    return resolutions


def build_scoreable_results(
    original_records: Mapping[str, dict[str, Any]],
    events: Sequence[dict[str, Any]],
) -> ScoreableResults:
    """Return only approved, conflict-free predictions and per-key diagnostics."""
    diagnostics = resolve_results(original_records, events)
    records = tuple(
        deepcopy(diagnostics[key].scoreable_result)
        for key in sorted(diagnostics)
        if diagnostics[key].scoreable_result is not None
    )
    return ScoreableResults(records=records, diagnostics=diagnostics)
