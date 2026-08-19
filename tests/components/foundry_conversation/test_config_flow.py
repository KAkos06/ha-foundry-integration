"""Tests for the Microsoft Foundry config flow."""

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.foundry_conversation.const import (
    CONF_ENDPOINT,
    CONF_MODEL,
    DOMAIN,
)


async def test_user_flow(hass: HomeAssistant) -> None:
    """A valid endpoint, key, and deployment create an entry."""
    validate = AsyncMock()
    with patch(
        "custom_components.foundry_conversation.config_flow."
        "_async_create_and_validate_client",
        validate,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_ENDPOINT: (
                    "https://example.services.ai.azure.com/openai/v1/responses"
                ),
                CONF_API_KEY: "secret-key",
                CONF_MODEL: "gpt-5.4",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENDPOINT] == (
        "https://example.services.ai.azure.com/openai/v1/"
    )
    assert result["data"][CONF_API_KEY] == "secret-key"
    assert result["options"][CONF_MODEL] == "gpt-5.4"
    validate.assert_awaited_once()


async def test_invalid_endpoint(hass: HomeAssistant) -> None:
    """An invalid endpoint is reported on its form field."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_ENDPOINT: "http://example.invalid/openai/v1/",
            CONF_API_KEY: "secret-key",
            CONF_MODEL: "gpt-5.4",
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ENDPOINT: "invalid_endpoint"}
