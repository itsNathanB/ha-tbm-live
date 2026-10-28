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
# Part minimale des courses d'un sens pour proposer un arrêt dans ce sens.
MIN_SHARE = 0.15
# Part minimale des courses à un arrêt pour afficher un terminus.
MIN_DESTINATION_SHARE = 0.10


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
class StopDirection:
    """Un sens de passage à un arrêt donné."""

    ref: str
    # Terminus atteignables depuis cet arrêt, du plus fréquent au plus rare.
    destinations: list[str] = field(default_factory=list)
    # Points d'arrêt physiques à interroger pour ce sens.
    stop_refs: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """Libellé affiché : « Vers Pessac Centre / France Alouette »."""
        if self.destinations:
            return "Vers " + " / ".join(self.destinations)
        return f"Sens {int(self.ref) + 1}" if self.ref.isdigit() else self.ref


@dataclass(slots=True)
class LineStop:
    """Un arrêt (commercial) d'une ligne, avec les sens qui y passent."""

    name: str
    directions: dict[str, StopDirection] = field(default_factory=dict)


@dataclass(slots=True)
class Departure:
    """Un passage à venir."""

    time: datetime
    aimed_time: datetime | None
    realtime: bool
    destination: str | None
    line_name: str | None
    cancelled: bool = False
    journey_ref: str | None = None

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


_SMALL_WORDS = {"de", "du", "des", "la", "le", "les", "et", "à", "au", "aux", "sur", "en"}


def pretty_name(text: str | None) -> str | None:
    """« LA GARDETTE » -> « La Gardette ». Laisse intacts les noms déjà en casse mixte."""
    if not text or not text.isupper():
        return text

    def word(w: str, first: bool) -> str:
        lower = w.lower()
        if not first and lower in _SMALL_WORDS:
            return lower
        if lower.startswith(("d'", "l'")) and len(lower) > 2:
            head = lower[:2] if not first else lower[0].upper() + "'"
            return head + lower[2:].capitalize()
        return "-".join(part.capitalize() for part in lower.split("-"))

    return " ".join(word(w, i == 0) for i, w in enumerate(text.split()))


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def _calls_of(journey: dict[str, Any]) -> list[dict[str, Any]]:
    """Arrêts d'une course, dans l'ordre de desserte."""
    calls = collect(journey.get("RecordedCalls"), "RecordedCall")
    calls += collect(journey.get("EstimatedCalls"), "EstimatedCall")
    orders = [as_str(c.get("Order")) for c in calls]
    if calls and all(o and o.isdigit() for o in orders):
        calls = [c for _, c in sorted(zip((int(o) for o in orders), calls), key=lambda x: x[0])]
    return calls


def _merge_sequence(order: list[str], sequence: list[str]) -> None:
    """Insère dans `order` les éléments de `sequence` en respectant leur position."""
    position = -1
    for item in sequence:
        if item in order:
            position = order.index(item)
        else:
            position += 1
            order.insert(position, item)


def parse_line_stops_from_timetable(
    payloads: dict[str, dict[str, Any]], line: Line
) -> list[LineStop]:
    """Arrêts de la ligne et sens desservis, depuis estimated-timetable.

    `payloads` associe un DirectionRef demandé ("0", "1") à la réponse brute.
    Toutes les courses sont fusionnées : les branches (tram A, B...) et les
    services partiels sont donc couverts.

    Les couples (arrêt, sens) desservis par une petite minorité de courses
    sont écartés : ce sont des mouvements techniques (sortie de dépôt,
    voyages sans voyageurs...) qui feraient apparaître un arrêt sur une ligne
    qui ne le dessert pas en service normal (ex. « La Vache » sur le tram C).
    """
    stops: dict[str, LineStop] = {}
    display: dict[str, str] = {}
    counters: dict[tuple[str, str], Counter[str]] = {}
    served: Counter[tuple[str, str]] = Counter()
    sequences: dict[str, list[list[str]]] = {}

    for requested_direction, payload in payloads.items():
        for journey in collect(payload, "EstimatedVehicleJourney"):
            if as_str(journey.get("LineRef")) not in (None, line.ref):
                continue
            direction = as_str(journey.get("DirectionRef")) or requested_direction
            calls = [c for c in _calls_of(journey) if as_str(c.get("StopPointRef"))]
            if not calls:
                continue
            names = {
                as_str(c.get("StopPointRef")): as_str(c.get("StopPointName"))
                for c in calls
            }
            destination_ref = as_str(journey.get("DestinationRef")) or as_str(
                calls[-1].get("StopPointRef")
            )
            destination = pretty_name(
                as_str(journey.get("DestinationName")) or names.get(destination_ref)
            )
            sequence: list[str] = []
            for call in calls:
                ref = as_str(call.get("StopPointRef"))
                name = names.get(ref) or ref
                key = _norm(name)
                sequence.append(key)
                display.setdefault(key, name)
                # Le terminus d'une course n'est pas un départ.
                if ref == destination_ref:
                    continue
                stop = stops.setdefault(key, LineStop(name=name))
                stop_direction = stop.directions.setdefault(
                    direction, StopDirection(ref=direction)
                )
                if ref not in stop_direction.stop_refs:
                    stop_direction.stop_refs.append(ref)
                served[(key, direction)] += 1
                if destination:
                    counters.setdefault((key, direction), Counter())[destination] += 1
            sequences.setdefault(direction, []).append(sequence)

    if not stops:
        return []

    # Écarte les dessertes marginales (moins de MIN_SHARE des courses du sens).
    busiest: Counter[str] = Counter()
    for (_, direction), count in served.items():
        busiest[direction] = max(busiest[direction], count)
    for (key, direction), count in served.items():
        if count < max(2, MIN_SHARE * busiest[direction]):
            stops[key].directions.pop(direction, None)
    stops = {key: stop for key, stop in stops.items() if stop.directions}

    # Terminus affichés : on ignore les services partiels rares.
    for (key, direction), counter in counters.items():
        if key not in stops or direction not in stops[key].directions:
            continue
        total = sum(counter.values())
        ranked = counter.most_common()
        stops[key].directions[direction].destinations = [
            name
            for i, (name, count) in enumerate(ranked)
            if i == 0 or count >= MIN_DESTINATION_SHARE * total
        ]

    # Ordre le long de la ligne : sens 0 d'abord, puis sens 1 parcouru à l'envers.
    order: list[str] = []
    for direction in sorted(sequences):
        reverse = direction != min(sequences)
        for sequence in sorted(sequences[direction], key=len, reverse=True):
            _merge_sequence(order, list(reversed(sequence)) if reverse else sequence)

    return [stops[key] for key in order if key in stops]


def parse_line_stops_from_discovery(
    payload: dict[str, Any], line: Line
) -> list[LineStop]:
    """Repli (nuit) : arrêts desservant la ligne, sens issus de lines-discovery.

    Sans les courses du jour, on ne sait pas quel point d'arrêt physique sert
    quel sens : tous sont interrogés et les passages sont filtrés par sens.
    """
    refs_by_name: dict[str, list[str]] = {}
    display: dict[str, str] = {}
    for raw in collect(payload, "AnnotatedStopPointRef"):
        stop_ref = as_str(raw.get("StopPointRef"))
        if not stop_ref:
            continue
        served = {as_str(item) for item in as_list(raw.get("Lines"))}
        if line.ref not in served:
            continue
        name = as_str(raw.get("StopName")) or stop_ref
        key = _norm(name)
        display.setdefault(key, name)
        refs_by_name.setdefault(key, []).append(stop_ref)

    directions = line.directions or [Direction(ref="0", name=""), Direction(ref="1", name="")]
    result: list[LineStop] = []
    for key in sorted(refs_by_name, key=lambda k: display[k].casefold()):
        stop = LineStop(name=display[key])
        for direction in directions:
            stop.directions[direction.ref] = StopDirection(
                ref=direction.ref,
                destinations=[d.strip() for d in direction.name.split(" / ") if d.strip()],
                stop_refs=list(refs_by_name[key]),
            )
        result.append(stop)
    return result


def parse_departures(
    payload: dict[str, Any],
    line_ref: str,
    direction_ref: str | None = None,
    monitoring_ref: str | None = None,
) -> list[Departure]:
    """Parse la réponse stop-monitoring en passages triés.

    - garde uniquement la ligne et le sens demandés ;
    - écarte les véhicules qui terminent leur course à cet arrêt (arrivées).
    """
    departures: list[Departure] = []
    for visit in collect(payload, "MonitoredStopVisit"):
        journey = visit.get("MonitoredVehicleJourney") or {}
        if not isinstance(journey, dict):
            continue
        visit_line = as_str(journey.get("LineRef"))
        if visit_line and visit_line != line_ref:
            continue

        visit_direction = as_str(journey.get("DirectionRef"))
        if direction_ref is not None and visit_direction not in (None, direction_ref):
            continue

        call = journey.get("MonitoredCall") or {}
        if not isinstance(call, dict):
            continue

        stop_ref = as_str(visit.get("MonitoringRef")) or monitoring_ref
        order = as_str(call.get("Order"))
        if (
            stop_ref
            and as_str(journey.get("DestinationRef")) == stop_ref
            and not (order and order.isdigit() and int(order) <= 1)
        ):
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

        framed = journey.get("FramedVehicleJourneyRef")
        journey_ref = (
            as_str(framed.get("DatedVehicleJourneyRef"))
            if isinstance(framed, dict)
            else None
        ) or as_str(journey.get("VehicleJourneyRef"))

        departures.append(
            Departure(
                time=when,
                aimed_time=aimed,
                realtime=realtime,
                destination=pretty_name(
                    as_str(journey.get("DestinationName"))
                    or as_str(call.get("DestinationDisplay"))
                ),
                line_name=as_str(journey.get("PublishedLineName")),
                cancelled="cancel" in status,
                journey_ref=journey_ref,
            )
        )
    departures.sort(key=lambda d: d.time)
    return departures


def merge_departures(groups: list[list[Departure]]) -> list[Departure]:
    """Fusionne les passages de plusieurs points d'arrêt, sans doublon."""
    seen: set[str] = set()
    merged: list[Departure] = []
    for departure in sorted((d for g in groups for d in g), key=lambda d: d.time):
        if departure.journey_ref:
            if departure.journey_ref in seen:
                continue
            seen.add(departure.journey_ref)
        merged.append(departure)
    return merged


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

    async def async_get_line_stops(self, line: Line) -> list[LineStop]:
        """Arrêts de la ligne, avec les sens et terminus desservis.

        Source principale : estimated-timetable (courses réelles de la journée).
        Elle ne contient que la journée commerciale en cours ; la nuit, on se
        replie sur stoppoints-discovery + les sens de lines-discovery.
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

        stops = parse_line_stops_from_timetable(payloads, line)
        if stops:
            return stops

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
        stops = parse_line_stops_from_discovery(payload, line)
        if not stops:
            raise TbmNoDataError(f"Aucun arrêt trouvé pour {line.ref}")
        return stops

    async def async_get_departures(
        self,
        stop_refs: list[str],
        line_ref: str,
        direction_ref: str | None = None,
    ) -> list[Departure]:
        """Prochains passages à un arrêt (un ou plusieurs points physiques)."""
        groups: list[list[Departure]] = []
        responses: list[dict[str, Any]] = []
        for stop_ref in stop_refs:
            payload = await self._get(
                "stop-monitoring",
                {
                    "MonitoringRef": stop_ref,
                    "LineRef": line_ref,
                    "PreviewInterval": PREVIEW_INTERVAL,
                    "MaximumStopVisits": MAX_STOP_VISITS,
                },
            )
            if self.last_response:
                responses.append(self.last_response)
            groups.append(
                parse_departures(
                    payload,
                    line_ref,
                    direction_ref=direction_ref,
                    monitoring_ref=stop_ref,
                )
            )
        if len(responses) > 1:
            self.last_response = {"multiple": responses}
        return merge_departures(groups)
