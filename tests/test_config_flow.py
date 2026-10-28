"""Tests de l'assistant de configuration (ligne → arrêt → sens)."""

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tbm_live.api import Direction, Line, LineStop, StopDirection
from custom_components.tbm_live.const import DOMAIN

TRAM_B = Line(
    ref="bordeaux:Line:60:LOC",
    name="Tram B",
    short_name="B",
    mode="tram",
    directions=[Direction("0", "Berges de la Garonne"), Direction("1", "Pessac Centre")],
)
BUS_1 = Line(ref="bordeaux:Line:01:LOC", name="Lianes 1", short_name="1", mode="bus")

STOPS = [
    LineStop(
        name="Quinconces",
        directions={
            "0": StopDirection("0", ["Berges de la Garonne", "Claveau"], ["BP:3763"]),
            "1": StopDirection("1", ["France Alouette", "Pessac Centre"], ["BP:3764"]),
        },
    ),
    LineStop(
        name="Pessac Centre",
        directions={"0": StopDirection("0", ["Berges de la Garonne"], ["BP:5247"])},
    ),
]


@pytest.fixture
def mock_client():
    with (
        patch(
            "custom_components.tbm_live.config_flow.TbmClient.async_get_lines",
            AsyncMock(return_value=[TRAM_B, BUS_1]),
        ),
        patch(
            "custom_components.tbm_live.config_flow.TbmClient.async_get_line_stops",
            AsyncMock(return_value=STOPS),
        ),
        patch("custom_components.tbm_live.async_setup_entry", return_value=True),
    ):
        yield


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_full_flow(hass: HomeAssistant, mock_client) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"line": "Tram B"}
    )
    assert result["step_id"] == "stop"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"stop": "Quinconces"}
    )
    assert result["step_id"] == "direction"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"direction": "Vers France Alouette / Pessac Centre"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Tram B · Quinconces → France Alouette / Pessac Centre"
    assert result["data"]["stop_refs"] == ["BP:3764"]
    assert result["data"]["direction_ref"] == "1"


async def test_single_direction_skips_step(hass: HomeAssistant, mock_client) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"line": "Tram B"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"stop": "Pessac Centre"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["direction_ref"] == "0"


async def test_free_text_rejected(hass: HomeAssistant, mock_client) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"line": "Tram Z"}
    )
    assert result["step_id"] == "user"
    assert result["errors"] == {"line": "invalid_choice"}


async def test_duplicate_aborts(hass: HomeAssistant, mock_client) -> None:
    for _ in range(2):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"line": "Tram B"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"stop": "Pessac Centre"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
