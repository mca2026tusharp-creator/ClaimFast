"""Offline tests for the one-request Groq smoke-test response validator."""

import json
from types import SimpleNamespace

import pytest

import scripts.test_groq_inference as inference_test
from src import config
from src.response_validation import REQUIRED_KEYS, validate_response


def _valid_response():
    return {
        "claim_type": "own_damage",
        "urgency": "medium",
        "policy_number": "POL-TEST-001",
        "incident_date": "2026-11-11",
        "location": "Kalyan station",
        "vehicle_reg_no": "MH02AB1234",
        "estimated_amount_inr": 18500,
        "injury_reported": False,
        "police_report_filed": True,
    }


def test_valid_response_has_exact_required_keys_and_types():
    parsed, issues = validate_response(json.dumps(_valid_response()))

    assert set(parsed) == REQUIRED_KEYS
    assert issues == []


@pytest.mark.parametrize(
    ("change", "expected_issue"),
    [
        (lambda value: value.pop("location"), "Missing required keys"),
        (lambda value: value.update(extra="value"), "Unexpected keys"),
        (lambda value: value.update(claim_type="unknown"), "claim_type"),
        (lambda value: value.update(urgency="urgent"), "urgency"),
        (lambda value: value.update(policy_number=12), "policy_number"),
        (lambda value: value.update(incident_date="2026-02-30"), "incident_date"),
        (lambda value: value.update(estimated_amount_inr=True), "estimated_amount_inr"),
        (lambda value: value.update(estimated_amount_inr=-1), "non-negative integer"),
        (lambda value: value.update(injury_reported=1), "injury_reported"),
        (lambda value: value.update(police_report_filed="true"), "police_report_filed"),
    ],
)
def test_invalid_fields_are_reported(change, expected_issue):
    response = _valid_response()
    change(response)

    _, issues = validate_response(json.dumps(response))

    assert any(expected_issue in issue for issue in issues)


def test_malformed_json_is_rejected_without_repair():
    parsed, issues = validate_response('{"claim_type": ')

    assert parsed is None
    assert len(issues) == 1
    assert "Malformed JSON" in issues[0]


def test_non_object_json_is_rejected():
    parsed, issues = validate_response("[]")

    assert parsed == []
    assert issues == ["Top-level JSON value must be an object."]


def test_main_makes_exactly_one_mocked_completion_request(monkeypatch, capsys):
    calls = []
    client_options = []
    content = json.dumps(_valid_response())
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content))
        ],
        usage=SimpleNamespace(prompt_tokens=123, completion_tokens=45),
    )

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return response

    client = FakeClient()

    def create_client(**kwargs):
        client_options.append(kwargs)
        return client

    monkeypatch.setattr(inference_test, "create_groq_client", create_client)

    assert inference_test.main() == 0

    assert client_options == [{"max_retries": 0}]
    assert len(calls) == 1
    assert calls[0]["model"] == config.MODEL_NAME
    assert calls[0]["temperature"] == 0
    assert "{{CLAIM_MESSAGE}}" not in calls[0]["messages"][0]["content"]
    assert inference_test.SYNTHETIC_MESSAGE in calls[0]["messages"][0]["content"]
    output = capsys.readouterr().out
    assert "All nine-field validations passed: yes." in output
    assert "Input tokens: 123" in output
    assert "Output tokens: 45" in output
