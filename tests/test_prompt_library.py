"""Offline validation of ClaimFast prompts against the development split only."""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PROJECT_ROOT / "prompts"
DEV_PATH = PROJECT_ROOT / "data" / "dev.jsonl"

PROMPT_FILES = {
    "a": PROMPTS_DIR / "prompt_a_zero_shot.md",
    "k3": PROMPTS_DIR / "prompt_b_few_shot_k3.md",
    "k5": PROMPTS_DIR / "prompt_b_few_shot_k5.md",
    "k8": PROMPTS_DIR / "prompt_b_few_shot_k8.md",
    "c": PROMPTS_DIR / "prompt_c_reasoning.md",
}
EXPECTED_FIELDS = {
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
EXPECTED_CLAIM_TYPES = {
    "own_damage",
    "third_party_liability",
    "theft",
    "total_loss",
    "glass_only",
    "not_a_claim",
}
EXAMPLE_PATTERN = re.compile(
    r"### Example (DEV-\d+)\n\n\*\*Message\*\*\n\n> (.*?)"
    r"\n\n\*\*Correct JSON\*\*\n\n```json\n(.*?)\n```",
    re.DOTALL,
)


def _dev_records():
    with DEV_PATH.open(encoding="utf-8") as dev_file:
        return [json.loads(line) for line in dev_file if line.strip()]


def _examples(prompt_text):
    examples = []
    for match in EXAMPLE_PATTERN.finditer(prompt_text):
        examples.append(
            (match.group(1), match.group(2), json.loads(match.group(3)))
        )
    return examples


def _output_schema(prompt_text):
    blocks = re.findall(r"```json\n(.*?)\n```", prompt_text, re.DOTALL)
    assert blocks, "prompt must include a JSON output schema"
    return json.loads(blocks[-1])


def test_all_prompt_files_exist_and_share_required_schema():
    assert all(path.is_file() for path in PROMPT_FILES.values())
    assert (PROMPTS_DIR / "README.md").is_file()

    schemas = {
        key: _output_schema(path.read_text(encoding="utf-8"))
        for key, path in PROMPT_FILES.items()
    }
    assert all(schema["type"] == "object" for schema in schemas.values())
    assert all(schema["additionalProperties"] is False for schema in schemas.values())
    assert all(set(schema["properties"]) == EXPECTED_FIELDS for schema in schemas.values())
    assert all(set(schema["required"]) == EXPECTED_FIELDS for schema in schemas.values())
    assert all(schema == schemas["a"] for schema in schemas.values())
    for schema in schemas.values():
        properties = schema["properties"]
        assert set(properties["claim_type"]["enum"]) == EXPECTED_CLAIM_TYPES
        assert properties["urgency"]["enum"] == ["high", "medium", "low"]
        assert properties["policy_number"]["type"] == ["string", "null"]
        assert properties["incident_date"]["type"] == ["string", "null"]
        assert properties["location"]["type"] == ["string", "null"]
        assert properties["vehicle_reg_no"]["type"] == ["string", "null"]
        assert properties["estimated_amount_inr"]["type"] == ["integer", "null"]
        assert properties["injury_reported"]["type"] == ["boolean", "null"]
        assert properties["police_report_filed"]["type"] == ["boolean", "null"]
        assert "default" not in repr(schema).lower()

    for path in PROMPT_FILES.values():
        text = path.read_text(encoding="utf-8")
        normalized_text = " ".join(text.split())
        for claim_type in EXPECTED_CLAIM_TYPES:
            assert f"`{claim_type}`" in text
        assert "{{CLAIM_MESSAGE}}" in text
        assert "untrusted" in text.lower()
        assert (
            "The schema illustrates required keys and value types only. "
            "Placeholder values are not defaults. Determine claim type and "
            "urgency from the actual message."
        ) in normalized_text
        assert (
            "The JSON Schema below describes the required output structure. "
            "Do not return the schema itself. Return one JSON instance that "
            "conforms to the schema, populated using only information "
            "supported by the claim message. The output must contain exactly "
            "the nine required fields and no additional fields."
        ) in normalized_text


def test_task_prompts_and_guide_share_approved_label_policies():
    injury_rule = (
        "For `third_party_liability`, an injured person means someone other "
        "than the policyholder/claimant. Injury to the policyholder alone "
        "does not establish `third_party_liability`. Classify the claim type "
        "using the primary reported claim evidence and extract injury "
        "separately. Any reported injury still makes urgency `high`."
    )
    multi_incident_rule = (
        "When the latest sender explicitly identifies which incident is the "
        "current/main claim, classify that incident. Do not select an "
        "incident merely because it is chronologically newer. If multiple "
        "distinct incidents are mentioned and no primary incident is clear, "
        "do not invent a precedence rule. Such cases require human "
        "adjudication."
    )

    texts = [
        " ".join(path.read_text(encoding="utf-8").split())
        for path in PROMPT_FILES.values()
    ]
    texts.append(
        " ".join(
            (PROJECT_ROOT / "data" / "labeling_guide.md")
            .read_text(encoding="utf-8")
            .split()
        )
    )
    assert all(injury_rule in text for text in texts)
    assert all(multi_incident_rule in text for text in texts)
    assert all(
        "approved external handling policy before inclusion in scored evaluation"
        in text
        for text in texts
    )


def test_prompt_a_has_no_worked_examples_and_requires_json_only():
    prompt_a = PROMPT_FILES["a"].read_text(encoding="utf-8")

    assert _examples(prompt_a) == []
    assert "Return valid JSON only" in prompt_a


def test_few_shot_counts_are_nested_and_match_dev_ground_truth():
    prompt_examples = {
        key: _examples(PROMPT_FILES[key].read_text(encoding="utf-8"))
        for key in ("k3", "k5", "k8")
    }
    assert [example[0] for example in prompt_examples["k3"]] == [
        "DEV-01",
        "DEV-06",
        "DEV-17",
    ]
    assert [example[0] for example in prompt_examples["k5"]] == [
        "DEV-01",
        "DEV-06",
        "DEV-17",
        "DEV-10",
        "DEV-14",
    ]
    assert [example[0] for example in prompt_examples["k8"]] == [
        "DEV-01",
        "DEV-06",
        "DEV-17",
        "DEV-10",
        "DEV-14",
        "DEV-11",
        "DEV-03",
        "DEV-09",
    ]
    assert [len(prompt_examples[key]) for key in ("k3", "k5", "k8")] == [3, 5, 8]
    assert prompt_examples["k5"][:3] == prompt_examples["k3"]
    assert prompt_examples["k8"][:5] == prompt_examples["k5"]

    dev_by_id = {
        f"DEV-{index:02d}": record
        for index, record in enumerate(_dev_records(), start=1)
    }
    for examples in prompt_examples.values():
        for example_id, message, label in examples:
            assert message == dev_by_id[example_id]["original_message"]
            assert label == dev_by_id[example_id]["label"]
            assert set(label) == EXPECTED_FIELDS


def test_k8_covers_all_claim_types_and_smaller_sets_document_limit():
    examples = {
        key: _examples(PROMPT_FILES[key].read_text(encoding="utf-8"))
        for key in ("k3", "k5", "k8")
    }
    assert {label["claim_type"] for _, _, label in examples["k3"]} == {
        "own_damage",
        "third_party_liability",
        "not_a_claim",
    }
    assert {label["claim_type"] for _, _, label in examples["k5"]} == {
        "own_damage",
        "third_party_liability",
        "theft",
        "total_loss",
        "not_a_claim",
    }
    assert {label["claim_type"] for _, _, label in examples["k8"]} == EXPECTED_CLAIM_TYPES

    readme = " ".join(
        (PROMPTS_DIR / "README.md").read_text(encoding="utf-8").split()
    )
    assert "cannot cover every class" in readme
    assert "k=3 includes `own_damage`, `third_party_liability`, and `not_a_claim`" in readme
    assert "k=5 adds `theft` and `total_loss`" in readme


def test_prompt_c_separates_summary_from_final_json_parser_target():
    prompt_c_raw = PROMPT_FILES["c"].read_text(encoding="utf-8")
    prompt_c = " ".join(prompt_c_raw.split())

    assert "<decision_summary>" in prompt_c
    assert "<final_json>" in prompt_c
    assert "require exactly one" in prompt_c
    assert "parse only its contents" in prompt_c
    assert "reject malformed JSON" in prompt_c
    assert "validate exactly the nine required keys and their" in prompt_c
    assert "reject unexpected additional keys" in prompt_c
    assert "private internal chain-of-thought" in prompt_c
    assert "reasoning" not in _output_schema(prompt_c_raw)["properties"]
    assert (
        "This instruction applies only to the JSON inside "
        "`<final_json>...</final_json>`, not to the decision summary"
    ) in prompt_c
