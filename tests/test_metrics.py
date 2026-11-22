import pytest

from src.metrics import lenient_location_match, score_predictions


def _label(claim_type, urgency, **overrides):
    label = {
        "claim_type": claim_type,
        "urgency": urgency,
        "policy_number": None,
        "incident_date": None,
        "location": None,
        "vehicle_reg_no": None,
        "estimated_amount_inr": None,
        "injury_reported": None,
        "police_report_filed": None,
    }
    label.update(overrides)
    return label


def _gold(record_id, claim_type, urgency, message, **overrides):
    return {
        "record_id": record_id,
        "label": _label(claim_type, urgency, **overrides),
        "original_message": message,
    }


def _parsed(label, **overrides):
    prediction = dict(label)
    prediction.update(overrides)
    return prediction


def _result(record_id, parsed_json, run=1, **overrides):
    result = {
        "record_id": record_id,
        "run": run,
        "parsed_json": parsed_json,
        "parse_ok": True,
        "schema_ok": True,
        "input_tokens": 10 * int(record_id[-1]),
        "output_tokens": 2 * int(record_id[-1]),
        "reasoning_tokens": int(record_id[-1]) % 3,
        "latency_seconds": int(record_id[-1]) / 10,
    }
    result.update(overrides)
    return result


def _toy_data():
    gold = [
        _gold("DEV-01", "own_damage", "medium", "Car damage at the depot."),
        _gold(
            "DEV-02",
            "own_damage",
            "medium",
            "Policy MOT-123; damage in New York City.",
            policy_number="MOT-123",
            location="New York City",
        ),
        _gold(
            "DEV-03",
            "theft",
            "high",
            "My car was stolen in Boston; a passenger was injured.",
            injury_reported=True,
        ),
        _gold(
            "DEV-04",
            "third_party_liability",
            "medium",
            "My car damaged a shop gate in Boston.",
            location="Boston",
        ),
        _gold("DEV-05", "total_loss", "high", "The insurer declared a total loss."),
        _gold("DEV-06", "glass_only", "medium", "A stone cracked the windscreen."),
        _gold("DEV-07", "not_a_claim", "low", "Please send a copy of my policy."),
        _gold("DEV-08", "own_damage", "medium", "The car needs a repair estimate."),
    ]
    results = [
        _result("DEV-01", _parsed(gold[0]["label"])),
        _result(
            "DEV-02",
            _parsed(gold[1]["label"], claim_type="theft", location="New York"),
        ),
        _result(
            "DEV-03",
            _parsed(gold[2]["label"], urgency="medium", policy_number="MOT-999",
                    injury_reported=False),
        ),
        _result(
            "DEV-04",
            None,
            parse_ok=False,
            schema_ok=False,
        ),
        _result("DEV-05", _parsed(gold[4]["label"])),
        _result("DEV-06", _parsed(gold[5]["label"])),
        _result("DEV-07", _parsed(gold[6]["label"])),
        _result("DEV-08", _parsed(gold[7]["label"])),
    ]
    return gold, results


def test_metrics_on_eight_hand_scored_records():
    gold, predictions = _toy_data()
    metrics = score_predictions(
        gold,
        predictions,
        [1],
        input_price_per_m=2,
        output_price_per_m=4,
    )

    # Six of eight claim types match; the DEV-02 label is wrong and DEV-04 failed.
    assert metrics["claim_type"]["accuracy"] == pytest.approx(6 / 8)
    # Claim F1s are own_damage 4/5, third_party_liability 0, theft 2/3,
    # and 1 each for total_loss, glass_only, not_a_claim: (4/5+0+2/3+3)/6=67/90.
    assert metrics["claim_type"]["macro_f1"] == pytest.approx(67 / 90)
    # The matrix uses the six fixed claim labels followed by the invalid-output column.
    assert metrics["claim_type"]["confusion_matrix"] == [
        [2, 0, 1, 0, 0, 0, 0],
        [0, 0, 0, 0, 0, 0, 1],
        [0, 0, 1, 0, 0, 0, 0],
        [0, 0, 0, 1, 0, 0, 0],
        [0, 0, 0, 0, 1, 0, 0],
        [0, 0, 0, 0, 0, 1, 0],
        [0, 0, 0, 0, 0, 0, 0],
    ]
    # Urgency has six correct outputs; high F1=2/3, medium F1=4/5,
    # low F1=1, so macro-F1=(2/3+4/5+1)/3=37/45.
    assert metrics["urgency"]["accuracy"] == pytest.approx(6 / 8)
    assert metrics["urgency"]["macro_f1"] == pytest.approx(37 / 45)
    # The bad policy value and invalid output miss two of eight policy fields.
    assert metrics["fields"]["policy_number"]["exact_match"] == pytest.approx(6 / 8)
    # Policy F1s are null 5/6, MOT-123 1, and hallucinated MOT-999 0:
    # (5/6+1+0)/3=11/18.
    assert metrics["fields"]["policy_number"]["macro_f1"] == pytest.approx(11 / 18)
    # New York is a strict mismatch to New York City, but passes containment;
    # Boston is wrong because its response is unparseable: strict 6/8, lenient 7/8.
    assert metrics["fields"]["location"]["exact_match"] == pytest.approx(6 / 8)
    assert metrics["fields"]["location"]["lenient_match"] == pytest.approx(7 / 8)
    # Only DEV-03 hallucinates a policy number; overall denominator is 8*7 fields.
    assert metrics["hallucination"]["per_field"]["policy_number"]["count"] == 1
    assert metrics["hallucination"]["per_field"]["policy_number"]["rate"] == pytest.approx(1 / 8)
    assert metrics["hallucination"]["overall"]["rate"] == pytest.approx(1 / 56)
    # Parse/schema rates are 7/8 because DEV-04 is unparseable and invalid.
    assert metrics["parse_ok_rate"] == pytest.approx(7 / 8)
    assert metrics["schema_ok_rate"] == pytest.approx(7 / 8)
    # One of two gold high-urgency records is missed; the single reported injury is missed.
    assert metrics["safety_critical"]["high_urgency_recall"] == pytest.approx(1 / 2)
    assert metrics["safety_critical"]["high_urgency_false_negatives"] == 1
    assert metrics["safety_critical"]["injury_reported_true_recall"] == 0
    assert metrics["safety_critical"]["injury_reported_true_false_negatives"] == 1
    # Token means are 45 input, 9 output, 9/8 reasoning, and 0.45 seconds.
    assert metrics["usage"]["mean_input_tokens"] == pytest.approx(45)
    assert metrics["usage"]["mean_output_tokens"] == pytest.approx(9)
    assert metrics["usage"]["mean_reasoning_tokens"] == pytest.approx(9 / 8)
    assert metrics["usage"]["mean_latency_seconds"] == pytest.approx(0.45)
    # At one run, 1,000*(45*2 + 9*4)/1,000,000 = 0.126 price units.
    assert metrics["usage"]["cost_per_1000_claims"] == pytest.approx(0.126)
    # A single run has no run-to-run agreement score.
    assert metrics["run_to_run_agreement"] == {
        "parsed_json": None,
        "claim_type": None,
    }


def test_run_agreement_counts_identical_json_and_claim_type_separately():
    gold = [
        _gold("DEV-01", "own_damage", "medium", "First record."),
        _gold("DEV-02", "theft", "medium", "Second record."),
    ]
    predictions = [
        _result("DEV-01", _parsed(gold[0]["label"]), run=1),
        _result("DEV-01", _parsed(gold[0]["label"]), run=2),
        _result("DEV-02", _parsed(gold[1]["label"]), run=1),
        _result("DEV-02", _parsed(gold[1]["label"], location="Boston"), run=2),
    ]

    metrics = score_predictions(gold, predictions, [1, 2])

    # One of two records has identical JSON; both retain the same claim type.
    assert metrics["run_to_run_agreement"]["parsed_json"] == pytest.approx(1 / 2)
    assert metrics["run_to_run_agreement"]["claim_type"] == pytest.approx(2 / 2)


def test_missing_prediction_is_counted_as_wrong():
    gold = [_gold("DEV-01", "own_damage", "medium", "Car damage.")]

    metrics = score_predictions(gold, [], [1])

    # The one expected key is missing, so parse/schema rates and accuracy are all 0/1.
    assert metrics["missing_predictions"] == 1
    assert metrics["parse_ok_rate"] == 0
    assert metrics["schema_ok_rate"] == 0
    assert metrics["claim_type"]["accuracy"] == 0


def test_error_marked_parse_valid_prediction_is_counted_as_invalid():
    gold = [_gold("DEV-01", "own_damage", "medium", "Car damage.")]
    prediction = _result(
        "DEV-01",
        _parsed(gold[0]["label"]),
        error="synthetic validation failure",
    )

    metrics = score_predictions(gold, [prediction], [1])

    assert metrics["claim_type"]["accuracy"] == 0
    assert metrics["claim_type"]["confusion_matrix"][0][-1] == 1


def test_location_token_overlap_uses_jaccard_threshold():
    # Two shared tokens out of three is 2/3, above the documented 0.5 threshold.
    assert lenient_location_match("North Main Street", "North Street") is True


def test_identifier_is_hallucinated_when_normalized_value_is_not_in_message():
    gold = [
        _gold(
            "DEV-01",
            "own_damage",
            "medium",
            "Policy MOT-123 covers the car.",
            policy_number="MOT-123",
        )
    ]
    prediction = _result(
        "DEV-01",
        _parsed(gold[0]["label"], policy_number="MOT-999"),
    )

    metrics = score_predictions(gold, [prediction], [1])

    # Gold and prediction are both non-null, but MOT999 is absent from the message.
    assert metrics["hallucination"]["per_field"]["policy_number"]["count"] == 1
    assert metrics["hallucination"]["overall"]["rate"] == pytest.approx(1 / 7)

