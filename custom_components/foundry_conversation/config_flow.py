"""Config flow for Microsoft Foundry Conversation."""

import logging
from collections.abc import Mapping
from typing import Any, override

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
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .auth import create_foundry_connection
from .client import (
    FoundryError,
    InvalidEndpointError,
    async_list_targets,
    async_validate_connection,
    make_target,
    normalize_endpoint,
    parse_target,
)
from .const import (
    AUTH_API_KEY,
    AUTH_ENTRA_ID,
    CONF_ALLOW_CONTROL,
    CONF_AUTH_TYPE,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_ENDPOINT,
    CONF_MAX_OUTPUT_TOKENS,
    CONF_MAX_TOOL_ITERATIONS,
    CONF_MODEL,
    CONF_REASONING_EFFORT,
    CONF_TARGET,
    CONF_TEMPERATURE,
    CONF_TENANT_ID,
    CONF_TIMEOUT,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_TOOL_ITERATIONS,
    DEFAULT_NAME,
    DEFAULT_OPTIONS,
    DEFAULT_TIMEOUT,
    DOMAIN,
    REASONING_DISABLED,
    TARGET_AGENT,
)

LOGGER = logging.getLogger(__name__)


def _auth_schema(suggested: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build the endpoint and authentication method schema."""
    suggested = suggested or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_ENDPOINT, default=suggested.get(CONF_ENDPOINT, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
            vol.Required(
                CONF_AUTH_TYPE, default=suggested.get(CONF_AUTH_TYPE, AUTH_API_KEY)
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[AUTH_API_KEY, AUTH_ENTRA_ID],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_AUTH_TYPE,
                )
            ),
        }
    )


def _credentials_schema(
    auth_type: str, suggested: Mapping[str, Any] | None = None
) -> vol.Schema:
    """Build a credential schema for the selected authentication method."""
    suggested = suggested or {}
    if auth_type == AUTH_ENTRA_ID:
        return vol.Schema(
            {
                vol.Required(
                    CONF_TENANT_ID, default=suggested.get(CONF_TENANT_ID, "")
                ): str,
                vol.Required(
                    CONF_CLIENT_ID, default=suggested.get(CONF_CLIENT_ID, "")
                ): str,
                vol.Required(CONF_CLIENT_SECRET): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
    return vol.Schema(
        {
            vol.Required(CONF_API_KEY): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            )
        }
    )


def _target_selector(
    targets: list[tuple[str, str]], current: str | None = None
) -> SelectSelector:
    """Build the model and agent dropdown."""
    options = [
        SelectOptionDict(
            value=make_target(target_type, name), label=f"{name} — {target_type}"
        )
        for target_type, name in targets
    ]
    if current and current not in {option["value"] for option in options}:
        target_type, name = parse_target(current)
        options.append(SelectOptionDict(value=current, label=f"{name} — {target_type}"))
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
            sort=True,
        )
    )


def _target_schema(
    targets: list[tuple[str, str]], current: str | None = None
) -> vol.Schema:
    """Build the target selection form."""
    default = current or (make_target(*targets[0]) if targets else "")
    return vol.Schema(
        {vol.Required(CONF_TARGET, default=default): _target_selector(targets, current)}
    )


def _options_schema(
    options: Mapping[str, Any], targets: list[tuple[str, str]]
) -> vol.Schema:
    """Build the integration options schema."""
    current_target = options.get(CONF_TARGET)
    if current_target is None and options.get(CONF_MODEL):
        current_target = make_target("model", options[CONF_MODEL])
    schema: dict[vol.Marker, Any] = {
        vol.Required(CONF_TARGET, default=current_target or ""): _target_selector(
            targets, current_target
        ),
        vol.Optional(
            CONF_PROMPT, description={"suggested_value": options.get(CONF_PROMPT)}
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
            CONF_TIMEOUT, default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        ): NumberSelector(
            NumberSelectorConfig(min=10, max=120, step=1, unit_of_measurement="s")
        ),
        vol.Required(
            CONF_ALLOW_CONTROL, default=options.get(CONF_ALLOW_CONTROL, False)
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


async def _async_list_connection_targets(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> list[tuple[str, str]]:
    """Create temporary clients and list available targets."""
    http_client = get_async_client(hass)
    connection = create_foundry_connection(data, http_client)
    try:
        return await async_list_targets(
            connection.openai_client,
            data[CONF_ENDPOINT],
            http_client,
            connection.credential,
            api_key=connection.api_key,
        )
    finally:
        await connection.async_close()


async def _async_validate_target(
    hass: HomeAssistant, data: Mapping[str, Any], target: str
) -> str:
    """Create a temporary client and validate a model or agent target."""
    connection = create_foundry_connection(data, get_async_client(hass))
    try:
        return await async_validate_connection(connection.openai_client, target)
    finally:
        await connection.async_close()


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

    VERSION = 2

    def __init__(self) -> None:
        """Initialize flow state."""
        self._connection_data: dict[str, Any] = {}
        self._targets: list[tuple[str, str]] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return FoundryOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect endpoint and authentication type."""
        errors: dict[str, str] = {}
        suggested = user_input or {}
        if user_input is not None:
            try:
                endpoint = normalize_endpoint(user_input[CONF_ENDPOINT])
            except InvalidEndpointError:
                errors[CONF_ENDPOINT] = "invalid_endpoint"
            else:
                self._connection_data = {
                    CONF_ENDPOINT: endpoint,
                    CONF_AUTH_TYPE: user_input[CONF_AUTH_TYPE],
                }
                return await self.async_step_credentials()
        return self.async_show_form(
            step_id="user", data_schema=_auth_schema(suggested), errors=errors
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials and discover available targets."""
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {**self._connection_data, **user_input}
            try:
                targets = await _async_list_connection_targets(self.hass, candidate)
            except FoundryError as err:
                _log_validation_error(err)
                errors["base"] = err.error_key
            except Exception:
                LOGGER.exception("Unexpected Microsoft Foundry discovery error")
                errors["base"] = "unknown"
            else:
                if not targets:
                    errors["base"] = "no_targets"
                else:
                    self._connection_data = candidate
                    self._targets = targets
                    return await self.async_step_target()
        return self.async_show_form(
            step_id="credentials",
            data_schema=_credentials_schema(
                self._connection_data.get(CONF_AUTH_TYPE, AUTH_API_KEY), user_input
            ),
            errors=errors,
        )

    async def async_step_target(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select and validate a model or agent."""
        errors: dict[str, str] = {}
        if user_input is not None:
            target = user_input[CONF_TARGET].strip()
            try:
                target = await _async_validate_target(
                    self.hass, self._connection_data, target
                )
            except FoundryError as err:
                _log_validation_error(err)
                errors["base"] = err.error_key
            except Exception:
                LOGGER.exception("Unexpected Microsoft Foundry target validation error")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data=self._connection_data,
                    options={**DEFAULT_OPTIONS, CONF_TARGET: target},
                )
        return self.async_show_form(
            step_id="target",
            data_schema=_target_schema(
                self._targets, user_input.get(CONF_TARGET) if user_input else None
            ),
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
        """Update rejected credentials."""
        entry = self._get_reauth_entry()
        auth_type = entry.data.get(CONF_AUTH_TYPE, AUTH_API_KEY)
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {**entry.data, **user_input}
            target = entry.options.get(
                CONF_TARGET, make_target("model", entry.options.get(CONF_MODEL, ""))
            )
            try:
                await _async_validate_target(self.hass, candidate, target)
            except FoundryError as err:
                _log_validation_error(err)
                errors["base"] = err.error_key
            except Exception:
                LOGGER.exception("Unexpected Microsoft Foundry reauthentication error")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(entry, data=candidate)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(auth_type),
            errors=errors,
        )

    @override
    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update endpoint and authentication type."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        suggested = user_input or entry.data
        if user_input is not None:
            try:
                endpoint = normalize_endpoint(user_input[CONF_ENDPOINT])
            except InvalidEndpointError:
                errors[CONF_ENDPOINT] = "invalid_endpoint"
            else:
                self._connection_data = {
                    CONF_ENDPOINT: endpoint,
                    CONF_AUTH_TYPE: user_input[CONF_AUTH_TYPE],
                }
                return await self.async_step_reconfigure_credentials()
        return self.async_show_form(
            step_id="reconfigure", data_schema=_auth_schema(suggested), errors=errors
        )

    async def async_step_reconfigure_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate replacement credentials and proceed to target selection."""
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {**self._connection_data, **user_input}
            try:
                targets = await _async_list_connection_targets(self.hass, candidate)
            except FoundryError as err:
                _log_validation_error(err)
                errors["base"] = err.error_key
            except Exception:
                LOGGER.exception("Unexpected Microsoft Foundry reconfigure error")
                errors["base"] = "unknown"
            else:
                self._connection_data = candidate
                self._targets = targets
                return await self.async_step_reconfigure_target()
        return self.async_show_form(
            step_id="reconfigure_credentials",
            data_schema=_credentials_schema(
                self._connection_data.get(CONF_AUTH_TYPE, AUTH_API_KEY)
            ),
            errors=errors,
        )

    async def async_step_reconfigure_target(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select and validate a model or agent during reconfiguration."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        current_target = entry.options.get(
            CONF_TARGET, make_target("model", entry.options.get(CONF_MODEL, ""))
        )
        if user_input is not None:
            target = user_input[CONF_TARGET].strip()
            try:
                target = await _async_validate_target(
                    self.hass, self._connection_data, target
                )
            except FoundryError as err:
                _log_validation_error(err)
                errors["base"] = err.error_key
            except Exception:
                LOGGER.exception("Unexpected Microsoft Foundry target validation error")
                errors["base"] = "unknown"
            else:
                new_options = dict(entry.options)
                new_options.pop(CONF_MODEL, None)
                new_options[CONF_TARGET] = target
                if parse_target(target)[0] == TARGET_AGENT:
                    new_options[CONF_ALLOW_CONTROL] = False
                return self.async_update_reload_and_abort(
                    entry,
                    data=self._connection_data,
                    options=new_options,
                )
        return self.async_show_form(
            step_id="reconfigure_target",
            data_schema=_target_schema(
                self._targets,
                user_input.get(CONF_TARGET) if user_input else current_target,
            ),
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
        try:
            targets = await _async_list_connection_targets(
                self.hass, self.config_entry.data
            )
        except FoundryError as err:
            _log_validation_error(err)
            errors["base"] = err.error_key
            targets = []
        except Exception:
            LOGGER.exception("Unexpected Microsoft Foundry discovery error")
            errors["base"] = "unknown"
            targets = []

        if user_input is not None:
            target = user_input[CONF_TARGET].strip()
            old_target = current.get(
                CONF_TARGET, make_target("model", current.get(CONF_MODEL, ""))
            )
            if target != old_target:
                try:
                    target = await _async_validate_target(
                        self.hass, self.config_entry.data, target
                    )
                except FoundryError as err:
                    _log_validation_error(err)
                    errors["base"] = err.error_key
                except Exception:
                    LOGGER.exception(
                        "Unexpected Microsoft Foundry target validation error"
                    )
                    errors["base"] = "unknown"
            else:
                target = old_target
            if parse_target(target)[0] == TARGET_AGENT:
                user_input[CONF_ALLOW_CONTROL] = False
            if not errors:
                new_options = dict(user_input)
                new_options.pop(CONF_MODEL, None)
                new_options[CONF_TARGET] = target
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
            data_schema=_options_schema(current, targets),
            errors=errors,
        )
