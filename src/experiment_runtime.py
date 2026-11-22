"""Request-loop helpers for the simplified ClaimFast experiment runner."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from groq import APIStatusError

from src import config
from src.prompt_helpers import validate_model_output
from src.results_store import (
    RETRYABLE_FAILED_OBSERVATION_KEY,
    ResultsStore,
    is_successful_result,
    result_key,
)
from src.validate_dataset import validate_record

FALLBACK_WAITS = (5, 10, 20, 40, 40)
MAX_429_RETRIES = 5
RETRY_LOG_PATH = config.OUTPUTS_DIR / "retry_log.jsonl"
LEGACY_RETRY_LOG_FIELDS = {"key", "attempt", "timestamp_utc", "wait_seconds"}
RETRY_LOG_FIELDS = LEGACY_RETRY_LOG_FIELDS | {
    "http_status",
    "error_class",
    "retry_after_raw",
    "retry_after_seconds",
    "wait_source",
    "rate_limit_headers",
    "request_id",
}
RATE_LIMIT_HEADER_NAMES = {
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
}
_SAFE_RATE_LIMIT_REMAINING = re.compile(r"^\d{1,20}$")
_SAFE_RATE_LIMIT_RESET = re.compile(
    r"^(?:\d+(?:\.\d+)?(?:ns|us|ms|s|m|h))+$"
    r"|^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SECRET_LIKE_HEADER_VALUE = re.compile(
    r"(?i)(?:bearer\s+|(?:gsk_|sk-)[A-Za-z0-9_-]{16,})"
)
JSON_NUMBER_PATTERN = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")


class RetryAfterParseError(ValueError):
    """A supplied Retry-After value could not be parsed reliably."""


def _response_headers(error: APIStatusError) -> Any:
    headers = getattr(getattr(error, "response", None), "headers", {})
    return headers if hasattr(headers, "items") else {}


def _header_value(headers: Any, name: str) -> Any:
    for header_name, value in headers.items():
        if isinstance(header_name, str) and header_name.lower() == name:
            return value
    return None


def _safe_retry_after_raw(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if (
        not value
        or len(value) > 256
        or any(not 32 <= ord(char) < 127 for char in value)
        or _SECRET_LIKE_HEADER_VALUE.search(value)
    ):
        return None
    return value


def _safe_rate_limit_header_value(name: str, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) > 128:
        return None
    pattern = (
        _SAFE_RATE_LIMIT_REMAINING
        if "remaining-" in name
        else _SAFE_RATE_LIMIT_RESET
    )
    return value if pattern.fullmatch(value) else None


def _safe_rate_limit_headers(headers: Any) -> dict[str, str]:
    result = {}
    for name in RATE_LIMIT_HEADER_NAMES:
        value = _header_value(headers, name)
        safe_value = _safe_rate_limit_header_value(name, value)
        if safe_value is not None:
            result[name] = safe_value
    return result


def _safe_request_id(headers: Any) -> str | None:
    value = _header_value(headers, "x-request-id")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (
        not _SAFE_REQUEST_ID.fullmatch(value)
        or _SECRET_LIKE_HEADER_VALUE.search(value)
    ):
        return None
    return value


def _validate_retry_diagnostics(event: dict[str, Any], line_number: int) -> None:
    if type(event["http_status"]) is not int or event["http_status"] != 429:
        raise ValueError(f"Retry log line {line_number} has an invalid HTTP status.")
    if event["error_class"] != "rate_limited":
        raise ValueError(f"Retry log line {line_number} has an invalid error class.")
    raw = event["retry_after_raw"]
    if raw is not None and _safe_retry_after_raw(raw) != raw:
        raise ValueError(f"Retry log line {line_number} has an invalid Retry-After value.")
    parsed_wait = event["retry_after_seconds"]
    if parsed_wait is not None and (
        isinstance(parsed_wait, bool)
        or not isinstance(parsed_wait, (int, float))
        or not math.isfinite(parsed_wait)
        or parsed_wait < 0
    ):
        raise ValueError(f"Retry log line {line_number} has an invalid parsed Retry-After.")
    if event["wait_source"] not in {"header", "fallback"}:
        raise ValueError(f"Retry log line {line_number} has an invalid wait source.")
    if event["wait_source"] == "fallback" and parsed_wait is not None:
        raise ValueError(f"Retry log line {line_number} has inconsistent fallback metadata.")
    rate_headers = event["rate_limit_headers"]
    if (
        not isinstance(rate_headers, dict)
        or not set(rate_headers).issubset(RATE_LIMIT_HEADER_NAMES)
        or any(
            _safe_rate_limit_header_value(name, value) != value
            for name, value in rate_headers.items()
        )
    ):
        raise ValueError(f"Retry log line {line_number} has invalid rate-limit headers.")
    request_id = event["request_id"]
    if request_id is not None and (
        not isinstance(request_id, str)
        or not _SAFE_REQUEST_ID.fullmatch(request_id)
        or _SECRET_LIKE_HEADER_VALUE.search(request_id)
    ):
        raise ValueError(f"Retry log line {line_number} has an invalid request ID.")


@dataclass(frozen=True)
class RetryProgress:
    count: int
    deadline: datetime | None


def load_records(split: str) -> list[dict[str, Any]]:
    path = config.DATA_DIR / f"{split}.jsonl"
    records = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            record = json.loads(line)
            errors = validate_record(record, f"{path.name}:{line_number}")
            if errors:
                raise ValueError("; ".join(errors))
            records.append({
                "split": split,
                "record_id": f"{split.upper()}-{line_number:02d}",
                "message": record["original_message"],
            })
    return records


def _skip_json_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _skip_json_string(text: str, index: int) -> int:
    if index >= len(text) or text[index] != '"':
        raise ValueError("Expected a JSON string.")
    index += 1
    while index < len(text):
        character = text[index]
        if character == '"':
            return index + 1
        if ord(character) < 0x20:
            raise ValueError("Invalid control character in JSON string.")
        if character == "\\":
            index += 1
            if index >= len(text):
                break
            escape = text[index]
            if escape == "u":
                digits = text[index + 1:index + 5]
                if len(digits) != 4 or any(
                    digit not in "0123456789abcdefABCDEF" for digit in digits
                ):
                    raise ValueError("Invalid Unicode escape in JSON string.")
                index += 5
                continue
            if escape not in '"\\/bfnrt':
                raise ValueError("Invalid escape in JSON string.")
        index += 1
    raise ValueError("Unterminated JSON string.")


def _skip_json_value(text: str, index: int) -> int:
    index = _skip_json_whitespace(text, index)
    if index >= len(text):
        raise ValueError("Missing JSON value.")
    character = text[index]
    if character == '"':
        return _skip_json_string(text, index)
    if character == "{":
        index = _skip_json_whitespace(text, index + 1)
        if index < len(text) and text[index] == "}":
            return index + 1
        while True:
            index = _skip_json_string(text, index)
            index = _skip_json_whitespace(text, index)
            if index >= len(text) or text[index] != ":":
                raise ValueError("Invalid JSON object.")
            index = _skip_json_value(text, index + 1)
            index = _skip_json_whitespace(text, index)
            if index < len(text) and text[index] == "}":
                return index + 1
            if index >= len(text) or text[index] != ",":
                raise ValueError("Invalid JSON object.")
            index = _skip_json_whitespace(text, index + 1)
    if character == "[":
        index = _skip_json_whitespace(text, index + 1)
        if index < len(text) and text[index] == "]":
            return index + 1
        while True:
            index = _skip_json_value(text, index)
            index = _skip_json_whitespace(text, index)
            if index < len(text) and text[index] == "]":
                return index + 1
            if index >= len(text) or text[index] != ",":
                raise ValueError("Invalid JSON array.")
            index = _skip_json_whitespace(text, index + 1)

    for literal in ("true", "false", "null"):
        if text.startswith(literal, index):
            end = index + len(literal)
            if end == len(text) or text[end] in " \t\r\n,]}":
                return end
    number = JSON_NUMBER_PATTERN.match(text, index)
    if number is not None:
        end = number.end()
        if end == len(text) or text[end] in " \t\r\n,]}":
            return end
    raise ValueError("Invalid JSON value.")


def extract_inference_message(line: str) -> str:
    """Decode only original_message; validate other JSON values without loading them."""
    decoder = json.JSONDecoder()
    index = _skip_json_whitespace(line, 0)
    if index >= len(line) or line[index] != "{":
        raise ValueError("Expected a JSON object.")
    index += 1
    seen_keys: set[str] = set()
    message: str | None = None
    while True:
        index = _skip_json_whitespace(line, index)
        if index < len(line) and line[index] == "}":
            index += 1
            break
        try:
            key, key_end = decoder.raw_decode(line, index)
        except json.JSONDecodeError as error:
            raise ValueError("Invalid JSON object key.") from error
        if not isinstance(key, str) or key in seen_keys:
            raise ValueError("Invalid or duplicate JSON object key.")
        seen_keys.add(key)
        index = _skip_json_whitespace(line, key_end)
        if index >= len(line) or line[index] != ":":
            raise ValueError("Invalid JSON object.")
        index = _skip_json_whitespace(line, index + 1)
        if key == "original_message":
            try:
                value, index = decoder.raw_decode(line, index)
            except json.JSONDecodeError as error:
                raise ValueError("Invalid original_message JSON string.") from error
            if not isinstance(value, str) or message is not None:
                raise ValueError("original_message must be a single string.")
            message = value
        else:
            index = _skip_json_value(line, index)
        index = _skip_json_whitespace(line, index)
        if index < len(line) and line[index] == "}":
            index += 1
            break
        if index >= len(line) or line[index] != ",":
            raise ValueError("Invalid JSON object.")
        index += 1

    if _skip_json_whitespace(line, index) != len(line):
        raise ValueError("Unexpected content after JSON object.")
    if message is None or not message.strip():
        raise ValueError("original_message must be a non-empty string.")
    return message


def load_inference_records(
    split: str,
    expected_record_ids: list[str],
    path: Path | None = None,
    expected_dataset_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Load a message-only inference view without decoding dataset labels."""
    if split != "test":
        raise ValueError("The label-blind inference loader is reserved for the test split.")
    dataset_path = path or config.DATA_DIR / "test.jsonl"
    records = []
    digest = hashlib.sha256()
    with dataset_path.open("rb") as source:
        for line_number, raw_line in enumerate(source, start=1):
            digest.update(raw_line)
            try:
                line = raw_line.decode("utf-8")
                message = extract_inference_message(line)
            except (UnicodeDecodeError, ValueError) as error:
                raise ValueError(
                    f"{dataset_path.name}:{line_number}: invalid inference record."
                ) from error
            records.append({
                "split": split,
                "record_id": f"{split.upper()}-{line_number:02d}",
                "message": message,
            })
    if (
        expected_dataset_sha256 is not None
        and digest.hexdigest() != expected_dataset_sha256
    ):
        raise ValueError("Held-out dataset changed while loading the inference view.")
    actual_ids = [record["record_id"] for record in records]
    if actual_ids != expected_record_ids:
        raise ValueError("Held-out dataset record IDs do not match the approved plan.")
    return records


def retry_after_seconds(error: APIStatusError) -> float | None:
    headers = getattr(getattr(error, "response", None), "headers", {})
    value = next(
        (str(v) for k, v in headers.items() if str(k).lower() == "retry-after"),
        None,
    )
    if value is None:
        return None
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
        raise ValueError
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = max(
                0.0,
                (retry_at - datetime.now(timezone.utc)).total_seconds(),
            )
            if math.isfinite(seconds):
                return seconds
        except (TypeError, ValueError, OverflowError):
            pass
        raise RetryAfterParseError(
            "Retry-After header is present but cannot be parsed reliably."
        )


def load_retry_state(path: Path = RETRY_LOG_PATH) -> dict[str, RetryProgress]:
    """Load per-observation 429 counts and their latest retry deadlines."""
    state: dict[str, RetryProgress] = {}
    if not path.exists():
        return state
    with path.open(encoding="utf-8") as retry_log:
        for line_number, line in enumerate(retry_log, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Retry log line {line_number} is malformed."
                ) from error
            event_fields = frozenset(event) if isinstance(event, dict) else frozenset()
            if event_fields not in {
                frozenset(LEGACY_RETRY_LOG_FIELDS),
                frozenset(RETRY_LOG_FIELDS),
            }:
                raise ValueError(
                    f"Retry log line {line_number} has an invalid schema."
                )
            if event_fields == frozenset(RETRY_LOG_FIELDS):
                _validate_retry_diagnostics(event, line_number)
            key = event["key"]
            attempt = event["attempt"]
            timestamp = event["timestamp_utc"]
            wait_seconds = event["wait_seconds"]
            if not isinstance(key, str) or len(key.split("|")) != 4:
                raise ValueError(f"Retry log line {line_number} has an invalid key.")
            previous = state.get(key)
            if type(attempt) is not int or attempt != (
                previous.count if previous is not None else 0
            ) + 1:
                raise ValueError(
                    f"Retry log line {line_number} has an ambiguous attempt."
                )
            try:
                if not isinstance(timestamp, str):
                    raise ValueError
                parsed_timestamp = datetime.fromisoformat(
                    timestamp.replace("Z", "+00:00")
                )
                if parsed_timestamp.tzinfo is None:
                    raise ValueError
                parsed_timestamp = parsed_timestamp.astimezone(timezone.utc)
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError(
                    f"Retry log line {line_number} has an invalid timestamp."
                ) from error
            if wait_seconds is not None and (
                isinstance(wait_seconds, bool)
                or not isinstance(wait_seconds, (int, float))
                or not math.isfinite(wait_seconds)
                or wait_seconds < 0
            ):
                raise ValueError(
                    f"Retry log line {line_number} has an invalid wait."
                )
            try:
                deadline = (
                    None
                    if wait_seconds is None
                    else parsed_timestamp + timedelta(seconds=wait_seconds)
                )
            except OverflowError as error:
                raise ValueError(
                    f"Retry log line {line_number} has an invalid retry deadline."
                ) from error
            state[key] = RetryProgress(attempt, deadline)
    return state


def append_retry_event(
    path: Path,
    key: str,
    attempt: int,
    wait_seconds: float | None,
    *,
    timestamp_utc: datetime | None = None,
    retry_after_raw: str | None = None,
    retry_after_seconds: float | None = None,
    wait_source: str = "fallback",
    rate_limit_headers: dict[str, str] | None = None,
    request_id: str | None = None,
) -> datetime:
    """Append and durably flush a sanitized rate-limit event."""
    path.parent.mkdir(parents=True, exist_ok=True)
    recorded_at = timestamp_utc or datetime.now(timezone.utc)
    if recorded_at.tzinfo is None:
        raise ValueError("Retry event timestamp must include a timezone.")
    recorded_at = recorded_at.astimezone(timezone.utc)
    event = {
        "key": key,
        "attempt": attempt,
        "timestamp_utc": recorded_at.isoformat(),
        "wait_seconds": wait_seconds,
        "http_status": 429,
        "error_class": "rate_limited",
        "retry_after_raw": retry_after_raw,
        "retry_after_seconds": retry_after_seconds,
        "wait_source": wait_source,
        "rate_limit_headers": rate_limit_headers or {},
        "request_id": request_id,
    }
    with path.open("a", encoding="utf-8") as retry_log:
        retry_log.write(json.dumps(event, sort_keys=True) + "\n")
        retry_log.flush()
        os.fsync(retry_log.fileno())
    return recorded_at


def _token_count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _average_total_tokens(store: ResultsStore) -> int | None:
    totals = [
        record["input_tokens"] + record["output_tokens"]
        for record in store.records.values()
        if _token_count(record.get("input_tokens"))
        and _token_count(record.get("output_tokens"))
    ]
    return math.ceil(sum(totals) / len(totals)) if totals else None


def _average_input_tokens(store: ResultsStore) -> int | None:
    values = [
        record["input_tokens"]
        for record in store.records.values()
        if _token_count(record.get("input_tokens"))
    ]
    return math.ceil(sum(values) / len(values)) if values else None


def estimate_next_call_tokens(store: ResultsStore, prompt_text: str) -> int:
    """Estimate a call from observed total usage or prompt length plus output cap."""
    average = _average_total_tokens(store)
    if average is not None:
        return average
    return math.ceil(len(prompt_text) / 4) + config.MAX_TOKENS


def today_usage(
    store: ResultsStore,
    prompt_text_by_key: dict[str, str],
    limit: int,
) -> tuple[int, bool]:
    """Count today's UTC token usage, estimating incomplete records conservatively."""
    today = datetime.now(timezone.utc).date().isoformat()
    average_input = _average_input_tokens(store)
    total, estimated = 0, False
    for key, record in store.records.items():
        timestamp = record.get("timestamp_utc")
        try:
            parsed_timestamp = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )
            if parsed_timestamp.tzinfo is None:
                raise ValueError
            record_day = (
                parsed_timestamp.astimezone(timezone.utc).date().isoformat()
            )
        except (AttributeError, TypeError, ValueError):
            record_day = today
            estimated = True
        if record_day != today:
            continue

        input_tokens = record.get("input_tokens")
        if not _token_count(input_tokens):
            prompt_text = prompt_text_by_key.get(key)
            if prompt_text is not None:
                input_tokens = math.ceil(len(prompt_text) / 4)
            elif average_input is not None:
                input_tokens = average_input
            else:
                return limit, True
            estimated = True

        output_tokens = record.get("output_tokens")
        if not _token_count(output_tokens):
            output_tokens = config.MAX_TOKENS
            estimated = True

        total += input_tokens + output_tokens
        if total >= limit:
            return limit, estimated
    return total, estimated


def make_result(
    item: dict[str, Any],
    raw_output: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    reasoning_tokens: int | None,
    latency_seconds: float | None,
    timestamp_utc: str,
    error: str | None = None,
) -> dict[str, Any]:
    parse_ok, schema_ok, parsed, issues = validate_model_output(
        item["prompt_id"], raw_output
    )
    if error is None and issues:
        error = "; ".join(issues)
    return {
        "split": item["split"],
        "record_id": item["record_id"],
        "prompt_id": item["prompt_id"],
        "run": item["run"],
        "raw_output": raw_output,
        "parsed_json": parsed if parse_ok else None,
        "parse_ok": parse_ok,
        "schema_ok": schema_ok,
        "error": error,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "latency_seconds": latency_seconds,
        "timestamp_utc": timestamp_utc,
    }


def run_pending(
    pending: list[dict[str, Any]],
    client: Any,
    store: ResultsStore,
    max_calls: int | None = None,
    *,
    output=print,
    sleep=time.sleep,
    retry_log_path: Path = RETRY_LOG_PATH,
    prompt_text_by_key: dict[str, str] | None = None,
    no_daily_limit: bool = False,
    retry_state: dict[str, RetryProgress] | None = None,
    utc_now: Callable[[], datetime] | None = None,
    retry_failed_key: str | None = None,
    retry_failed_keys: set[str] | None = None,
) -> int:
    from groq import APIStatusError

    retry_failed_keys = set(retry_failed_keys or ())
    if retry_failed_key is not None:
        if retry_failed_key != RETRYABLE_FAILED_OBSERVATION_KEY:
            raise ValueError(f"Observation key is not retryable: {retry_failed_key}")
        retry_failed_keys.add(retry_failed_key)
    for key in retry_failed_keys:
        existing = store.records.get(key)
        if existing is None or is_successful_result(existing):
            raise ValueError("Only an existing unsuccessful result can be retried.")

    if retry_state is None:
        retry_state = load_retry_state(retry_log_path)
    if utc_now is None:
        utc_now = lambda: datetime.now(timezone.utc)
    if prompt_text_by_key is None:
        prompt_text_by_key = {
            result_key(item): item["prompt_text"] for item in pending
        }

    def save_result(result: dict[str, Any]) -> None:
        key = result_key(result)
        if retry_failed_key == key:
            store.replace_retryable_failed_record(result)
        elif key in retry_failed_keys:
            store.replace_unsuccessful_record(result)
        else:
            store.add_record(result)
        store.save()

    calls = 0
    for item in pending:
        key = result_key(item)
        if store.has_key(key) and key not in retry_failed_keys:
            continue
        prior_state = retry_state.get(key)
        if prior_state is not None and prior_state.deadline is None:
            raise ValueError(
                f"Retry log for {key} has no usable retry deadline; "
                "refusing to submit another request."
            )
        if max_calls is not None and calls >= max_calls:
            break
        prior_429_count = prior_state.count if prior_state is not None else 0
        if prior_429_count >= MAX_429_RETRIES + 1:
            result = make_result(
                item,
                None,
                None,
                None,
                None,
                None,
                datetime.now(timezone.utc).isoformat(),
                "HTTP 429 retry budget exhausted (5 retries)",
            )
            save_result(result)
            output("Saved result: HTTP 429 retry budget exhausted (5 retries)")
            continue

        raw_output = None
        input_tokens = output_tokens = reasoning_tokens = latency = error_text = None
        while True:
            limit = None if no_daily_limit else config.DAILY_TOKEN_LIMIT
            if limit is not None:
                used, estimated_usage = today_usage(
                    store, prompt_text_by_key, limit
                )
                if estimated_usage:
                    output(
                        "Missing stored usage or timestamp counted conservatively "
                        "for today (UTC)."
                    )
                next_estimate = estimate_next_call_tokens(
                    store, item["prompt_text"]
                )
                if used + next_estimate > limit:
                    today = datetime.now(timezone.utc).date().isoformat()
                    output(
                        f"Daily token limit would be exceeded for today (UTC, {today}): "
                        f"{used} used/estimated + {next_estimate} estimated for the "
                        f"next call > {limit}; stopping before request."
                    )
                    return 0
            remaining_retry_wait = 0.0
            current_state = retry_state.get(key)
            if current_state is not None:
                if current_state.deadline is None:
                    raise ValueError(
                        f"Retry log for {key} has no usable retry deadline; "
                        "refusing to submit another request."
                    )
                remaining_retry_wait = max(
                    0.0,
                    (current_state.deadline - utc_now()).total_seconds(),
                )
            request_delay = config.REQUEST_DELAY_SECONDS if calls else 0.0
            wait_seconds = max(request_delay, remaining_retry_wait)
            if wait_seconds > 0:
                sleep(wait_seconds)
            if (
                current_state is not None
                and current_state.deadline is not None
                and utc_now() < current_state.deadline
            ):
                raise ValueError(
                    f"Retry wait for {key} ended before its persisted deadline."
                )
            output(f"Call {calls + 1}: {key}")
            calls += 1
            started = time.perf_counter()
            try:
                response = client.chat.completions.create(
                    model=config.MODEL_NAME,
                    messages=[{"role": "user", "content": item["prompt_text"]}],
                    temperature=config.TEMPERATURE,
                    max_tokens=config.MAX_TOKENS,
                    reasoning_effort=config.REASONING_EFFORT,
                    reasoning_format=config.REASONING_FORMAT,
                )
                latency = time.perf_counter() - started
                choices = getattr(response, "choices", None)
                if choices:
                    content = getattr(choices[0].message, "content", None)
                    raw_output = content if isinstance(content, str) else None
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
                output_tokens = (
                    getattr(usage, "completion_tokens", None) if usage else None
                )
                completion_details = (
                    getattr(usage, "completion_tokens_details", None)
                    if usage else None
                )
                raw_reasoning_tokens = (
                    getattr(completion_details, "reasoning_tokens", None)
                    if completion_details else None
                )
                reasoning_tokens = (
                    raw_reasoning_tokens
                    if type(raw_reasoning_tokens) is int and raw_reasoning_tokens >= 0
                    else None
                )
                break
            except APIStatusError as exception:
                latency = time.perf_counter() - started
                if exception.status_code != 429:
                    error_text = f"HTTP {exception.status_code}"
                    break
                headers = _response_headers(exception)
                retry_after_raw = _safe_retry_after_raw(
                    _header_value(headers, "retry-after")
                )
                rate_limit_headers = _safe_rate_limit_headers(headers)
                request_id = _safe_request_id(headers)
                attempt = retry_state.get(key).count + 1 if key in retry_state else 1
                try:
                    wait_seconds = retry_after_seconds(exception)
                except RetryAfterParseError:
                    append_retry_event(
                        retry_log_path,
                        key,
                        attempt,
                        None,
                        timestamp_utc=utc_now(),
                        retry_after_raw=retry_after_raw,
                        wait_source="header",
                        rate_limit_headers=rate_limit_headers,
                        request_id=request_id,
                    )
                    retry_state[key] = RetryProgress(attempt, None)
                    result = make_result(
                        item,
                        None,
                        None,
                        None,
                        None,
                        latency,
                        datetime.now(timezone.utc).isoformat(),
                        "HTTP 429; unparseable Retry-After header",
                    )
                    save_result(result)
                    output(
                        "Unparseable Retry-After header; saved progress and "
                        "stopped safely."
                    )
                    return 1
                retry_after_seconds_value = wait_seconds
                if wait_seconds is None:
                    wait_seconds = FALLBACK_WAITS[
                        min(attempt - 1, len(FALLBACK_WAITS) - 1)
                    ]
                    wait_source = "fallback"
                else:
                    wait_source = "header"
                retry_time = append_retry_event(
                    retry_log_path,
                    key,
                    attempt,
                    wait_seconds,
                    timestamp_utc=utc_now(),
                    retry_after_raw=retry_after_raw,
                    retry_after_seconds=retry_after_seconds_value,
                    wait_source=wait_source,
                    rate_limit_headers=rate_limit_headers,
                    request_id=request_id,
                )
                retry_state[key] = RetryProgress(
                    attempt,
                    retry_time + timedelta(seconds=wait_seconds),
                )
                if attempt >= MAX_429_RETRIES + 1:
                    error_text = "HTTP 429 after 5 retries"
                    break
                if max_calls is not None and calls >= max_calls:
                    output(
                        "HTTP 429 logged; max-calls limit reached before retry. "
                        "The observation remains resumable."
                    )
                    return 0
            except KeyboardInterrupt:
                error_text = "Interrupted; request outcome unknown"
                result = make_result(
                    item, None, None, None, None, latency,
                    datetime.now(timezone.utc).isoformat(), error_text,
                )
                save_result(result)
                output("Interrupted; current result saved.")
                return 130
            except Exception as exception:
                latency = time.perf_counter() - started
                error_text = type(exception).__name__
                break

        result = make_result(
            item,
            raw_output,
            input_tokens if type(input_tokens) is int and input_tokens >= 0 else None,
            output_tokens if type(output_tokens) is int and output_tokens >= 0 else None,
            reasoning_tokens,
            latency,
            datetime.now(timezone.utc).isoformat(),
            error_text,
        )
        save_result(result)
        output("Saved result: " + ("success" if error_text is None else error_text))
        limit = None if no_daily_limit else config.DAILY_TOKEN_LIMIT
        if limit is not None:
            used, estimated_usage = today_usage(
                store, prompt_text_by_key, limit
            )
            if estimated_usage:
                output(
                    "Missing stored usage or timestamp counted conservatively "
                    "for today (UTC)."
                )
            if used >= limit:
                today = datetime.now(timezone.utc).date().isoformat()
                output(f"Daily token limit reached for today (UTC, {today}); resume later.")
                return 0
    return 0
