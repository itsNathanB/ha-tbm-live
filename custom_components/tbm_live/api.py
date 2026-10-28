"""Client de l'API temps réel TBM (SIRI-Lite, Bordeaux Métropole).

Ce module ne dépend pas de Home Assistant (hors aiohttp), ce qui permet de
tester le parseur indépendamment.

Le format SIRI-Lite de Mecatran/KB2M :
- encapsule la réponse dans un bloc racine "Siri" ;
- encapsule les références dans un objet {"value": "..."} ;
- encapsule les libellés (LineName, PlaceName...) dans des listes
  [{"value": "...", "lang": "fr"}].

Le parseur est volontairement tolérant : il cherche les blocs utiles quel que
soit leur niveau d'imbrication et accepte les valeurs encapsulées ou non.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import re
from typing import Any

import aiohttp

from .const import (
    API_BASE_URL,
    API_KEY,
    MAX_STOP_VISITS,
    METRO_BBOX,
    MODE_BUS,
    MODE_OTHER,
    MODE_TRAM,
    PREVIEW_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
TRAM_LETTERS = {"A", "B", "C", "D", "E", "F"}


class TbmError(Exception):
    """Erreur générique de l'API TBM."""


class TbmConnectionError(TbmError):
    """L'API est injoignable ou a répondu une erreur HTTP."""


class TbmNoDataError(TbmError):
    """L'API a répondu mais sans les données attendues."""


# --------------------------------------------------------------------------
# Modèles
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Direction:
    """Un sens de circulation d'une ligne."""

    ref: str
    name: str
    destination_ref: str | None = None


@dataclass(slots=True)
class Line:
    """Une ligne du réseau."""

    ref: str
    name: str
    short_name: str
    mode: str
    directions: list[Direction] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Libellé affiché dans la liste de choix."""
        prefix = {MODE_TRAM: "Tram", MODE_BUS: "Bus"}.get(self.mode, "Ligne")
        if self.name and self.name.upper() != self.short_name.upper():
            if self.name.lower().startswith(prefix.lower()):
                return self.name
            return f"{prefix} {self.short_name} – {self.name}"
        return f"{prefix} {self.short_name}"


@dataclass(slots=True)
class StopChoice:
    """Un arrêt dans un sens donné, proposé à l'utilisateur."""

    stop_ref: str
    stop_name: str
    direction_ref: str
    direction_name: str
    destination_ref: str | None = None
    # True si l'arrêt est un point d'arrêt physique propre à ce sens : dans ce
    # cas inutile de filtrer les passages par sens.
    physical: bool = False

    @property
    def key(self) -> str:
        """Identifiant unique du choix."""
        return f"{self.stop_ref}|{self.direction_ref}"

    @property
    def label(self) -> str:
        """Libellé affiché dans la liste de choix."""
        return f"{self.stop_name} → {self.direction_name}"


@dataclass(slots=True)
class Departure:
    """Un passage à venir."""

    time: datetime
    aimed_time: datetime | None
    realtime: bool
    destination: str | None
    line_name: str | None
    cancelled: bool = False

    @property
    def delay_minutes(self) -> int | None:
        """Retard (positif) ou avance (négatif) en minutes, si connu."""
        if not self.realtime or self.aimed_time is None:
            return None
        return round((self.time - self.aimed_time).total_seconds() / 60)

    def as_dict(self, now: datetime) -> dict[str, Any]:
        """Représentation pour les attributs d'entité."""
        return {
            "time": self.time.isoformat(),
            "minutes": max(0, int((self.time - now).total_seconds() // 60)),
            "aimed_time": self.aimed_time.isoformat() if self.aimed_time else None,
            "realtime": self.realtime,
            "delay_minutes": self.delay_minutes,
            "destination": self.destination,
            "cancelled": self.cancelled,
        }


# --------------------------------------------------------------------------
# Outils de parsing
# --------------------------------------------------------------------------


def unwrap(value: Any) -> Any:
    """Extrait une valeur scalaire d'un objet SIRI-Lite.

    Accepte "x", {"value": "x"}, [{"value": "x"}] ou [{"value": "x", "lang": "fr"}].
    """
    if isinstance(value, list):
        if not value:
            return None
        # On préfère un libellé en français s'il y en a plusieurs.
        for item in value:
            if isinstance(item, dict) and item.get("lang") in ("fr", "FR"):
                return unwrap(item)
        return unwrap(value[0])
    if isinstance(value, dict):
        for key in ("value", "Value"):
            if key in value:
                return value[key]
        return None
    return value


def as_str(value: Any) -> str | None:
    """Valeur unwrap convertie en chaîne non vide, sinon None."""
    value = unwrap(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def as_list(value: Any) -> list[Any]:
    """Normalise un élément SIRI qui peut être un objet seul ou une liste."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def find_all(obj: Any, key: str) -> list[Any]:
    """Toutes les valeurs associées à `key`, à n'importe quelle profondeur."""
    found: list[Any] = []
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for k, v in current.items():
                if k == key:
                    found.append(v)
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(current, list):
            stack.extend(reversed(current))
    return found


def collect(obj: Any, key: str) -> list[dict[str, Any]]:
    """Liste aplatie des objets trouvés sous `key`."""
    items: list[dict[str, Any]] = []
    for value in find_all(obj, key):
        items.extend(item for item in as_list(value) if isinstance(item, dict))
    return items


def parse_time(value: Any) -> datetime | None:
    """Parse une date ISO-8601 SIRI en datetime avec fuseau."""
    text = as_str(value)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    # Python < 3.11 n'accepte pas toutes les précisions de fraction.
    text = re.sub(r"\.(\d{6})\d+", r".\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def short_code_from_ref(ref: str) -> str:
    """'bordeaux:Line:01:LOC' -> '01' ; renvoie la ref si format inconnu."""
    parts = ref.split(":")
    if len(parts) >= 3:
        return parts[2]
    return ref


def guess_mode(short_name: str, name: str, raw: dict[str, Any]) -> str:
    """Devine le mode (tram/bus) d'une ligne."""
    for ext_key in ("Extension", "Extensions"):
        ext = raw.get(ext_key)
        if isinstance(ext, dict):
            route_type = str(unwrap(ext.get("RouteType")) or "").lower()
            if route_type in ("tram", "tramway", "0"):
                return MODE_TRAM
            if route_type in ("bus", "3"):
                return MODE_BUS
    lowered = (name or "").lower()
    if lowered.startswith("tram") or short_name.upper() in TRAM_LETTERS:
        return MODE_TRAM
    if "bat" in lowered or "navette fluviale" in lowered or "bateau" in lowered:
        return MODE_OTHER
    return MODE_BUS


def _line_sort_key(line: Line) -> tuple[int, int, str]:
    order = {MODE_TRAM: 0, MODE_BUS: 1, MODE_OTHER: 2}[line.mode]
    digits = re.sub(r"\D", "", line.short_name)
    number = int(digits) if digits else 9999
    return (order, number, line.short_name)


def parse_lines(payload: dict[str, Any]) -> list[Line]:
    """Parse la réponse lines-discovery."""
    lines: dict[str, Line] = {}
    for raw in collect(payload, "AnnotatedLineRef"):
        ref = as_str(raw.get("LineRef"))
        if not ref:
            continue
        name = as_str(raw.get("LineName")) or ""
        short = as_str(raw.get("LineCode")) or as_str(raw.get("PublicCode"))
        if not short:
            short = short_code_from_ref(ref).lstrip("0") or short_code_from_ref(ref)
        directions: list[Direction] = []
        for dest in as_list(raw.get("Destinations")):
            if not isinstance(dest, dict):
                continue
            direction_ref = as_str(dest.get("DirectionRef"))
            place = as_str(dest.get("PlaceName")) or as_str(dest.get("DestinationName"))
            if direction_ref is None or not place:
                continue
            directions.append(
                Direction(
                    ref=direction_ref,
                    name=place,
                    destination_ref=as_str(dest.get("DestinationRef")),
                )
            )
        lines[ref] = Line(
            ref=ref,
            name=name,
            short_name=short,
            mode=guess_mode(short, name, raw),
            directions=directions,
        )
    return sorted(lines.values(), key=_line_sort_key)


def parse_stops_from_timetable(
    payloads: dict[str, dict[str, Any]], line_ref: str
) -> list[StopChoice]:
    """Construit la liste ordonnée des arrêts par sens depuis estimated-timetable.

    `payloads` associe un DirectionRef demandé ("0", "1") à la réponse brute.
    """
    choices: list[StopChoice] = []
    for requested_direction, payload in payloads.items():
        journeys = [
            j
            for j in collect(payload, "EstimatedVehicleJourney")
            if as_str(j.get("LineRef")) in (None, line_ref)
        ]
        if not journeys:
            continue

        destination_names = Counter(
            name
            for j in journeys
            if (name := as_str(j.get("DestinationName")))
        )
        destination_refs = Counter(
            ref for j in journeys if (ref := as_str(j.get("DestinationRef")))
        )
        direction_name = (
            destination_names.most_common(1)[0][0]
            if destination_names
            else f"Sens {int(requested_direction) + 1}"
            if requested_direction.isdigit()
            else requested_direction
        )
        destination_ref = (
            destination_refs.most_common(1)[0][0] if destination_refs else None
        )

        def calls_of(journey: dict[str, Any]) -> list[dict[str, Any]]:
            calls = collect(journey.get("RecordedCalls"), "RecordedCall")
            calls += collect(journey.get("EstimatedCalls"), "EstimatedCall")
            # Tri par ordre de desserte si disponible.
            return sorted(
                calls,
                key=lambda c: int(as_str(c.get("Order")) or 0)
                if (as_str(c.get("Order")) or "").isdigit()
                else 0,
            )

        seen: set[str] = set()
        # Les courses les plus longues d'abord : elles couvrent tout le tracé.
        for journey in sorted(journeys, key=lambda j: len(calls_of(j)), reverse=True):
            for call in calls_of(journey):
                stop_ref = as_str(call.get("StopPointRef"))
                if not stop_ref or stop_ref in seen:
                    continue
                seen.add(stop_ref)
                choices.append(
                    StopChoice(
                        stop_ref=stop_ref,
                        stop_name=as_str(call.get("StopPointName")) or stop_ref,
                        direction_ref=requested_direction,
                        direction_name=direction_name,
                        destination_ref=destination_ref,
                        physical=True,
                    )
                )
    return choices


def parse_stops_from_discovery(
    payload: dict[str, Any], line: Line
) -> list[StopChoice]:
    """Repli : arrêts desservant la ligne, combinés aux sens de lines-discovery."""
    stops: dict[str, str] = {}
    for raw in collect(payload, "AnnotatedStopPointRef"):
        stop_ref = as_str(raw.get("StopPointRef"))
        if not stop_ref:
            continue
        served = {as_str(item) for item in as_list(raw.get("Lines"))}
        served |= {
            as_str(item.get("LineRef"))
            for item in as_list(raw.get("Lines"))
            if isinstance(item, dict) and "LineRef" in item
        }
        if line.ref not in served:
            continue
        stops[stop_ref] = as_str(raw.get("StopName")) or stop_ref

    directions = line.directions or [
        Direction(ref="0", name="Sens 1"),
        Direction(ref="1", name="Sens 2"),
    ]
    choices = [
        StopChoice(
            stop_ref=stop_ref,
            stop_name=stop_name,
            direction_ref=direction.ref,
            direction_name=direction.name,
            destination_ref=direction.destination_ref,
            physical=False,
        )
        for stop_ref, stop_name in sorted(stops.items(), key=lambda s: s[1])
        for direction in directions
    ]
    return choices


def parse_departures(
    payload: dict[str, Any],
    line_ref: str,
    direction_ref: str | None = None,
    destination_ref: str | None = None,
    filter_direction: bool = False,
) -> list[Departure]:
    """Parse la réponse stop-monitoring en passages triés."""
    departures: list[Departure] = []
    for visit in collect(payload, "MonitoredStopVisit"):
        journey = visit.get("MonitoredVehicleJourney") or {}
        if not isinstance(journey, dict):
            continue
        visit_line = as_str(journey.get("LineRef"))
        if visit_line and visit_line != line_ref:
            continue

        if filter_direction:
            visit_direction = as_str(journey.get("DirectionRef"))
            visit_destination = as_str(journey.get("DestinationRef"))
            if direction_ref is not None and visit_direction is not None:
                if visit_direction != direction_ref:
                    continue
            elif destination_ref and visit_destination:
                if visit_destination != destination_ref:
                    continue

        call = journey.get("MonitoredCall") or {}
        if not isinstance(call, dict):
            continue

        expected = parse_time(call.get("ExpectedDepartureTime")) or parse_time(
            call.get("ExpectedArrivalTime")
        )
        aimed = parse_time(call.get("AimedDepartureTime")) or parse_time(
            call.get("AimedArrivalTime")
        )
        when = expected or aimed
        if when is None:
            continue

        monitored = unwrap(journey.get("Monitored"))
        realtime = expected is not None and monitored is not False

        status = " ".join(
            str(as_str(x) or "")
            for x in (
                call.get("DepartureStatus"),
                call.get("ArrivalStatus"),
                journey.get("VehicleStatus"),
            )
        ).lower()

        departures.append(
            Departure(
                time=when,
                aimed_time=aimed,
                realtime=realtime,
                destination=as_str(journey.get("DestinationName"))
                or as_str(call.get("DestinationDisplay")),
                line_name=as_str(journey.get("PublishedLineName")),
                cancelled="cancel" in status,
            )
        )
    departures.sort(key=lambda d: d.time)
    return departures


# --------------------------------------------------------------------------
# Client HTTP
# --------------------------------------------------------------------------


class TbmClient:
    """Client asynchrone de l'API SIRI-Lite TBM."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self.last_response: dict[str, Any] | None = None

    async def _get(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = {"AccountKey": API_KEY, **(params or {})}
        url = f"{API_BASE_URL}/{method}.json"
        try:
            async with self._session.get(
                url,
                params=query,
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise TbmConnectionError(
                        f"{method} a répondu HTTP {resp.status} : {text[:200]}"
                    )
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise TbmConnectionError(f"{method} injoignable : {err}") from err
        except ValueError as err:
            raise TbmNoDataError(f"{method} : réponse non JSON") from err
        if not isinstance(payload, dict):
            raise TbmNoDataError(f"{method} : réponse inattendue")
        self.last_response = {"method": method, "params": params, "payload": payload}
        return payload

    async def async_check(self) -> bool:
        """Vérifie que l'API répond."""
        await self._get("check-status")
        return True

    async def async_get_lines(self) -> list[Line]:
        """Liste des lignes du réseau."""
        lines = parse_lines(await self._get("lines-discovery"))
        if not lines:
            raise TbmNoDataError("Aucune ligne renvoyée par lines-discovery")
        return lines

    async def async_get_stop_choices(self, line: Line) -> list[StopChoice]:
        """Arrêts de la ligne, par sens.

        Source principale : estimated-timetable (arrêts réellement desservis,
        dans l'ordre, avec la destination). Elle ne contient que les courses
        de la journée commerciale en cours ; la nuit, on se replie sur
        stoppoints-discovery.
        """
        payloads: dict[str, dict[str, Any]] = {}
        for direction in ("0", "1"):
            try:
                payloads[direction] = await self._get(
                    "estimated-timetable",
                    {
                        "LineRef": line.ref,
                        "DirectionRef": direction,
                        "PreviewInterval": "PT12H",
                    },
                )
            except TbmError as err:
                _LOGGER.debug("estimated-timetable sens %s : %s", direction, err)

        choices = parse_stops_from_timetable(payloads, line.ref)
        if choices:
            return choices

        _LOGGER.debug("Repli sur stoppoints-discovery pour %s", line.ref)
        payload = await self._get(
            "stoppoints-discovery",
            {
                "BoundingBox.UpperLeft.longitude": METRO_BBOX["upper_left_lon"],
                "BoundingBox.UpperLeft.latitude": METRO_BBOX["upper_left_lat"],
                "BoundingBox.LowerRight.longitude": METRO_BBOX["lower_right_lon"],
                "BoundingBox.LowerRight.latitude": METRO_BBOX["lower_right_lat"],
            },
        )
        choices = parse_stops_from_discovery(payload, line)
        if not choices:
            raise TbmNoDataError(f"Aucun arrêt trouvé pour {line.ref}")
        return choices

    async def async_get_departures(
        self,
        stop_ref: str,
        line_ref: str,
        direction_ref: str | None = None,
        destination_ref: str | None = None,
        filter_direction: bool = False,
    ) -> list[Departure]:
        """Prochains passages à un arrêt, pour une ligne et un sens."""
        payload = await self._get(
            "stop-monitoring",
            {
                "MonitoringRef": stop_ref,
                "LineRef": line_ref,
                "PreviewInterval": PREVIEW_INTERVAL,
                "MaximumStopVisits": MAX_STOP_VISITS,
            },
        )
        return parse_departures(
            payload,
            line_ref,
            direction_ref=direction_ref,
            destination_ref=destination_ref,
            filter_direction=filter_direction,
        )
