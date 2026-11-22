"""Check Groq authentication with the models-list endpoint."""

from collections.abc import Callable

from groq import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    Groq,
)

from src import config
from src.groq_client import create_groq_client

Output = Callable[[str], None]


def _safe_api_error_message(error: Exception) -> str:
    if isinstance(error, AuthenticationError):
        return "Authentication failed. Check GROQ_API_KEY in the project-root .env."
    if isinstance(error, APIConnectionError):
        return "Network connection to Groq failed. Check connectivity and try again."
    if isinstance(error, APIStatusError):
        return f"Groq rejected the models-list request (HTTP {error.status_code})."
    return f"Groq request failed ({type(error).__name__})."


def check_groq_connection(
    client: Groq,
    model_name: str,
    output: Output = print,
) -> int:
    """Verify authentication and report configured-model availability."""
    try:
        response = client.models.list()
    except (AuthenticationError, APIConnectionError, APIStatusError) as error:
        output(_safe_api_error_message(error))
        return 1

    model_ids = {model.id for model in response.data}
    output("Groq authentication successful.")

    if model_name and model_name != "FILL_IN_MODEL_NAME":
        if model_name in model_ids:
            output(f"Configured model is available: {model_name}")
        else:
            output(f"Configured model is not listed as available: {model_name}")
    else:
        output("No model is configured; model availability was not checked.")
    return 0


def main(output: Output = print) -> int:
    """Load credentials, initialize the client, and check authentication."""
    try:
        client = create_groq_client()
    except RuntimeError:
        output(
            "Configuration error: GROQ_API_KEY is unavailable. "
            "Add it to the project-root .env using .env.example as a guide."
        )
        return 1
    except (TypeError, ValueError) as error:
        output(f"Groq client configuration is invalid ({type(error).__name__}).")
        return 1

    return check_groq_connection(client, config.MODEL_NAME, output)


if __name__ == "__main__":
    raise SystemExit(main())
