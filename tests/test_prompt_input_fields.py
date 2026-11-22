import json
import re
from pathlib import Path

from src.experiment_runtime import load_records
from src.prompt_helpers import load_prompts, render_prompt
from src.validate_dataset import LABEL_FIELDS


def test_model_input_uses_only_original_message_and_few_shot_labels(tmp_path, monkeypatch):
    message = "Synthetic message marker: windshield cracked."
    record = {
        "original_message": message,
        "label": {
            "claim_type": "glass_only",
            "urgency": "medium",
            "policy_number": None,
            "incident_date": None,
            "location": None,
            "vehicle_reg_no": None,
            "estimated_amount_inr": None,
            "injury_reported": None,
            "police_report_filed": None,
        },
        "tags": ["private-tag-marker"],
        "note": "private-note-marker",
    }
    (tmp_path / "dev.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("src.experiment_runtime.config.DATA_DIR", tmp_path)

    loaded = load_records("dev")

    assert loaded == [{
        "split": "dev",
        "record_id": "DEV-01",
        "message": message,
    }]
    for prompt_id, expected_examples in (("B-k3", 3), ("B-k5", 5), ("B-k8", 8)):
        template = load_prompts([prompt_id])[prompt_id]
        prompt = render_prompt(template, loaded[0]["message"])
        assert message in prompt
        assert "private-tag-marker" not in prompt
        assert "private-note-marker" not in prompt

        examples = prompt.split("## Worked examples\n", 1)[1].split(
            "## Required output", 1
        )[0]
        example_labels = re.findall(r"```json\s*(.*?)\s*```", examples, re.DOTALL)
        assert len(example_labels) == expected_examples
        assert examples.count("**Message**") == expected_examples
        assert examples.count("**Correct JSON**") == expected_examples
        assert all(
            set(json.loads(label)) == LABEL_FIELDS for label in example_labels
        )
