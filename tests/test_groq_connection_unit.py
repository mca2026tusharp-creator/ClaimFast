"""Offline tests for Groq configuration and connection reporting."""

import sys
from types import SimpleNamespace

import pytest

from src import config
import scripts.test_groq_connection as connection_test
from src.groq_client import create_groq_client


def test_get_groq_api_key_loads_the_configured_environment_file(monkeypatch, tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("GROQ_API_KEY=test-key-value\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    assert bool(config.get_groq_api_key() == "test-key-value")


def test_missing_api_key_has_actionable_error_without_a_value(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda **kwargs: None)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GROQ_API_KEY is missing") as error:
        config.get_groq_api_key()

    assert bool("test-key-value" not in str(error.value))
    assert ".env.example" in str(error.value)


def test_client_initialization_passes_key_to_sdk_without_logging(monkeypatch):
    calls = []
    key_sentinel = object()

    class FakeGroq:
        def __init__(self, *, api_key):
            calls.append(api_key)

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(config, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setitem(
        sys.modules,
        "groq",
        SimpleNamespace(Groq=FakeGroq),
    )
    monkeypatch.setattr(
        "src.groq_client.get_groq_api_key",
        lambda: key_sentinel,
    )

    client = create_groq_client()

    assert isinstance(client, FakeGroq)
    assert calls == [key_sentinel]


def test_connection_uses_models_list_and_reports_model_availability():
    calls = []
    client = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: (
                calls.append("list")
                or SimpleNamespace(data=[SimpleNamespace(id="configured-model")])
            )
        )
    )
    messages = []

    result = connection_test.check_groq_connection(
        client, "configured-model", messages.append
    )

    assert result == 0
    assert calls == ["list"]
    assert messages == [
        "Groq authentication successful.",
        "Configured model is available: configured-model",
    ]


def test_error_reporting_does_not_expose_exception_text_or_secret():
    secret = "test-key-value"
    message = connection_test._safe_api_error_message(RuntimeError(secret))

    assert message == "Groq request failed (RuntimeError)."
    assert bool(secret not in message)


def test_main_reports_missing_key_without_exception_text(monkeypatch):
    secret = "test-key-value"

    def missing_key():
        raise RuntimeError(secret)

    monkeypatch.setattr(connection_test, "create_groq_client", missing_key)
    messages = []

    result = connection_test.main(messages.append)

    assert result == 1
    assert messages == [
        "Configuration error: GROQ_API_KEY is unavailable. "
        "Add it to the project-root .env using .env.example as a guide."
    ]
    assert bool(secret not in "\n".join(messages))


def test_missing_model_reports_that_availability_was_not_checked():
    client = SimpleNamespace(
        models=SimpleNamespace(list=lambda: SimpleNamespace(data=[]))
    )
    messages = []

    result = connection_test.check_groq_connection(
        client, "FILL_IN_MODEL_NAME", messages.append
    )

    assert result == 0
    assert messages == [
        "Groq authentication successful.",
        "No model is configured; model availability was not checked.",
    ]
