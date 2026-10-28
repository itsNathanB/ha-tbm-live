"""Constantes de l'intégration TBM Live."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "tbm_live"

# API SIRI-Lite open data de Bordeaux Métropole (opérée par Mecatran / KB2M).
API_BASE_URL = "https://bdx.mecatran.com/utw/ws/siri/2.0/bordeaux"
# Clé publique documentée par Bordeaux Métropole pour l'open data.
API_KEY = "opendata-bordeaux-metropole-flux-gtfs-rt"

# Emprise couvrant la métropole bordelaise (utilisée pour lister les arrêts).
METRO_BBOX = {
    "upper_left_lon": -0.85,
    "upper_left_lat": 45.05,
    "lower_right_lon": -0.35,
    "lower_right_lat": 44.70,
}

CONF_LINE_REF = "line_ref"
CONF_LINE_NAME = "line_name"
CONF_LINE_MODE = "line_mode"
CONF_STOP_REF = "stop_ref"
# Points d'arrêt physiques interrogés (v0.2+). CONF_STOP_REF reste le premier.
CONF_STOP_REFS = "stop_refs"
CONF_STOP_NAME = "stop_name"
CONF_DIRECTION_REF = "direction_ref"
CONF_DIRECTION_NAME = "direction_name"
# Champs des formulaires de configuration.
CONF_LINE = "line"
CONF_STOP = "stop"
CONF_DIRECTION = "direction"

DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)
# Fenêtre d'interrogation des prochains passages (durée ISO-8601).
PREVIEW_INTERVAL = "PT2H"
MAX_STOP_VISITS = 20
# Nombre de passages exposés en attribut.
MAX_DEPARTURES_ATTR = 6

MODE_TRAM = "tram"
MODE_BUS = "bus"
MODE_OTHER = "other"

ATTRIBUTION = (
    "Source : open data Bordeaux Métropole (Licence Ouverte). "
    "Intégration non officielle."
)
