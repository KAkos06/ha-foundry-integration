"""Constants for the Microsoft Foundry Conversation integration."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "foundry_conversation"
PLATFORMS: Final = (Platform.CONVERSATION,)

CONF_ENDPOINT: Final = "endpoint"
CONF_MODEL: Final = "model"
CONF_MAX_OUTPUT_TOKENS: Final = "max_output_tokens"
CONF_MAX_TOOL_ITERATIONS: Final = "max_tool_iterations"
CONF_TIMEOUT: Final = "timeout"
CONF_ALLOW_CONTROL: Final = "allow_home_assistant_control"
CONF_TEMPERATURE: Final = "temperature"
CONF_REASONING_EFFORT: Final = "reasoning_effort"

DEFAULT_NAME: Final = "Microsoft Foundry"
DEFAULT_MAX_OUTPUT_TOKENS: Final = 4096
DEFAULT_MAX_TOOL_ITERATIONS: Final = 10
DEFAULT_TIMEOUT: Final = 60
REASONING_DISABLED: Final = "disabled"

DEFAULT_OPTIONS: Final[Mapping[str, object]] = MappingProxyType(
    {
        CONF_MAX_OUTPUT_TOKENS: DEFAULT_MAX_OUTPUT_TOKENS,
        CONF_MAX_TOOL_ITERATIONS: DEFAULT_MAX_TOOL_ITERATIONS,
        CONF_TIMEOUT: DEFAULT_TIMEOUT,
        CONF_ALLOW_CONTROL: False,
        CONF_REASONING_EFFORT: REASONING_DISABLED,
    }
)
