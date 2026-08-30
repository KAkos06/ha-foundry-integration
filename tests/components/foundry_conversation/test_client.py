"""Tests for Microsoft Foundry client helpers."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol
from homeassistant.helpers import llm
from httpx import AsyncClient

from custom_components.foundry_conversation.auth import create_foundry_connection
from custom_components.foundry_conversation.client import (
    FoundryAuthenticationError,
    FoundryPermissionError,
    InvalidEndpointError,
    _format_tool,
    _translate_openai_error,
    async_list_targets,
    async_validate_connection,
    make_target,
    normalize_endpoint,
    parse_target,
    project_endpoint_from_openai,
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
        (
            "https://example.services.ai.azure.com/api/projects/home",
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


def test_permission_error_translation() -> None:
    """Permission failures map to a dedicated user-facing error."""
    err = openai.PermissionDeniedError("Forbidden", response=Mock(), body=None)

    translated = _translate_openai_error(err)

    assert isinstance(translated, FoundryPermissionError)
    assert translated.error_key == "insufficient_permissions"


def test_target_helpers() -> None:
    """Target identifiers preserve type and name."""
    assert make_target("agent", "home-agent") == "agent:home-agent"
    assert parse_target("agent:home-agent") == ("agent", "home-agent")
    assert parse_target("legacy-deployment") == ("model", "legacy-deployment")
    assert (
        project_endpoint_from_openai(
            "https://example.services.ai.azure.com/api/projects/home/openai/v1/"
        )
        == "https://example.services.ai.azure.com/api/projects/home"
    )


async def test_validate_agent_uses_agent_reference() -> None:
    """Agent validation sends an agent reference instead of a model."""
    create = AsyncMock(return_value=SimpleNamespace(status="completed"))
    client = SimpleNamespace(responses=SimpleNamespace(create=create))

    result = await async_validate_connection(cast(Any, client), "agent:home-agent")
    assert result == "agent:home-agent"

    request = create.await_args.kwargs
    assert "model" not in request
    assert request["extra_body"]["agent_reference"] == {
        "type": "agent_reference",
        "name": "home-agent",
    }


async def test_validate_agent_fallback_from_model_not_found() -> None:
    """Unprefixed agent name falls back to agent reference on 404."""
    create = AsyncMock()
    # First call with model fails with NotFound, second call with agent succeeds
    create.side_effect = [
        openai.NotFoundError("Model not found", response=Mock(), body=None),
        SimpleNamespace(status="completed"),
    ]
    client = SimpleNamespace(responses=SimpleNamespace(create=create))

    result = await async_validate_connection(cast(Any, client), "home-agent")
    assert result == "agent:home-agent"
    assert create.await_count == 2


async def test_list_targets_combines_models_and_agents() -> None:
    """Entra project connections discover both target types."""

    async def models() -> Any:
        yield SimpleNamespace(id="gpt-5.4")

    client = SimpleNamespace(
        models=SimpleNamespace(list=AsyncMock(return_value=models()))
    )
    credential = SimpleNamespace(
        get_token=AsyncMock(return_value=SimpleNamespace(token="token"))
    )
    response = SimpleNamespace(
        status_code=200,
        raise_for_status=Mock(),
        json=Mock(return_value={"data": [{"name": "home-agent"}]}),
    )
    http_client = SimpleNamespace(get=AsyncMock(return_value=response))

    targets = await async_list_targets(
        cast(Any, client),
        "https://example.services.ai.azure.com/api/projects/home/openai/v1/",
        cast(Any, http_client),
        cast(Any, credential),
    )

    assert targets == [("model", "gpt-5.4"), ("agent", "home-agent")]


async def test_list_targets_returns_empty_without_real_data() -> None:
    """Discovery should not invent fallback model names."""

    async def models() -> Any:
        if False:
            yield None

    client = SimpleNamespace(
        models=SimpleNamespace(list=AsyncMock(return_value=models()))
    )
    http_client = SimpleNamespace(get=AsyncMock())

    targets = await async_list_targets(
        cast(Any, client),
        "https://example.openai.azure.com/openai/v1/",
        cast(Any, http_client),
    )

    assert targets == []


async def test_create_foundry_connection_uses_api_key_header() -> None:
    """API-key auth must use the Foundry ``api-key`` header."""
    connection = create_foundry_connection(
        {
            "auth_type": "api_key",
            "api_key": "test-key",
            "endpoint": "https://example.services.ai.azure.com/openai/v1/",
        },
        AsyncClient(),
    )
    try:
        assert connection.openai_client.auth_headers == {"api-key": "test-key"}
    finally:
        await connection.async_close()


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
