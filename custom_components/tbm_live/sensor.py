"""Capteurs des prochains passages (de 2 à 5 selon l'option choisie)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import Departure
from .const import (
    ATTRIBUTION,
    CONF_COUNT,
    CONF_DIRECTION_NAME,
    CONF_LINE_CODE,
    CONF_LINE_MODE,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    CONF_STOP_NAME,
    CONF_STOP_REF,
    DEFAULT_COUNT,
    DOMAIN,
    MODE_TRAM,
)
from .coordinator import TbmConfigEntry, TbmCoordinator

# Un passage reste affiché 30 s après son heure (le véhicule est à quai).
GRACE = timedelta(seconds=30)


@dataclass(frozen=True, kw_only=True)
class TbmSensorDescription(SensorEntityDescription):
    """Description d'un capteur de passage."""

    index: int


SENSORS: tuple[TbmSensorDescription, ...] = tuple(
    TbmSensorDescription(key=key, translation_key=key, index=index)
    for index, key in enumerate(
        (
            "next_departure",
            "second_departure",
            "third_departure",
            "fourth_departure",
            "fifth_departure",
        )
    )
)


def departure_count(entry: TbmConfigEntry) -> int:
    """Nombre de passages suivis pour cette entrée."""
    return int(entry.options.get(CONF_COUNT, DEFAULT_COUNT))


def line_code(data: dict[str, Any]) -> str:
    """Code court de la ligne (« B », « 15 »), y compris pour les entrées v0.1/v0.2."""
    if code := data.get(CONF_LINE_CODE):
        return str(code)
    name = str(data.get(CONF_LINE_NAME) or "")
    match = re.match(r"(?:tram|bus)\s+([^\s–-]+)", name, re.IGNORECASE)
    if match:
        return match.group(1)
    ref = str(data.get(CONF_LINE_REF) or "")
    parts = ref.split(":")
    return parts[2].lstrip("0") if len(parts) >= 3 else name[:3]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TbmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Crée les capteurs d'un arrêt suivi, et retire ceux devenus inutiles."""
    coordinator = entry.runtime_data
    count = departure_count(entry)

    registry = er.async_get(hass)
    for description in SENSORS[count:]:
        if entity_id := registry.async_get_entity_id(
            Platform.SENSOR, DOMAIN, f"{entry.unique_id}|{description.key}"
        ):
            registry.async_remove(entity_id)

    async_add_entities(
        TbmDepartureSensor(coordinator, description, count)
        for description in SENSORS[:count]
    )


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
        self,
        coordinator: TbmCoordinator,
        description: TbmSensorDescription,
        count: int,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._count = count
        entry = coordinator.config_entry
        self._data = entry.data
        self._attr_unique_id = f"{entry.unique_id}|{description.key}"
        self._attr_icon = (
            "mdi:tram" if self._data.get(CONF_LINE_MODE) == MODE_TRAM else "mdi:bus"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=entry.title,
            # Projet communautaire : surtout pas le nom de l'exploitant ici,
            # Home Assistant l'afficherait comme fabricant (« par … »).
            manufacturer="Projet communautaire (non officiel)",
            model=self._data.get(CONF_LINE_NAME),
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://github.com/itsNathanB/ha-tbm-live",
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
            "line_code": line_code(self._data),
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
            attrs["count"] = self._count
            attrs["departures"] = [
                d.as_dict(now) for d in self._upcoming()[: self._count]
            ]
        return attrs
