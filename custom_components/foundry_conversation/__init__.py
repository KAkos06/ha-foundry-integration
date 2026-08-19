"""Microsoft Foundry Conversation integration."""

from dataclasses import dataclass

import openai

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

from .client import FoundryClient
from .const import CONF_ENDPOINT, PLATFORMS


@dataclass(slots=True)
class FoundryRuntimeData:
    """Runtime data for a Microsoft Foundry config entry."""

    client: FoundryClient


type FoundryConfigEntry = ConfigEntry[FoundryRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: FoundryConfigEntry) -> bool:
    """Set up Microsoft Foundry from a config entry."""
    openai_client = openai.AsyncOpenAI(
        api_key=entry.data[CONF_API_KEY],
        base_url=entry.data[CONF_ENDPOINT],
        http_client=get_async_client(hass),
    )
    entry.runtime_data = FoundryRuntimeData(client=FoundryClient(openai_client))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FoundryConfigEntry) -> bool:
    """Unload a Microsoft Foundry config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: FoundryConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
