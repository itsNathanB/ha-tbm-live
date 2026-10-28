"""TBM Live : prochains passages des bus et trams de Bordeaux Métropole."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TbmClient
from .const import CARD_FILENAME, CARD_URL_BASE, DOMAIN
from .coordinator import TbmConfigEntry, TbmCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

WWW_DIR = Path(__file__).parent / "www"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Sert la carte Lovelace et la charge dans tous les tableaux de bord."""
    if hass.data.get(f"{DOMAIN}_card_registered"):
        return True
    if hass.http is None or "frontend" not in hass.config.components:
        # Pas d'interface web (tests, installation minimale) : rien à servir.
        return True
    hass.data[f"{DOMAIN}_card_registered"] = True

    from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    manifest = json.loads(
        await hass.async_add_executor_job(
            (Path(__file__).parent / "manifest.json").read_text, "utf-8"
        )
    )
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL_BASE, str(WWW_DIR), cache_headers=True)]
        )
    except RuntimeError:
        # Chemin déjà enregistré (rechargement de l'intégration).
        _LOGGER.debug("Chemin %s déjà servi", CARD_URL_BASE)
    # Le numéro de version force le navigateur à recharger la carte après
    # une mise à jour.
    add_extra_js_url(hass, f"{CARD_URL_BASE}/{CARD_FILENAME}?v={manifest['version']}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TbmConfigEntry) -> bool:
    """Configure un arrêt suivi."""
    client = TbmClient(async_get_clientsession(hass))
    coordinator = TbmCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: TbmConfigEntry) -> None:
    """Recharge l'entrée quand ses options changent (nombre de passages)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TbmConfigEntry) -> bool:
    """Décharge un arrêt suivi."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
