"""Diagnostics : configuration et dernière réponse brute de l'API.

Les données TBM sont publiques et la clé d'API est la clé open data
documentée : rien à masquer.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import TbmConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TbmConfigEntry
) -> dict[str, Any]:
    """Diagnostics d'un arrêt suivi."""
    coordinator = entry.runtime_data
    return {
        "entry": dict(entry.data),
        "last_update_success": coordinator.last_update_success,
        "departures": [
            {
                "time": d.time.isoformat(),
                "aimed_time": d.aimed_time.isoformat() if d.aimed_time else None,
                "realtime": d.realtime,
                "destination": d.destination,
                "cancelled": d.cancelled,
            }
            for d in (coordinator.data or [])
        ],
        "last_api_response": coordinator.client.last_response,
    }
