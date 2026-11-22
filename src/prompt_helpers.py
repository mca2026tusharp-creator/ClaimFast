"""Small shared helpers for rendering prompts and validating model output."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src import config
from src.response_validation import validate_response

PROMPT_FILES = {
    "A": "prompt_a_zero_shot.md",
    "B-k3": "prompt_b_few_shot_k3.md",
    "B-k5": "prompt_b_few_shot_k5.md",
    "B-k8": "prompt_b_few_shot_k8.md",
    "C": "prompt_c_reasoning.md",
}
FINAL_JSON_PATTERN = re.compile(
    r"<final_json>(.*?)</final_json>",
    flags=re.DOTALL,
)


def load_prompts(prompt_ids: list[str] | None = None) -> dict[str, str]:
    selected = prompt_ids or list(PROMPT_FILES)
    prompts = {}
    for prompt_id in selected:
        if prompt_id not in PROMPT_FILES:
            raise ValueError(f"Unknown prompt ID: {prompt_id}")
        text = (config.PROMPTS_DIR / PROMPT_FILES[prompt_id]).read_text(
            encoding="utf-8"
        )
        if text.count("{{CLAIM_MESSAGE}}") != 1:
            raise ValueError(f"{PROMPT_FILES[prompt_id]} needs one claim placeholder.")
        prompts[prompt_id] = text
    return prompts


def render_prompt(template: str, message: str) -> str:
    if template.count("{{CLAIM_MESSAGE}}") != 1:
        raise ValueError("A prompt must contain exactly one claim placeholder.")
    return template.replace("{{CLAIM_MESSAGE}}", message)


def validate_model_output(
    prompt_id: str,
    raw_output: str | None,
) -> tuple[bool, bool, Any | None, list[str]]:
    if raw_output is None:
        return False, False, None, ["No response content was returned."]

    json_text = raw_output
    if prompt_id == "C":
        blocks = FINAL_JSON_PATTERN.findall(raw_output)
        if (
            raw_output.count("<final_json>") != 1
            or raw_output.count("</final_json>") != 1
            or len(blocks) != 1
        ):
            return False, False, None, [
                "Prompt C response must contain exactly one <final_json> block."
            ]
        json_text = blocks[0].strip()

    try:
        json.loads(json_text)
    except json.JSONDecodeError as error:
        return False, False, None, [
            f"Malformed JSON at line {error.lineno}, column {error.colno}."
        ]
    parsed, issues = validate_response(json_text)
    return True, not issues, parsed, issues


def build_results_meta(plan: dict[str, Any] | None = None) -> dict[str, Any]:
    prompt_hashes = {
        prompt_id: hashlib.sha256(
            (config.PROMPTS_DIR / filename).read_bytes()
        ).hexdigest()
        for prompt_id, filename in PROMPT_FILES.items()
    }
    meta = {
        "model": config.MODEL_NAME,
        "temperature": config.TEMPERATURE,
        "max_tokens": config.MAX_TOKENS,
        "reasoning_effort": config.REASONING_EFFORT,
        "reasoning_format": config.REASONING_FORMAT,
        "prompt_sha256": prompt_hashes,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    from src.experiment_plan import (
        ExperimentPlanError,
        load_development_plan,
        plan_result_metadata,
    )

    if plan is not None:
        meta.update(plan_result_metadata(plan))
        return meta
    try:
        meta.update(plan_result_metadata(load_development_plan()))
    except (OSError, ValueError, ExperimentPlanError):
        return meta
    return meta
