"""Atomic persistence for ClaimFast raw model responses."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

RESULTS_PATH = Path(__file__).resolve().parents[1] / "outputs" / "raw_responses.json"
RETRYABLE_FAILED_OBSERVATION_KEY = "dev|DEV-18|C|1"
RECORD_FIELDS = {
    "split",
    "record_id",
    "prompt_id",
    "run",
    "raw_output",
    "parsed_json",
    "parse_ok",
    "schema_ok",
    "error",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "latency_seconds",
    "timestamp_utc",
}


def result_key(record: dict[str, Any]) -> str:
    return (
        f"{record['split']}|{record['record_id']}|"
        f"{record['prompt_id']}|{record['run']}"
    )


def is_successful_result(record: dict[str, Any]) -> bool:
    return (
        record.get("error") is None
        and record.get("parse_ok") is True
        and record.get("schema_ok") is True
    )


class ResultsStore:
    def __init__(
        self,
        path: Path = RESULTS_PATH,
        meta: dict[str, Any] | None = None,
    ):
        self.path = Path(path)
        self.meta = dict(meta or {})
        self.records: dict[str, dict[str, Any]] = {}

    @classmethod
    def load(
        cls,
        path: Path = RESULTS_PATH,
        meta: dict[str, Any] | None = None,
    ) -> "ResultsStore":
        store = cls(path, meta)
        if not store.path.exists():
            return store
        try:
            data = json.loads(store.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Results file is unreadable or malformed.") from error
        if (
            not isinstance(data, dict)
            or set(data) != {"meta", "records"}
            or not isinstance(data["meta"], dict)
            or not isinstance(data["records"], dict)
        ):
            raise ValueError("Results file has an invalid top-level structure.")
        store.meta = data["meta"]
        for key, record in data["records"].items():
            if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
                raise ValueError(f"Results record {key!r} has an invalid schema.")
            if result_key(record) != key:
                raise ValueError(f"Results record key {key!r} does not match its fields.")
            reasoning_tokens = record["reasoning_tokens"]
            if reasoning_tokens is not None and (
                type(reasoning_tokens) is not int or reasoning_tokens < 0
            ):
                raise ValueError(
                    f"Results record {key!r} has an invalid reasoning token count."
                )
            store.records[key] = record
        return store

    def has_key(self, key: str) -> bool:
        return key in self.records

    def _validate_record(self, record: dict[str, Any]) -> None:
        if set(record) != RECORD_FIELDS:
            raise ValueError("Result record has an invalid schema.")
        reasoning_tokens = record["reasoning_tokens"]
        if reasoning_tokens is not None and (
            type(reasoning_tokens) is not int or reasoning_tokens < 0
        ):
            raise ValueError("Result record has an invalid reasoning token count.")

    def add_record(self, record: dict[str, Any]) -> None:
        self._validate_record(record)
        key = result_key(record)
        if key in self.records:
            raise ValueError(f"Duplicate result key: {key}")
        self.records[key] = record

    def replace_retryable_failed_record(self, record: dict[str, Any]) -> None:
        key = result_key(record)
        if key != RETRYABLE_FAILED_OBSERVATION_KEY:
            raise ValueError(f"Observation key is not retryable: {key}")
        self.replace_unsuccessful_record(record)

    def replace_unsuccessful_record(self, record: dict[str, Any]) -> None:
        self._validate_record(record)
        key = result_key(record)
        existing = self.records.get(key)
        if existing is None or is_successful_result(existing):
            raise ValueError("Only an existing unsuccessful result can be retried.")
        self.records[key] = record

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temp_path = Path(output.name)
                json.dump(
                    {"meta": self.meta, "records": self.records},
                    output,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()


def migrate_observations(
    store: ResultsStore,
    observations: Iterable[dict[str, Any]],
    split: str = "dev",
) -> int:
    migrated = 0
    for observation in observations:
        errors = observation.get("validation_errors") or []
        record = {
            "split": split,
            "record_id": observation["record_id"],
            "prompt_id": observation["prompt_id"],
            "run": observation["run_number"],
            "raw_output": observation.get("raw_model_response"),
            "parsed_json": observation.get("parsed_json"),
            "parse_ok": observation.get("json_parse_valid", False),
            "schema_ok": observation.get("json_valid", False),
            "error": "; ".join(str(error) for error in errors) or None,
            "input_tokens": observation.get("input_tokens"),
            "output_tokens": observation.get("output_tokens"),
            "reasoning_tokens": (
                observation.get("reasoning_tokens")
                if type(observation.get("reasoning_tokens")) is int
                and observation["reasoning_tokens"] >= 0
                else None
            ),
            "latency_seconds": observation.get("latency_seconds"),
            "timestamp_utc": observation.get("timestamp_utc"),
        }
        store.add_record(record)
        migrated += 1
    return migrated
