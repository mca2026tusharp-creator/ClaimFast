"""Automated checks for the ClaimFast dataset validation logic."""

import json

import pytest

from src.validate_dataset import (
    LABEL_FIELDS,
    validate_datasets,
    validate_record,
)


def _record(
    message="General enquiry, no incident.",
    tags=None,
    note=None,
    **label_changes,
):
    label = {
        "claim_type": "not_a_claim",
        "urgency": "low",
        "policy_number": None,
        "incident_date": None,
        "location": None,
        "vehicle_reg_no": None,
        "estimated_amount_inr": None,
        "injury_reported": None,
        "police_report_filed": None,
    }
    label.update(label_changes)
    assert set(label) == LABEL_FIELDS
    record = {"original_message": message, "label": label, "tags": tags or []}
    if note is not None:
        record["note"] = note
    return record


def test_split_validation_counts_and_distributions(tmp_path):
    dev_path = tmp_path / "dev.jsonl"
    test_path = tmp_path / "test.jsonl"
    dev_path.write_text(
        "\n".join(
            json.dumps(_record(f"Development enquiry {index}, no incident."))
            for index in range(20)
        ) + "\n",
        encoding="utf-8",
    )
    test_path.write_text(
        "\n".join(
            json.dumps(_record(f"Test enquiry {index}, no incident."))
            for index in range(60)
        ) + "\n",
        encoding="utf-8",
    )

    distributions, errors = validate_datasets(dev_path, test_path)

    assert errors == []
    assert sum(distributions["dev"][0].values()) == 20
    assert sum(distributions["dev"][1].values()) == 20
    assert sum(distributions["test"][0].values()) == 60
    assert sum(distributions["test"][1].values()) == 60
    assert distributions["dev"][0] == {"not_a_claim": 20}
    assert distributions["test"][0] == {"not_a_claim": 60}
    assert distributions["dev"][1] == {"low": 20}
    assert distributions["test"][1] == {"low": 60}


def test_valid_extractions_must_be_supported_by_message():
    record = _record(
        "On 2026-03-02 at Central Park, car XX 01 AB 1234 had damage. "
        "Policy POL-001. Estimate ₹12000. No one was hurt; no police report filed.",
        claim_type="own_damage",
        urgency="medium",
        policy_number="POL-001",
        incident_date="2026-03-02",
        location="Central Park",
        vehicle_reg_no="XX01AB1234",
        estimated_amount_inr=12000,
        injury_reported=False,
        police_report_filed=False,
    )

    assert validate_record(record, "test") == []


def test_validator_rejects_invalid_date_registration_boolean_and_unsupported_value():
    record = _record(
        "On 2026-03-02, a car was damaged.",
        claim_type="own_damage",
        urgency="medium",
        policy_number="POL-NOT-IN-MESSAGE",
        incident_date="2026-02-30",
        location="Mars",
        vehicle_reg_no="xx 01 ab 1234",
        estimated_amount_inr=True,
        injury_reported="false",
        police_report_filed=None,
    )

    errors = validate_record(record, "test")
    assert any("policy_number is not present" in error for error in errors)
    assert any("valid ISO" in error for error in errors)
    assert any("location is not present" in error for error in errors)
    assert any("uppercase and contain no spaces or hyphens" in error for error in errors)
    assert any("estimated_amount_inr must be" in error for error in errors)
    assert any("injury_reported must be true, false, or null" in error for error in errors)


def test_validator_rejects_missing_and_extra_label_fields():
    record = _record()
    del record["label"]["location"]
    record["label"]["unexpected"] = "value"

    errors = validate_record(record, "test")

    assert any("label fields must be exactly" in error for error in errors)


def test_validator_requires_all_nine_label_fields_including_injury_reported():
    record = _record()
    assert len(record["label"]) == 9
    del record["label"]["injury_reported"]

    errors = validate_record(record, "test")

    assert any("label fields must be exactly" in error for error in errors)


def test_record_accepts_tags_and_optional_single_line_note():
    record = _record(
        "General enquiry, no incident.",
        tags=["hinglish", "typos/abbreviations"],
        note="The sender reports a possible theft attempt without confirmation.",
    )

    assert validate_record(record, "test") == []

    record["note"] = "First line\nSecond line"
    errors = validate_record(record, "test")
    assert any("single-line string" in error for error in errors)


def test_record_requires_string_tags():
    record = _record(tags=["hinglish", 7])

    errors = validate_record(record, "test")

    assert any("tags must be a list of non-empty strings" in error for error in errors)


@pytest.mark.parametrize(
    "message",
    [
        "My car was hit on 21 Oct; please open a claim.",
        "My car was hit on 21/10; please open a claim.",
    ],
)
def test_non_iso_day_month_must_match_normalized_date(message):
    record = _record(
        message,
        claim_type="own_damage",
        urgency="medium",
        incident_date="2026-10-21",
    )

    assert validate_record(record, "test") == []


def test_non_iso_day_month_mismatch_is_rejected():
    record = _record(
        "My car was hit on 21 Oct; please open a claim.",
        claim_type="own_damage",
        urgency="medium",
        incident_date="2026-10-22",
    )

    errors = validate_record(record, "test")

    assert any("does not match an explicit date" in error for error in errors)


def test_ordinal_day_month_date_matches_normalized_date():
    record = _record(
        "My car was hit on 2nd August; please open a claim.",
        claim_type="own_damage",
        urgency="medium",
        incident_date="2026-08-02",
    )

    assert validate_record(record, "test") == []


def test_relative_date_with_null_incident_date_is_valid():
    record = _record(
        "Kal raat someone hit my parked car; please open a claim.",
        claim_type="own_damage",
        urgency="medium",
        incident_date=None,
    )

    assert validate_record(record, "test") == []


def test_relative_date_cannot_support_non_null_incident_date():
    record = _record(
        "Kal raat someone hit my parked car; please open a claim.",
        claim_type="own_damage",
        urgency="medium",
        incident_date="2026-10-21",
    )

    errors = validate_record(record, "test")

    assert any("does not match an explicit date" in error for error in errors)


def test_validator_enforces_high_urgency_for_reported_injury():
    record = _record(
        "A passenger was injured in the crash.",
        claim_type="own_damage",
        urgency="medium",
        injury_reported=True,
    )

    errors = validate_record(record, "test")

    assert any("requires high urgency" in error for error in errors)


def test_natural_hinglish_unroadworthy_phrase_supports_high_urgency():
    record = _record(
        "Meri gaadi chalne layak nahi hai; tow karwana pada.",
        claim_type="own_damage",
        urgency="high",
    )

    assert validate_record(record, "test") == []


@pytest.mark.parametrize(
    "message",
    [
        "Brakes poori tarah fail ho gaye, gaadi ab chal nahi sakti. Nobody was hurt.",
        "Wheel lock ho gaya, gaadi hil nahi rahi. Nobody was hurt.",
        "Highway par van atak gayi, chalana khatarnak hai. Nobody was hurt.",
    ],
)
def test_natural_unsafe_vehicle_cues_support_high_urgency(message):
    record = _record(
        message,
        claim_type="own_damage",
        urgency="high",
        injury_reported=False,
    )

    assert validate_record(record, "test") == []


@pytest.mark.parametrize(
    "written_registration",
    [
        "XX01AB1234",
        "XX-01-AB-1234",
        "xx 01 ab 1234",
        "XX01 AB1234",
    ],
)
def test_registration_formats_normalize_to_uppercase_alphanumeric_value(
    written_registration,
):
    record = _record(
        f"My car {written_registration} was damaged in a collision.",
        claim_type="own_damage",
        urgency="medium",
        vehicle_reg_no="XX01AB1234",
    )

    assert validate_record(record, "test") == []


def test_normalized_registration_label_cannot_contain_hyphens():
    record = _record(
        "My car XX-01-AB-1234 was damaged.",
        claim_type="own_damage",
        urgency="medium",
        vehicle_reg_no="XX-01-AB-1234",
    )

    errors = validate_record(record, "test")

    assert any("contain no spaces or hyphens" in error for error in errors)


@pytest.mark.parametrize(
    "evidence",
    [
        "The passenger was taken to hospital.",
        "The passenger went to a clinic.",
        "Stitches were needed for the cut.",
        "A cast was put on the wrist.",
        "The passenger was bleeding.",
        "The report confirmed a fracture.",
        "An ambulance took the passenger from the scene.",
    ],
)
def test_medical_evidence_supports_reported_injury(evidence):
    record = _record(
        evidence,
        claim_type="own_damage",
        urgency="high",
        injury_reported=True,
    )

    assert validate_record(record, "test") == []


@pytest.mark.parametrize(
    ("message", "amount"),
    [
        ("The repair estimate is 18k.", 18000),
        ("The repair estimate is 1.2 lakh.", 120000),
        ("The repair estimate is Rs 12,500.", 12500),
    ],
)
def test_explicit_shorthand_amounts_normalize_to_integer_rupees(message, amount):
    record = _record(
        message,
        claim_type="own_damage",
        urgency="medium",
        estimated_amount_inr=amount,
    )

    assert validate_record(record, "test") == []


@pytest.mark.parametrize(
    ("message", "amount"),
    [
        ("The repair estimate is approx 45000.", 45000),
        ("The repair estimate is around 45k.", 45000),
    ],
)
def test_single_approximate_amount_is_extracted(message, amount):
    record = _record(
        message,
        claim_type="own_damage",
        urgency="medium",
        estimated_amount_inr=amount,
    )

    assert validate_record(record, "test") == []


def test_validator_reports_non_string_labels_without_crashing():
    record = _record(claim_type=["theft"], urgency=[])

    errors = validate_record(record, "test")

    assert any("invalid claim_type" in error for error in errors)
    assert any("invalid urgency" in error for error in errors)


def test_jsonl_loader_reports_invalid_json_and_wrong_record_count(tmp_path):
    dev_path = tmp_path / "dev.jsonl"
    test_path = tmp_path / "test.jsonl"
    dev_path.write_text('{"original_message":\n', encoding="utf-8")
    test_path.write_text(
        json.dumps(_record()) + "\n",
        encoding="utf-8",
    )

    _, errors = validate_datasets(dev_path, test_path)

    assert any("invalid JSON" in error for error in errors)
    assert any("expected 20 lines, found 1" in error for error in errors)
    assert any("expected 60 lines, found 1" in error for error in errors)


def test_validator_detects_duplicate_messages_across_splits(tmp_path):
    dev_path = tmp_path / "dev.jsonl"
    test_path = tmp_path / "test.jsonl"
    dev_records = [
        _record(f"Development enquiry {index}, no incident.")
        for index in range(20)
    ]
    test_records = [
        _record("Development enquiry 0, no incident.")
    ] + [
        _record(f"Test enquiry {index}, no incident.")
        for index in range(59)
    ]
    dev_path.write_text(
        "\n".join(json.dumps(record) for record in dev_records) + "\n",
        encoding="utf-8",
    )
    test_path.write_text(
        "\n".join(json.dumps(record) for record in test_records) + "\n",
        encoding="utf-8",
    )

    _, errors = validate_datasets(dev_path, test_path)

    assert any("duplicate message" in error for error in errors)
