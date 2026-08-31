"""Tests for the Microsoft Foundry config flow."""

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.foundry_conversation import async_migrate_entry
from custom_components.foundry_conversation.const import (
    AUTH_API_KEY,
    CONF_ALLOW_CONTROL,
    CONF_AUTH_TYPE,
    CONF_ENDPOINT,
    CONF_MAX_OUTPUT_TOKENS,
    CONF_MAX_TOOL_ITERATIONS,
    CONF_REASONING_EFFORT,
    CONF_TARGET,
    CONF_TIMEOUT,
    DOMAIN,
    REASONING_DISABLED,
    TARGET_AGENT,
    TARGET_MODEL,
)


async def test_user_flow_lists_and_selects_target(hass: HomeAssistant) -> None:
    """Credentials lead to a labeled model and agent dropdown."""
    assert await async_setup_component(hass, "homeassistant", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_ENDPOINT: "https://example.services.ai.azure.com/api/projects/home",
            CONF_AUTH_TYPE: AUTH_API_KEY,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "credentials"

    discover = AsyncMock(
        return_value=[(TARGET_MODEL, "gpt-5.4"), (TARGET_AGENT, "home-agent")]
    )
    with patch(
        "custom_components.foundry_conversation.config_flow."
        "_async_list_connection_targets",
        discover,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "secret-key"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "target"
    target_selector = next(iter(result["data_schema"].schema.values()))
    labels = [option["label"] for option in target_selector.config["options"]]
    assert labels == ["gpt-5.4 — model", "home-agent — agent"]

    validate = AsyncMock()
    with patch(
        "custom_components.foundry_conversation.config_flow._async_validate_target",
        validate,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TARGET: "agent:home-agent"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENDPOINT] == (
        "https://example.services.ai.azure.com/api/projects/home/openai/v1/"
    )
    assert result["data"][CONF_API_KEY] == "secret-key"
    assert result["options"][CONF_TARGET] == "agent:home-agent"
    validate.assert_awaited_once()


async def test_invalid_endpoint(hass: HomeAssistant) -> None:
    """An invalid endpoint is reported on its form field."""
    assert await async_setup_component(hass, "homeassistant", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_ENDPOINT: "http://example.invalid/openai/v1/",
            CONF_AUTH_TYPE: AUTH_API_KEY,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ENDPOINT: "invalid_endpoint"}


async def test_migrate_model_entry(hass: HomeAssistant) -> None:
    """Version 1 model entries migrate without user intervention."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENDPOINT: "https://example.openai.azure.com/openai/v1/",
            CONF_API_KEY: "secret-key",
        },
        options={"model": "gpt-5.4"},
        version=1,
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.data[CONF_AUTH_TYPE] == AUTH_API_KEY
    assert entry.options[CONF_TARGET] == "model:gpt-5.4"
    assert "model" not in entry.options


async def test_agent_options_save_home_assistant_control(
    hass: HomeAssistant,
) -> None:
    """Home Assistant control remains enabled for an agent target."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENDPOINT: "https://example.services.ai.azure.com/openai/v1/",
            CONF_API_KEY: "secret-key",
            CONF_AUTH_TYPE: AUTH_API_KEY,
        },
        options={CONF_TARGET: "agent:home-agent"},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.foundry_conversation.config_flow."
        "_async_list_connection_targets",
        AsyncMock(return_value=[(TARGET_AGENT, "home-agent")]),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_TARGET: "agent:home-agent",
                CONF_MAX_OUTPUT_TOKENS: 4096,
                CONF_MAX_TOOL_ITERATIONS: 10,
                CONF_TIMEOUT: 60,
                CONF_ALLOW_CONTROL: True,
                CONF_REASONING_EFFORT: REASONING_DISABLED,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ALLOW_CONTROL] is True
