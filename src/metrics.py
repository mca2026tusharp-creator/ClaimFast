"""Pure metric functions for ClaimFast gold records and stored predictions.

Location strict match is exact string equality. Lenient match lowercases and
strips punctuation, then accepts containment or Jaccard token overlap of at
least 0.5. Per-field F1 is macro-F1 over the distinct gold and valid predicted
values, with null treated as a value. Invalid outputs count as wrong and are
not treated as null. Hallucination rates use all expected predictions as the
per-field denominator; overall rate divides by expected predictions times the
seven non-classification fields. An invalid output has no field value to
hallucinate.
"""

import json
import math
import re
import unicodedata
from statistics import mean
from typing import Any, Sequence

from sklearn.metrics import accuracy_score, confusion_matrix, f1_score

CLAIM_TYPE_LABELS = (
    "own_damage",
    "third_party_liability",
    "theft",
    "total_loss",
    "glass_only",
    "not_a_claim",
)
URGENCY_LABELS = ("high", "medium", "low")
FIELD_NAMES = (
    "policy_number",
    "incident_date",
    "location",
    "vehicle_reg_no",
    "estimated_amount_inr",
    "injury_reported",
    "police_report_filed",
)
INVALID_PREDICTION = "__invalid_prediction__"
IDENTIFIER_FIELDS = {"policy_number", "vehicle_reg_no"}


def _is_usable_result(result: dict[str, Any] | None) -> bool:
    return bool(
        result
        and result.get("parse_ok") is True
        and result.get("schema_ok") is True
        and isinstance(result.get("parsed_json"), dict)
        and result.get("error") is None
    )


def _prediction_value(
    result: dict[str, Any] | None,
    field: str,
) -> Any:
    if not _is_usable_result(result):
        return INVALID_PREDICTION
    parsed = result["parsed_json"]
    return parsed.get(field, INVALID_PREDICTION)


def _classification_metrics(
    gold_values: Sequence[str],
    predicted_values: Sequence[Any],
    fixed_labels: Sequence[str],
) -> dict[str, Any]:
    if not gold_values:
        labels = [*fixed_labels, INVALID_PREDICTION]
        return {
            "accuracy": None,
            "macro_f1": None,
            "labels": labels,
            "confusion_matrix": [[0] * len(labels) for _ in labels],
        }

    actual = list(gold_values)
    predicted = [
        value if value in fixed_labels else INVALID_PREDICTION
        for value in predicted_values
    ]
    matrix_labels = [*fixed_labels, INVALID_PREDICTION]
    return {
        "accuracy": float(accuracy_score(actual, predicted)),
        "macro_f1": float(
            f1_score(
                actual,
                predicted,
                labels=list(fixed_labels),
                average="macro",
                zero_division=0,
            )
        ),
        "labels": matrix_labels,
        "confusion_matrix": confusion_matrix(
            actual,
            predicted,
            labels=matrix_labels,
        ).tolist(),
    }


def _canonical_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _field_f1(gold_values: Sequence[Any], predicted_values: Sequence[Any]) -> float | None:
    if not gold_values:
        return None
    gold = [_canonical_value(value) for value in gold_values]
    predicted = [
        INVALID_PREDICTION
        if value == INVALID_PREDICTION
        else _canonical_value(value)
        for value in predicted_values
    ]
    labels = sorted(
        set(gold) | {value for value in predicted if value != INVALID_PREDICTION}
    )
    return float(
        f1_score(
            gold,
            predicted,
            labels=labels,
            average="macro",
            zero_division=0,
        )
    )


def _normalized_location(value: str) -> str:
    characters = (
        " " if unicodedata.category(character).startswith("P") else character
        for character in value.casefold()
    )
    return " ".join("".join(characters).split())


def lenient_location_match(gold: Any, predicted: Any) -> bool:
    """Return whether two locations match by normalized containment or overlap."""
    if gold is None or predicted is None:
        return gold is predicted
    if not isinstance(gold, str) or not isinstance(predicted, str):
        return False

    gold_text = _normalized_location(gold)
    predicted_text = _normalized_location(predicted)
    if not gold_text or not predicted_text:
        return gold == predicted
    if gold_text in predicted_text or predicted_text in gold_text:
        return True

    gold_tokens = set(gold_text.split())
    predicted_tokens = set(predicted_text.split())
    union = gold_tokens | predicted_tokens
    return bool(
        union
        and len(gold_tokens & predicted_tokens) / len(union) >= 0.5
    )


def _normalized_identifier(value: str) -> str:
    return re.sub(r"[\s-]+", "", value).upper()


def _is_hallucinated(
    field: str,
    gold: Any,
    predicted: Any,
    message: str,
) -> bool:
    if predicted == INVALID_PREDICTION or predicted is None:
        return False
    if gold is None:
        return True
    if field in IDENTIFIER_FIELDS:
        if not isinstance(predicted, str):
            return True
        return _normalized_identifier(predicted) not in _normalized_identifier(message)
    return False


def _mean_numeric(
    results: Sequence[dict[str, Any] | None],
    field: str,
) -> float | None:
    values = [
        result[field]
        for result in results
        if result is not None
        and isinstance(result.get(field), (int, float))
        and not isinstance(result.get(field), bool)
        and math.isfinite(result[field])
        and result[field] >= 0
    ]
    return float(mean(values)) if values else None


def _run_agreement(
    gold_records: Sequence[dict[str, Any]],
    predictions: dict[tuple[str, int], dict[str, Any]],
    run_numbers: Sequence[int],
) -> dict[str, float | None]:
    if len(run_numbers) < 2 or not gold_records:
        return {"parsed_json": None, "claim_type": None}

    identical_json = 0
    identical_claim_type = 0
    for gold_record in gold_records:
        results = [
            predictions.get((gold_record["record_id"], run))
            for run in run_numbers
        ]
        if not all(_is_usable_result(result) for result in results):
            continue
        parsed_values = [result["parsed_json"] for result in results]
        canonical = [
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for value in parsed_values
        ]
        if len(set(canonical)) == 1:
            identical_json += 1
        claim_types = [value.get("claim_type") for value in parsed_values]
        if (
            all(value in CLAIM_TYPE_LABELS for value in claim_types)
            and len(set(claim_types)) == 1
        ):
            identical_claim_type += 1

    count = len(gold_records)
    return {
        "parsed_json": identical_json / count,
        "claim_type": identical_claim_type / count,
    }


def score_predictions(
    gold_records: Sequence[dict[str, Any]],
    prediction_records: Sequence[dict[str, Any]],
    run_numbers: Sequence[int],
    *,
    input_price_per_m: float | None = None,
    output_price_per_m: float | None = None,
) -> dict[str, Any]:
    """Score one split, prompt, and view without reading or writing files.

    Each gold item contains ``record_id``, ``label``, and
    ``original_message``. Predictions contain ``record_id``, ``run``,
    ``parsed_json``, ``parse_ok``, and ``schema_ok`` plus optional usage.
    Missing keys and failed parses are included as invalid predictions.
    """
    if any(type(run) is not int or run < 1 for run in run_numbers):
        raise ValueError("run_numbers must contain positive integers.")
    run_numbers = sorted(set(run_numbers))
    if not run_numbers:
        raise ValueError("run_numbers must contain positive integers.")

    gold_by_id = {record["record_id"]: record for record in gold_records}
    if len(gold_by_id) != len(gold_records):
        raise ValueError("Gold record IDs must be unique.")

    prediction_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    gold_ids = set(gold_by_id)
    for record in prediction_records:
        key = (record["record_id"], record["run"])
        if key[0] not in gold_ids or key[1] not in run_numbers:
            continue
        if key in prediction_by_key:
            raise ValueError(f"Duplicate prediction for {key!r}.")
        prediction_by_key[key] = record

    aligned_gold: list[dict[str, Any]] = []
    aligned_results: list[dict[str, Any] | None] = []
    for gold_record in gold_records:
        for run in run_numbers:
            aligned_gold.append(gold_record)
            aligned_results.append(
                prediction_by_key.get((gold_record["record_id"], run))
            )

    expected_count = len(aligned_gold)
    missing_count = sum(result is None for result in aligned_results)
    parsed_predictions = [
        _prediction_value(result, "claim_type") for result in aligned_results
    ]
    urgency_predictions = [
        _prediction_value(result, "urgency") for result in aligned_results
    ]
    claim_type = _classification_metrics(
        [record["label"]["claim_type"] for record in aligned_gold],
        parsed_predictions,
        CLAIM_TYPE_LABELS,
    )
    urgency = _classification_metrics(
        [record["label"]["urgency"] for record in aligned_gold],
        urgency_predictions,
        URGENCY_LABELS,
    )

    field_metrics: dict[str, dict[str, float | None]] = {}
    hallucination_counts = {field: 0 for field in FIELD_NAMES}
    lenient_location_matches = 0
    for field in FIELD_NAMES:
        gold_values = [record["label"][field] for record in aligned_gold]
        predicted_values = [
            _prediction_value(result, field) for result in aligned_results
        ]
        exact_matches = sum(
            gold == predicted
            for gold, predicted in zip(gold_values, predicted_values)
        )
        field_metrics[field] = {
            "exact_match": exact_matches / expected_count if expected_count else None,
            "macro_f1": _field_f1(gold_values, predicted_values),
        }
        if field == "location":
            lenient_location_matches = sum(
                lenient_location_match(gold, predicted)
                for gold, predicted in zip(gold_values, predicted_values)
            )
            field_metrics[field]["lenient_match"] = (
                lenient_location_matches / expected_count if expected_count else None
            )
        for gold_record, gold, predicted in zip(
            aligned_gold, gold_values, predicted_values
        ):
            if _is_hallucinated(
                field,
                gold,
                predicted,
                gold_record["original_message"],
            ):
                hallucination_counts[field] += 1

    hallucination_total = sum(hallucination_counts.values())
    hallucination = {
        "per_field": {
            field: {
                "count": hallucination_counts[field],
                "rate": (
                    hallucination_counts[field] / expected_count
                    if expected_count else None
                ),
            }
            for field in FIELD_NAMES
        },
        "overall": {
            "count": hallucination_total,
            "rate": (
                hallucination_total / (expected_count * len(FIELD_NAMES))
                if expected_count else None
            ),
        },
    }

    high_false_negatives = sum(
        gold["label"]["urgency"] == "high" and predicted != "high"
        for gold, predicted in zip(aligned_gold, urgency_predictions)
    )
    high_count = sum(
        gold["label"]["urgency"] == "high" for gold in aligned_gold
    )
    injury_predictions = [
        _prediction_value(result, "injury_reported") for result in aligned_results
    ]
    injury_false_negatives = sum(
        gold["label"]["injury_reported"] is True and predicted is not True
        for gold, predicted in zip(aligned_gold, injury_predictions)
    )
    injury_count = sum(
        gold["label"]["injury_reported"] is True for gold in aligned_gold
    )

    mean_input = _mean_numeric(aligned_results, "input_tokens")
    mean_output = _mean_numeric(aligned_results, "output_tokens")
    mean_reasoning = _mean_numeric(aligned_results, "reasoning_tokens")
    mean_latency = _mean_numeric(aligned_results, "latency_seconds")
    cost = None
    if (
        input_price_per_m is not None
        and output_price_per_m is not None
        and mean_input is not None
        and mean_output is not None
    ):
        cost = (
            1000
            * len(run_numbers)
            * (
                mean_input * input_price_per_m
                + mean_output * output_price_per_m
            )
            / 1_000_000
        )

    prediction_lookup = {
        (gold["record_id"], run): result
        for gold in gold_records
        for run in run_numbers
        if (result := prediction_by_key.get((gold["record_id"], run))) is not None
    }
    return {
        "record_count": len(gold_records),
        "run_count": len(run_numbers),
        "expected_predictions": expected_count,
        "missing_predictions": missing_count,
        "parse_ok_rate": (
            sum(result is not None and result.get("parse_ok") is True
                for result in aligned_results) / expected_count
            if expected_count else None
        ),
        "schema_ok_rate": (
            sum(result is not None and result.get("schema_ok") is True
                for result in aligned_results) / expected_count
            if expected_count else None
        ),
        "claim_type": claim_type,
        "urgency": urgency,
        "fields": field_metrics,
        "hallucination": hallucination,
        "run_to_run_agreement": _run_agreement(
            gold_records,
            prediction_lookup,
            run_numbers,
        ),
        "usage": {
            "mean_input_tokens": mean_input,
            "mean_output_tokens": mean_output,
            "mean_reasoning_tokens": mean_reasoning,
            "mean_latency_seconds": mean_latency,
            "cost_per_1000_claims": cost,
        },
        "safety_critical": {
            "high_urgency_recall": (
                (high_count - high_false_negatives) / high_count
                if high_count else None
            ),
            "high_urgency_false_negatives": high_false_negatives,
            "injury_reported_true_recall": (
                (injury_count - injury_false_negatives) / injury_count
                if injury_count else None
            ),
            "injury_reported_true_false_negatives": injury_false_negatives,
        },
    }
