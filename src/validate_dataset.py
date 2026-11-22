"""Validate ClaimFast JSONL datasets and report label distributions."""

import json
import re
import sys
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEV_PATH = PROJECT_ROOT / "data" / "dev.jsonl"
TEST_PATH = PROJECT_ROOT / "data" / "test.jsonl"

CLAIM_TYPES = {
    "own_damage",
    "third_party_liability",
    "theft",
    "total_loss",
    "glass_only",
    "not_a_claim",
}
URGENCY_LEVELS = {"high", "medium", "low"}
LABEL_FIELDS = {
    "claim_type",
    "urgency",
    "policy_number",
    "incident_date",
    "location",
    "vehicle_reg_no",
    "estimated_amount_inr",
    "injury_reported",
    "police_report_filed",
}
REQUIRED_RECORD_FIELDS = {"original_message", "label", "tags"}
OPTIONAL_RECORD_FIELDS = {"note"}

MONTH_NUMBERS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
ISO_DATE_PATTERN = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
DAY_MONTH_TEXT_PATTERN = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(?P<month>January|February|March|April|May|June|July|August|"
    r"September|Sept|Sep|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|"
    r"Aug|Oct|Nov|Dec)\b",
    re.IGNORECASE,
)
DAY_MONTH_NUMERIC_PATTERN = re.compile(
    r"(?<!\d)(?P<day>\d{1,2})/(?P<month>\d{1,2})(?!\d)"
)


def _is_supported_text(message: str, value: str) -> bool:
    return value.casefold() in message.casefold()


def _is_supported_registration(message: str, value: str) -> bool:
    compact_message = re.sub(r"[\s-]+", "", message).upper()
    compact_value = re.sub(r"[\s-]+", "", value).upper()
    return compact_value in compact_message


def _explicit_dates(message: str) -> set[date]:
    dates: set[date] = set()
    for match in ISO_DATE_PATTERN.finditer(message):
        try:
            dates.add(date.fromisoformat(match.group()))
        except ValueError:
            pass
    for match in DAY_MONTH_TEXT_PATTERN.finditer(message):
        day = int(match.group("day"))
        month = MONTH_NUMBERS[match.group("month").casefold()]
        try:
            dates.add(date(2026, month, day))
        except ValueError:
            pass
    for match in DAY_MONTH_NUMERIC_PATTERN.finditer(message):
        try:
            dates.add(date(2026, int(match.group("month")), int(match.group("day"))))
        except ValueError:
            pass
    return dates


def _is_supported_amount(message: str, value: int) -> bool:
    for match in re.finditer(
        r"(?:₹|INR\b|Rs\.?\s*)\s*([\d,]+)|([\d,]+)\s*(?:INR\b|rupees?\b|Rs\.?)",
        message,
        flags=re.IGNORECASE,
    ):
        amount_text = match.group(1) or match.group(2)
        if int(amount_text.replace(",", "")) == value:
            return True
    for match in re.finditer(
        r"(?<![\w.])(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>k|lakh)\b",
        message,
        flags=re.IGNORECASE,
    ):
        multiplier = Decimal(1_000 if match.group("unit").casefold() == "k" else 100_000)
        try:
            if Decimal(match.group("number")) * multiplier == value:
                return True
        except InvalidOperation:
            continue
    for match in re.finditer(
        r"\b(?:approx(?:imately)?|around)\s+(?P<number>[\d,]+)\b"
        r"(?!\s*(?:[-–—]|\bto\b)\s*[\d,])",
        message,
        flags=re.IGNORECASE,
    ):
        if int(match.group("number").replace(",", "")) == value:
            return True
    return False


def _has_injury_evidence(message: str, value: bool) -> bool:
    text = message.casefold()
    if value:
        return bool(
            re.search(
                r"\b(injur\w*|hurt|wound\w*|fractur\w*|bleed\w*|hospital\w*|"
                r"clinic\w*|ambulance|stitches|cast|cut)\b|"
                r"\bbroken\s+(?:arm|leg|wrist|ankle|rib|finger)\b",
                text,
            )
        )
    return bool(
        re.search(
            r"\b(no|not|never|nobody|none|without)\b.{0,35}"
            r"\b(injur\w*|hurt|wound\w*|fracture\w*)\b",
            text,
        )
    )


def _has_unsafe_undrivable_evidence(message: str) -> bool:
    text = message.casefold()
    if re.search(
        r"\bundrivable\b|\bcan't be driven\b|\bcannot be driven\b",
        text,
    ) and re.search(r"\bunsafe\b", text):
        return True
    if re.search(
        r"\b(?:gaadi|car|vehicle)\b.{0,40}\b(?:chalne|chalane)\s+layak\s+nahi\b",
        text,
    ):
        return True
    if re.search(
        r"\bbrakes?\b.{0,40}\b(?:fail\w*|not working)\b",
        text,
    ) and re.search(
        r"\b(?:can't|cannot|can not)\s+(?:be\s+)?driven\b|"
        r"\bgaadi\b.{0,40}\bchal\s+nahi\s+sakti\b",
        text,
    ):
        return True
    if re.search(
        r"\b(?:wheel|tyre|tire)\b.{0,35}\block\w*\b.{0,50}"
        r"\b(?:won't move|cannot move|can't move|hil nahi rahi|nahi hil rahi)\b",
        text,
    ):
        return True
    return bool(
        re.search(
            r"\b(?:atak\w*|stuck|stranded)\b.{0,60}"
            r"\b(?:chalana khatarnak|dangerous to drive|khatarnak hai chalana)\b",
            text,
        )
    )


def _has_police_evidence(message: str, value: bool) -> bool:
    text = message.casefold()
    if value:
        return bool(
            re.search(
                r"\b(?:filed|lodged|registered|made)\b.{0,30}"
                r"\b(?:police report|report|fir|complaint)\b|"
                r"\b(?:police report|fir|complaint)\b.{0,30}"
                r"\b(?:filed|lodged|registered|made)\b|"
                r"\bpolice\b.{0,30}\b(?:filed|lodged|registered|made)\b.{0,20}\breport\b|"
                r"\breported it to the police\b",
                text,
            )
        )
    return bool(
        re.search(
            r"\b(?:no|not|never|haven't|hasn't|didn't|did not)\b.{0,35}"
            r"\b(?:police report|fir|police|complaint)\b",
            text,
        )
    )


def validate_record(record: Any, source: str) -> list[str]:
    """Return all schema and source-support errors for one decoded record."""
    errors: list[str] = []
    if not isinstance(record, dict):
        return [f"{source}: record must be a JSON object"]
    allowed_record_fields = REQUIRED_RECORD_FIELDS | OPTIONAL_RECORD_FIELDS
    if (
        not REQUIRED_RECORD_FIELDS.issubset(record)
        or set(record) - allowed_record_fields
    ):
        errors.append(
            f"{source}: record must contain {sorted(REQUIRED_RECORD_FIELDS)} "
            f"and may contain {sorted(OPTIONAL_RECORD_FIELDS)}"
        )

    message = record.get("original_message")
    if not isinstance(message, str) or not message.strip():
        errors.append(f"{source}: original_message must be a non-empty string")
        message = ""

    tags = record.get("tags")
    if not isinstance(tags, list) or any(
        not isinstance(tag, str) or not tag.strip() for tag in tags
    ):
        errors.append(f"{source}: tags must be a list of non-empty strings")
    note = record.get("note")
    if "note" in record and (
        not isinstance(note, str) or not note.strip() or "\n" in note or "\r" in note
    ):
        errors.append(f"{source}: note must be a non-empty single-line string")

    label = record.get("label")
    if not isinstance(label, dict):
        errors.append(f"{source}: label must be an object")
        return errors
    if set(label) != LABEL_FIELDS:
        errors.append(f"{source}: label fields must be exactly {sorted(LABEL_FIELDS)}")

    claim_type = label.get("claim_type")
    if not isinstance(claim_type, str) or claim_type not in CLAIM_TYPES:
        errors.append(f"{source}: invalid claim_type {claim_type!r}")

    urgency = label.get("urgency")
    if not isinstance(urgency, str) or urgency not in URGENCY_LEVELS:
        errors.append(f"{source}: invalid urgency {urgency!r}")

    for field in ("policy_number", "incident_date", "location", "vehicle_reg_no"):
        value = label.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{source}: {field} must be a non-empty string or null")

    policy_number = label.get("policy_number")
    if isinstance(policy_number, str) and not _is_supported_text(message, policy_number):
        errors.append(f"{source}: policy_number is not present in original_message")

    incident_date = label.get("incident_date")
    if isinstance(incident_date, str):
        try:
            parsed_date = date.fromisoformat(incident_date)
            if parsed_date.isoformat() != incident_date:
                raise ValueError
        except ValueError:
            errors.append(f"{source}: incident_date must be a valid ISO YYYY-MM-DD date")
        else:
            try:
                supported_dates = _explicit_dates(message)
            except (TypeError, ValueError):
                supported_dates = set()
            if parsed_date not in supported_dates:
                errors.append(
                    f"{source}: incident_date does not match an explicit date "
                    "in original_message"
                )

    location = label.get("location")
    if isinstance(location, str) and not _is_supported_text(message, location):
        errors.append(f"{source}: location is not present in original_message")

    registration = label.get("vehicle_reg_no")
    if isinstance(registration, str):
        if registration != registration.upper() or re.search(r"[\s-]", registration):
            errors.append(
                f"{source}: vehicle_reg_no must be uppercase and contain no spaces or hyphens"
            )
        if not _is_supported_registration(message, registration):
            errors.append(f"{source}: vehicle_reg_no is not supported by original_message")

    amount = label.get("estimated_amount_inr")
    if amount is not None and (type(amount) is not int or amount < 0):
        errors.append(f"{source}: estimated_amount_inr must be a non-negative integer or null")
    elif isinstance(amount, int) and not _is_supported_amount(message, amount):
        errors.append(f"{source}: estimated_amount_inr is not supported by original_message")

    for field in ("injury_reported", "police_report_filed"):
        value = label.get(field)
        if value is not None and type(value) is not bool:
            errors.append(f"{source}: {field} must be true, false, or null")
        elif isinstance(value, bool):
            has_evidence = (
                _has_injury_evidence(message, value)
                if field == "injury_reported"
                else _has_police_evidence(message, value)
            )
            if not has_evidence:
                errors.append(f"{source}: {field} is not explicitly supported by original_message")

    injury_reported = label.get("injury_reported")
    unsafe_vehicle = _has_unsafe_undrivable_evidence(message)
    if urgency == "high" and injury_reported is not True and not unsafe_vehicle:
        errors.append(
            f"{source}: high urgency requires reported injury or an explicitly undrivable, unsafe vehicle"
        )
    if isinstance(urgency, str) and urgency in {"medium", "low"} and (
        injury_reported is True or unsafe_vehicle
    ):
        errors.append(
            f"{source}: injury or an explicitly undrivable, unsafe vehicle requires high urgency"
        )

    return errors


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str], int]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    line_count = 0
    try:
        with path.open(encoding="utf-8") as dataset_file:
            for line_count, line in enumerate(dataset_file, start=1):
                if not line.strip():
                    errors.append(f"{path.name}:{line_count}: blank lines are not valid JSONL records")
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    errors.append(f"{path.name}:{line_count}: invalid JSON: {error.msg}")
                    continue
                records.append(record)
                errors.extend(validate_record(record, f"{path.name}:{line_count}"))
    except OSError as error:
        errors.append(f"{path}: cannot read dataset: {error}")
    return records, errors, line_count


def _check_split(
    path: Path, expected_count: int
) -> tuple[list[dict[str, Any]], list[str]]:
    records, errors, line_count = _load_jsonl(path)
    if line_count != expected_count:
        errors.append(
            f"{path.name}: expected {expected_count} lines, found {line_count}"
        )
    return records, errors


def validate_datasets(
    dev_path: Path = DEV_PATH, test_path: Path = TEST_PATH
) -> tuple[dict[str, list[dict[str, int]]], list[str]]:
    """Validate both splits and return per-split distributions and errors."""
    dev_records, dev_errors = _check_split(dev_path, 20)
    test_records, test_errors = _check_split(test_path, 60)
    errors = dev_errors + test_errors

    seen_messages: set[str] = set()
    for split_name, records in (("dev", dev_records), ("test", test_records)):
        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                continue
            message = record.get("original_message")
            if isinstance(message, str):
                normalized = " ".join(message.casefold().split())
                if normalized in seen_messages:
                    errors.append(
                        f"{split_name}:{index}: duplicate message within/across splits"
                    )
                seen_messages.add(normalized)

    distributions: dict[str, list[dict[str, int]]] = {}
    for split_name, records in (("dev", dev_records), ("test", test_records)):
        distributions[split_name] = [
            dict(sorted(Counter(
                record["label"]["claim_type"]
                for record in records
                if isinstance(record, dict)
                and isinstance(record.get("label"), dict)
                and isinstance(record["label"].get("claim_type"), str)
                and record["label"].get("claim_type") in CLAIM_TYPES
            ).items())),
            dict(sorted(Counter(
                record["label"]["urgency"]
                for record in records
                if isinstance(record, dict)
                and isinstance(record.get("label"), dict)
                and isinstance(record["label"].get("urgency"), str)
                and record["label"].get("urgency") in URGENCY_LEVELS
            ).items())),
        ]
    return distributions, errors


def main() -> int:
    distributions, errors = validate_datasets()
    for split_name in ("dev", "test"):
        claim_counts, urgency_counts = distributions[split_name]
        print(f"{split_name} claim_type distribution: {claim_counts}")
        print(f"{split_name} urgency distribution: {urgency_counts}")
    if errors:
        print(f"Validation failed with {len(errors)} error(s):")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Both datasets passed validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
