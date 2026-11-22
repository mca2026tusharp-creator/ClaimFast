"""Project settings and paths for ClaimFast."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Resolve all project folders relative to this file, not the current directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
REPORT_DIR = PROJECT_ROOT / "report"
SRC_DIR = PROJECT_ROOT / "src"
TESTS_DIR = PROJECT_ROOT / "tests"
PLAN_DIR = PROJECT_ROOT / "plans"
ENV_FILE = PROJECT_ROOT / ".env"
DEVELOPMENT_PLAN_PATH = PLAN_DIR / "authoritative_experiment_plan.json"
DEVELOPMENT_PLAN_EXPECTED_DIGEST = (
    "f9639d694d8847dc16dc7583eee4d404c744580d51f639fa2cdd152c23915604"
)
DEVELOPMENT_RUN_DIR = OUTPUTS_DIR / "development_runs" / (
    f"dev-{DEVELOPMENT_PLAN_EXPECTED_DIGEST[:12]}"
)
DEVELOPMENT_RESULTS_PATH = DEVELOPMENT_RUN_DIR / "results.json"
DEVELOPMENT_RETRY_LOG_PATH = DEVELOPMENT_RUN_DIR / "retry_log.jsonl"
HELDOUT_PLAN_PATH = PLAN_DIR / "heldout_test_plan_v1.json"
HELDOUT_PLAN_EXPECTED_DIGEST = (
    "16121fec106ce4b819f3093bedaba2d00eb83153cb71964759e6252864319b21"
)
HELDOUT_RUN_DIR = OUTPUTS_DIR / "heldout_runs" / (
    f"heldout-test-{HELDOUT_PLAN_EXPECTED_DIGEST[:12]}"
)
HELDOUT_RESULTS_PATH = HELDOUT_RUN_DIR / "results.json"
HELDOUT_RETRY_LOG_PATH = HELDOUT_RUN_DIR / "retry_log.jsonl"

# Candidate model selected for the case study.
MODEL_NAME = "openai/gpt-oss-120b"

# Generation settings.
TEMPERATURE = 0
MAX_TOKENS = 800
REASONING_EFFORT = "low"
REASONING_FORMAT = "hidden"
INPUT_PRICE_PER_M = None  # fill from Groq pricing page
OUTPUT_PRICE_PER_M = None  # fill from Groq pricing page

# Reproducibility and request pacing settings.
RANDOM_SEED = 42
RUNS_PER_PROMPT = 1  # Pilot default; pass --runs to override.
REQUEST_DELAY_SECONDS = 2
DAILY_TOKEN_LIMIT = 280_000  # From Groq console: openai/gpt-oss-120b tokens/day limit.


def get_groq_api_key() -> str:
    """Load the project-root environment file and return the configured API key."""
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or not api_key.strip():
        raise RuntimeError(
            "GROQ_API_KEY is missing. Add it to the project-root .env "
            "using .env.example as a guide."
        )
    return api_key.strip()
