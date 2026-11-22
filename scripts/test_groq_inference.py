"""Run one technical Groq inference smoke test using Prompt A."""

import time

from groq import APIConnectionError, APIStatusError
from groq import AuthenticationError

from src import config
from src.groq_client import create_groq_client
from src.response_validation import REQUIRED_KEYS, validate_response

SYNTHETIC_MESSAGE = (
    "Hello, my car MH02AB1234 was hit by another vehicle on 11 November 2026 "
    "near Kalyan station. The other driver damaged my front bumper. No one "
    "was injured. My policy number is POL-TEST-001. A police complaint has "
    "been filed. The estimated repair cost is INR 18500."
)
PROMPT_PATH = config.PROMPTS_DIR / "prompt_a_zero_shot.md"


def load_prompt() -> str:
    """Read Prompt A and substitute the synthetic claim message."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    if prompt.count("{{CLAIM_MESSAGE}}") != 1:
        raise ValueError("Prompt A must contain exactly one claim-message placeholder.")
    return prompt.replace("{{CLAIM_MESSAGE}}", SYNTHETIC_MESSAGE)


def _report_request_error(error: Exception) -> str:
    """Return a useful API/network error without echoing exception contents."""
    if isinstance(error, AuthenticationError):
        return "Groq authentication failed; check GROQ_API_KEY in the project-root .env."
    if isinstance(error, APIConnectionError):
        return "Network error while contacting Groq; check connectivity and try again."
    if isinstance(error, APIStatusError):
        return f"Groq API request failed with HTTP {error.status_code}."
    return f"Groq request failed ({type(error).__name__})."


def main() -> int:
    """Send exactly one completion request and report its raw response and checks."""
    try:
        client = create_groq_client(max_retries=0)
    except RuntimeError:
        print("Client initialization failed: GROQ_API_KEY is unavailable.")
        return 1
    except (TypeError, ValueError) as error:
        print(f"Client initialization failed ({type(error).__name__}).")
        return 1

    print("Client initialization: successful.")
    print(f"Model: {config.MODEL_NAME}")

    try:
        prompt = load_prompt()
    except (OSError, ValueError) as error:
        print(f"Prompt configuration error ({type(error).__name__}): {error}")
        return 1

    started_at = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=config.MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=config.MAX_TOKENS,
        )
    except (APIConnectionError, APIStatusError) as error:
        latency_seconds = time.perf_counter() - started_at
        print("Request succeeded: no.")
        print(f"Latency: {latency_seconds:.3f} seconds")
        print(_report_request_error(error))
        return 1
    latency_seconds = time.perf_counter() - started_at

    if not response.choices or response.choices[0].message.content is None:
        print("Request succeeded: yes.")
        print("JSON parsing succeeded: no.")
        print("Validation passed: no.")
        print("Response content: unavailable (the API returned no message content).")
        print(f"Latency: {latency_seconds:.3f} seconds")
        print("Input tokens: unavailable")
        print("Output tokens: unavailable")
        return 1

    response_text = response.choices[0].message.content
    parsed, issues = validate_response(response_text)
    print("Request succeeded: yes.")
    json_parsing_succeeded = not (
        issues and issues[0].startswith("Malformed JSON")
    )
    print(f"JSON parsing succeeded: {'yes' if json_parsing_succeeded else 'no'}.")
    print(f"All nine-field validations passed: {'yes' if not issues else 'no'}.")
    print("Response content:")
    print(response_text)
    for issue in issues:
        print(f"Validation issue: {issue}")
    print(f"Latency: {latency_seconds:.3f} seconds")
    usage = response.usage
    print(f"Input tokens: {usage.prompt_tokens if usage else 'unavailable'}")
    print(f"Output tokens: {usage.completion_tokens if usage else 'unavailable'}")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
