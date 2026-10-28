"""Capteurs « Prochain passage » et « Passage suivant »."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import Departure
from .const import (
    ATTRIBUTION,
    CONF_DIRECTION_NAME,
    CONF_LINE_MODE,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    CONF_STOP_NAME,
    CONF_STOP_REF,
    DOMAIN,
    MAX_DEPARTURES_ATTR,
    MODE_TRAM,
)
from .coordinator import TbmConfigEntry, TbmCoordinator

# Un passage reste affiché 30 s après son heure (le véhicule est à quai).
GRACE = timedelta(seconds=30)


@dataclass(frozen=True, kw_only=True)
class TbmSensorDescription(SensorEntityDescription):
    """Description d'un capteur de passage."""

    index: int


SENSORS: tuple[TbmSensorDescription, ...] = (
    TbmSensorDescription(key="next_departure", translation_key="next_departure", index=0),
    TbmSensorDescription(
        key="second_departure", translation_key="second_departure", index=1
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TbmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crée les capteurs d'un arrêt suivi."""
    coordinator = entry.runtime_data
    async_add_entities(TbmDepartureSensor(coordinator, desc) for desc in SENSORS)


class TbmDepartureSensor(CoordinatorEntity[TbmCoordinator], SensorEntity):
    """Heure du n-ième prochain passage (horodatage)."""

    entity_description: TbmSensorDescription
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_attribution = ATTRIBUTION
    # La liste des passages change chaque minute : inutile de la stocker
    # dans l'historique.
    _unrecorded_attributes = frozenset({"departures", "minutes"})

    def __init__(
        self, coordinator: TbmCoordinator, description: TbmSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._data = entry.data
        self._attr_unique_id = f"{entry.unique_id}|{description.key}"
        self._attr_icon = (
            "mdi:tram" if self._data.get(CONF_LINE_MODE) == MODE_TRAM else "mdi:bus"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=entry.title,
            manufacturer="TBM – Bordeaux Métropole",
            model=self._data.get(CONF_LINE_NAME),
            entry_type=DeviceEntryType.SERVICE,
        )

    def _upcoming(self) -> list[Departure]:
        now = dt_util.utcnow()
        return [
            d
            for d in (self.coordinator.data or [])
            if not d.cancelled and d.time >= now - GRACE
        ]

    def _current(self) -> Departure | None:
        upcoming = self._upcoming()
        index = self.entity_description.index
        return upcoming[index] if len(upcoming) > index else None

    @property
    def native_value(self) -> datetime | None:
        departure = self._current()
        return departure.time if departure else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = dt_util.utcnow()
        departure = self._current()
        attrs: dict[str, Any] = {
            "line": self._data.get(CONF_LINE_NAME),
            "line_ref": self._data.get(CONF_LINE_REF),
            "mode": self._data.get(CONF_LINE_MODE),
            "stop": self._data.get(CONF_STOP_NAME),
            "stop_ref": self._data.get(CONF_STOP_REF),
            "direction": self._data.get(CONF_DIRECTION_NAME),
        }
        if departure:
            attrs.update(
                {
                    "minutes": max(0, int((departure.time - now).total_seconds() // 60)),
                    "realtime": departure.realtime,
                    "delay_minutes": departure.delay_minutes,
                    "destination": departure.destination,
                }
            )
        if self.entity_description.index == 0:
            attrs["departures"] = [
                d.as_dict(now) for d in self._upcoming()[:MAX_DEPARTURES_ATTR]
            ]
        return attrs
