"""Minimal Groq client construction for ClaimFast."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.config import get_groq_api_key

if TYPE_CHECKING:
    from groq import Groq


def create_groq_client(max_retries: int | None = None) -> Groq:
    """Create an authenticated Groq client without exposing the API key."""
    from groq import Groq

    client_options = {}
    if max_retries is not None:
        client_options["max_retries"] = max_retries
    return Groq(api_key=get_groq_api_key(), **client_options)
