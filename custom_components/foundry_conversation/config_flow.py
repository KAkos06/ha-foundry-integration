"""Config flow for Microsoft Foundry Conversation."""

import logging
from collections.abc import Mapping
from typing import Any, override

import openai
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_API_KEY, CONF_PROMPT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import (
    FoundryError,
    InvalidEndpointError,
    async_validate_connection,
    normalize_endpoint,
)
from .const import (
    CONF_ALLOW_CONTROL,
    CONF_ENDPOINT,
    CONF_MAX_OUTPUT_TOKENS,
    CONF_MAX_TOOL_ITERATIONS,
    CONF_MODEL,
    CONF_REASONING_EFFORT,
    CONF_TEMPERATURE,
    CONF_TIMEOUT,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_TOOL_ITERATIONS,
    DEFAULT_NAME,
    DEFAULT_OPTIONS,
    DEFAULT_TIMEOUT,
    DOMAIN,
    REASONING_DISABLED,
)

LOGGER = logging.getLogger(__name__)


def _connection_schema(
    suggested: Mapping[str, Any] | None = None,
    *,
    include_model: bool,
) -> vol.Schema:
    """Build a connection form schema."""
    suggested = suggested or {}
    schema: dict[vol.Marker, Any] = {
        vol.Required(
            CONF_ENDPOINT,
            default=suggested.get(CONF_ENDPOINT, ""),
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
    if include_model:
        schema[vol.Required(CONF_MODEL, default=suggested.get(CONF_MODEL, ""))] = str
    return vol.Schema(schema)


def _options_schema(options: Mapping[str, Any]) -> vol.Schema:
    """Build the options form schema."""
    schema: dict[vol.Marker, Any] = {
        vol.Required(CONF_MODEL, default=options.get(CONF_MODEL, "")): str,
        vol.Optional(
            CONF_PROMPT,
            description={"suggested_value": options.get(CONF_PROMPT)},
        ): TemplateSelector(),
        vol.Required(
            CONF_MAX_OUTPUT_TOKENS,
            default=options.get(CONF_MAX_OUTPUT_TOKENS, DEFAULT_MAX_OUTPUT_TOKENS),
        ): NumberSelector(
            NumberSelectorConfig(min=1, max=131072, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(
            CONF_MAX_TOOL_ITERATIONS,
            default=options.get(CONF_MAX_TOOL_ITERATIONS, DEFAULT_MAX_TOOL_ITERATIONS),
        ): NumberSelector(
            NumberSelectorConfig(min=1, max=20, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(
            CONF_TIMEOUT,
            default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        ): NumberSelector(
            NumberSelectorConfig(min=10, max=120, step=1, unit_of_measurement="s")
        ),
        vol.Required(
            CONF_ALLOW_CONTROL,
            default=options.get(CONF_ALLOW_CONTROL, False),
        ): bool,
        vol.Optional(
            CONF_TEMPERATURE,
            description={"suggested_value": options.get(CONF_TEMPERATURE)},
        ): NumberSelector(NumberSelectorConfig(min=0, max=2, step=0.05)),
        vol.Required(
            CONF_REASONING_EFFORT,
            default=options.get(CONF_REASONING_EFFORT, REASONING_DISABLED),
        ): SelectSelector(
            SelectSelectorConfig(
                options=[
                    REASONING_DISABLED,
                    "none",
                    "minimal",
                    "low",
                    "medium",
                    "high",
                    "xhigh",
                ],
                mode=SelectSelectorMode.DROPDOWN,
                translation_key=CONF_REASONING_EFFORT,
            )
        ),
    }
    return vol.Schema(schema)


async def _async_create_and_validate_client(
    hass: HomeAssistant,
    endpoint: str,
    api_key: str,
    model: str,
) -> None:
    """Create a temporary client and validate the connection."""
    client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint,
        http_client=get_async_client(hass),
    )
    await async_validate_connection(client, model)


def _log_validation_error(err: FoundryError) -> None:
    """Log useful API details without headers or credentials."""
    cause = err.__cause__
    LOGGER.warning(
        "Microsoft Foundry validation failed: category=%s cause=%s status=%s "
        "code=%s message=%s",
        err.error_key,
        type(cause).__name__ if cause else type(err).__name__,
        getattr(cause, "status_code", None),
        getattr(cause, "code", None),
        getattr(cause, "message", str(err)),
    )


class FoundryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Microsoft Foundry config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return FoundryOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up Microsoft Foundry."""
        errors: dict[str, str] = {}
        suggested = user_input or {}
        if user_input is not None:
            try:
                endpoint = normalize_endpoint(user_input[CONF_ENDPOINT])
            except InvalidEndpointError:
                errors[CONF_ENDPOINT] = "invalid_endpoint"
            else:
                try:
                    await _async_create_and_validate_client(
                        self.hass,
                        endpoint,
                        user_input[CONF_API_KEY],
                        user_input[CONF_MODEL].strip(),
                    )
                except FoundryError as err:
                    _log_validation_error(err)
                    errors["base"] = err.error_key
                except Exception:
                    LOGGER.exception("Unexpected Microsoft Foundry validation error")
                    errors["base"] = "unknown"
                else:
                    options = {
                        **DEFAULT_OPTIONS,
                        CONF_MODEL: user_input[CONF_MODEL].strip(),
                    }
                    return self.async_create_entry(
                        title=DEFAULT_NAME,
                        data={
                            CONF_ENDPOINT: endpoint,
                            CONF_API_KEY: user_input[CONF_API_KEY],
                        },
                        options=options,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(suggested, include_model=True),
            errors=errors,
        )

    @override
    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update an invalid API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _async_create_and_validate_client(
                    self.hass,
                    entry.data[CONF_ENDPOINT],
                    user_input[CONF_API_KEY],
                    entry.options[CONF_MODEL],
                )
            except FoundryError as err:
                _log_validation_error(err)
                errors["base"] = err.error_key
            except Exception:
                LOGGER.exception("Unexpected Microsoft Foundry reauthentication error")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )

    @override
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update endpoint and API key."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        suggested = {CONF_ENDPOINT: entry.data[CONF_ENDPOINT]}
        if user_input is not None:
            try:
                endpoint = normalize_endpoint(user_input[CONF_ENDPOINT])
            except InvalidEndpointError:
                errors[CONF_ENDPOINT] = "invalid_endpoint"
            else:
                try:
                    await _async_create_and_validate_client(
                        self.hass,
                        endpoint,
                        user_input[CONF_API_KEY],
                        entry.options[CONF_MODEL],
                    )
                except FoundryError as err:
                    _log_validation_error(err)
                    errors["base"] = err.error_key
                except Exception:
                    LOGGER.exception("Unexpected Microsoft Foundry validation error")
                    errors["base"] = "unknown"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_ENDPOINT: endpoint,
                            CONF_API_KEY: user_input[CONF_API_KEY],
                        },
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(suggested, include_model=False),
            errors=errors,
        )


class FoundryOptionsFlow(OptionsFlow):
    """Handle Microsoft Foundry options."""

    @override
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage conversation options."""
        errors: dict[str, str] = {}
        current = dict(self.config_entry.options)
        if user_input is not None:
            model = user_input[CONF_MODEL].strip()
            if model != current.get(CONF_MODEL):
                try:
                    await _async_create_and_validate_client(
                        self.hass,
                        self.config_entry.data[CONF_ENDPOINT],
                        self.config_entry.data[CONF_API_KEY],
                        model,
                    )
                except FoundryError as err:
                    _log_validation_error(err)
                    errors["base"] = err.error_key
                except Exception:
                    LOGGER.exception(
                        "Unexpected Microsoft Foundry model validation error"
                    )
                    errors["base"] = "unknown"
            if not errors:
                new_options = dict(user_input)
                new_options[CONF_MODEL] = model
                for integer_option in (
                    CONF_MAX_OUTPUT_TOKENS,
                    CONF_MAX_TOOL_ITERATIONS,
                    CONF_TIMEOUT,
                ):
                    new_options[integer_option] = int(new_options[integer_option])
                if not new_options.get(CONF_PROMPT):
                    new_options.pop(CONF_PROMPT, None)
                return self.async_create_entry(title="", data=new_options)
            current = user_input

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(current),
            errors=errors,
        )
