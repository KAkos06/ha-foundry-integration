"""Microsoft Foundry Conversation integration."""

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

from .auth import FoundryConnection, create_foundry_connection
from .client import FoundryClient, make_target
from .const import (
    AUTH_API_KEY,
    CONF_AUTH_TYPE,
    CONF_MODEL,
    CONF_TARGET,
    PLATFORMS,
)


@dataclass(slots=True)
class FoundryRuntimeData:
    """Runtime data for a Microsoft Foundry config entry."""

    client: FoundryClient
    connection: FoundryConnection


type FoundryConfigEntry = ConfigEntry[FoundryRuntimeData]


async def async_migrate_entry(
    hass: HomeAssistant, config_entry: FoundryConfigEntry
) -> bool:
    """Migrate API-key model-only entries to target-aware entries."""
    if config_entry.version > 2:
        return False
    if config_entry.version == 1:
        data = {**config_entry.data, CONF_AUTH_TYPE: AUTH_API_KEY}
        options = dict(config_entry.options)
        model = options.pop(CONF_MODEL, "")
        options[CONF_TARGET] = make_target("model", model)
        hass.config_entries.async_update_entry(
            config_entry, data=data, options=options, version=2
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FoundryConfigEntry) -> bool:
    """Set up Microsoft Foundry from a config entry."""
    connection = create_foundry_connection(
        entry.data,
        get_async_client(hass),
    )
    entry.runtime_data = FoundryRuntimeData(
        client=FoundryClient(connection.openai_client),
        connection=connection,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FoundryConfigEntry) -> bool:
    """Unload a Microsoft Foundry config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.connection.async_close()
    return unloaded


async def _async_update_listener(
    hass: HomeAssistant, entry: FoundryConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
