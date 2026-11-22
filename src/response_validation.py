"""Validation for ClaimFast's nine-field model response."""

import json
import re
from datetime import date
from typing import Any

REQUIRED_KEYS = {
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
CLAIM_TYPES = {
    "own_damage",
    "third_party_liability",
    "theft",
    "total_loss",
    "glass_only",
    "not_a_claim",
}
URGENCY_LEVELS = {"high", "medium", "low"}
STRING_OR_NULL_FIELDS = {
    "policy_number",
    "incident_date",
    "location",
    "vehicle_reg_no",
}
BOOLEAN_OR_NULL_FIELDS = {"injury_reported", "police_report_filed"}


def validate_response(response_text: str) -> tuple[Any | None, list[str]]:
    """Parse JSON and validate its exact nine-field output structure."""
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as error:
        return None, [
            f"Malformed JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ]

    if not isinstance(result, dict):
        return result, ["Top-level JSON value must be an object."]

    issues = []
    missing_keys = REQUIRED_KEYS - result.keys()
    extra_keys = result.keys() - REQUIRED_KEYS
    if missing_keys:
        issues.append(f"Missing required keys: {', '.join(sorted(missing_keys))}.")
    if extra_keys:
        issues.append(f"Unexpected keys: {', '.join(sorted(extra_keys))}.")

    if "claim_type" in result and (
        not isinstance(result["claim_type"], str)
        or result["claim_type"] not in CLAIM_TYPES
    ):
        issues.append("claim_type must be one of the six allowed labels.")
    if "urgency" in result and (
        not isinstance(result["urgency"], str)
        or result["urgency"] not in URGENCY_LEVELS
    ):
        issues.append("urgency must be high, medium, or low.")

    for field in STRING_OR_NULL_FIELDS & result.keys():
        if result[field] is not None and not isinstance(result[field], str):
            issues.append(f"{field} must be a string or null.")
    if "estimated_amount_inr" in result:
        amount = result["estimated_amount_inr"]
        if amount is not None and (type(amount) is not int or amount < 0):
            issues.append("estimated_amount_inr must be a non-negative integer or null.")
    for field in BOOLEAN_OR_NULL_FIELDS & result.keys():
        if result[field] is not None and type(result[field]) is not bool:
            issues.append(f"{field} must be a Boolean or null.")

    incident_date = result.get("incident_date")
    if isinstance(incident_date, str):
        try:
            parsed_date = date.fromisoformat(incident_date)
            if parsed_date.isoformat() != incident_date or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", incident_date
            ):
                raise ValueError
        except ValueError:
            issues.append("incident_date must be a valid YYYY-MM-DD date.")

    return result, issues
