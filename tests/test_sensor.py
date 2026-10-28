"""Les capteurs reçoivent bien les passages du coordinateur."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.tbm_live.api import Departure
from custom_components.tbm_live.const import DOMAIN

DATA_V2 = {
    "line_ref": "bordeaux:Line:60:LOC",
    "line_name": "Tram B",
    "line_mode": "tram",
    "stop_name": "Quinconces",
    "stop_ref": "BP:3764",
    "stop_refs": ["BP:3764"],
    "direction_ref": "1",
    "direction_name": "France Alouette / Pessac Centre",
}
# Entrée créée en v0.1 : pas de stop_refs.
DATA_V1 = {
    "line_ref": "bordeaux:Line:60:LOC",
    "line_name": "Tram B",
    "line_mode": "tram",
    "stop_name": "Quinconces",
    "stop_ref": "BP:3764",
    "direction_ref": "1",
    "direction_name": "Sens 2",
    "physical": True,
}


async def _setup(hass: HomeAssistant, data: dict) -> tuple[MockConfigEntry, AsyncMock]:
    now = dt_util.utcnow()
    departures = [
        Departure(time=now + timedelta(minutes=m), aimed_time=now + timedelta(minutes=m),
                  realtime=True, destination="Pessac Centre", line_name=None,
                  journey_ref=f"j{m}")
        for m in (4, 12, 20)
    ]
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id="u", title="Tram B · Quinconces")
    entry.add_to_hass(hass)
    mock = AsyncMock(return_value=departures)
    with patch("custom_components.tbm_live.TbmClient.async_get_departures", mock):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, mock


def _states(hass: HomeAssistant, entry: MockConfigEntry):
    ents = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    return {e.unique_id.split("|")[-1]: hass.states.get(e.entity_id) for e in ents}


async def test_sensors_have_values(hass: HomeAssistant) -> None:
    entry, mock = await _setup(hass, DATA_V2)
    states = _states(hass, entry)
    assert states["next_departure"].state not in ("unknown", "unavailable")
    assert states["second_departure"].state not in ("unknown", "unavailable")
    assert states["next_departure"].attributes["minutes"] in (3, 4)
    assert len(states["next_departure"].attributes["departures"]) == 3
    assert mock.call_args.kwargs["stop_refs"] == ["BP:3764"]
    assert mock.call_args.kwargs["direction_ref"] == "1"


async def test_v01_entry_still_works(hass: HomeAssistant) -> None:
    entry, mock = await _setup(hass, DATA_V1)
    assert _states(hass, entry)["next_departure"].state not in ("unknown", "unavailable")
    assert mock.call_args.kwargs["stop_refs"] == ["BP:3764"]
