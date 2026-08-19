"""Tests for Microsoft Foundry client helpers."""

from unittest.mock import Mock

import pytest
import voluptuous as vol

from homeassistant.helpers import llm

from custom_components.foundry_conversation.client import (
    FoundryAuthenticationError,
    InvalidEndpointError,
    _format_tool,
    normalize_endpoint,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://example.openai.azure.com/openai/v1",
            "https://example.openai.azure.com/openai/v1/",
        ),
        (
            "https://example.services.ai.azure.com/openai/v1/responses",
            "https://example.services.ai.azure.com/openai/v1/",
        ),
        (
            "https://example.services.ai.azure.com/api/projects/home/openai/v1/",
            "https://example.services.ai.azure.com/api/projects/home/openai/v1/",
        ),
    ],
)
def test_normalize_endpoint(source: str, expected: str) -> None:
    """Supported endpoint forms are normalized to an SDK base URL."""
    assert normalize_endpoint(source) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.openai.azure.com/openai/v1/",
        "https://user:secret@example.openai.azure.com/openai/v1/",
        "https://example.openai.azure.com/",
        "https://example.openai.azure.com/openai/v1/?api-version=preview",
        "https://example.openai.azure.com/openai/v1/#fragment",
    ],
)
def test_reject_invalid_endpoint(endpoint: str) -> None:
    """Unsafe or non-v1 endpoint forms are rejected."""
    with pytest.raises(InvalidEndpointError):
        normalize_endpoint(endpoint)


def test_localized_error_message() -> None:
    """Runtime errors have English and Hungarian user-facing text."""
    error = FoundryAuthenticationError()
    assert "invalid" in error.user_message("en").lower()
    assert "érvénytelen" in error.user_message("hu-HU").lower()


def test_format_tool_preserves_required_parameters() -> None:
    """Home Assistant required tool arguments remain required."""
    tool = Mock(spec=llm.Tool)
    tool.name = "HassTest"
    tool.description = "Test a Home Assistant tool"
    tool.parameters = vol.Schema(
        {
            vol.Required("required_name"): str,
            vol.Optional("optional_value"): int,
        }
    )

    formatted = _format_tool(tool, None)

    assert formatted["name"] == "HassTest"
    assert formatted["parameters"]["required"] == ["required_name"]
