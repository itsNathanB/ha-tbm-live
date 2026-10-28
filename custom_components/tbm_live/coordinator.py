"""Coordinateur de mise à jour des prochains passages."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Departure, TbmClient, TbmError
from .const import (
    CONF_DIRECTION_REF,
    CONF_LINE_REF,
    CONF_STOP_NAME,
    CONF_STOP_REF,
    CONF_STOP_REFS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

type TbmConfigEntry = ConfigEntry["TbmCoordinator"]


class TbmCoordinator(DataUpdateCoordinator[list[Departure]]):
    """Interroge stop-monitoring pour un arrêt, une ligne et un sens."""

    config_entry: TbmConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: TbmConfigEntry, client: TbmClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data.get(CONF_STOP_NAME)}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> list[Departure]:
        data = self.config_entry.data
        try:
            return await self.client.async_get_departures(
                # Les entrées créées en v0.1 n'ont qu'un seul point d'arrêt.
                stop_refs=data.get(CONF_STOP_REFS) or [data[CONF_STOP_REF]],
                line_ref=data[CONF_LINE_REF],
                direction_ref=data.get(CONF_DIRECTION_REF),
            )
        except TbmError as err:
            raise UpdateFailed(str(err)) from err
