"""TBM Live : prochains passages des bus et trams de Bordeaux Métropole."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TbmClient
from .coordinator import TbmConfigEntry, TbmCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: TbmConfigEntry) -> bool:
    """Configure un arrêt suivi."""
    client = TbmClient(async_get_clientsession(hass))
    coordinator = TbmCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TbmConfigEntry) -> bool:
    """Décharge un arrêt suivi."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
