"""Tests du parseur sur des extraits de réponses réelles."""

from datetime import UTC, datetime, timedelta

from custom_components.tbm_live.api import (
    Line,
    merge_departures,
    parse_departures,
    parse_line_stops_from_timetable,
    pretty_name,
)

V = lambda v: {"value": v}  # noqa: E731
LINE = Line(ref="L", name="Tram A", short_name="A", mode="tram")


def _journey(direction, calls, dest=None):
    return {
        "LineRef": V("L"),
        "DirectionRef": V(direction),
        "DestinationRef": V(dest or calls[-1][0]),
        "EstimatedCalls": {
            "EstimatedCall": [
                {"StopPointRef": V(r), "StopPointName": [V(n)]} for r, n in calls
            ]
        },
    }


def _payload(*journeys):
    return {"Siri": {"ServiceDelivery": {"EstimatedTimetableDelivery": [
        {"EstimatedJourneyVersionFrame": [{"EstimatedVehicleJourney": list(journeys)}]}
    ]}}}


def test_branches_and_terminus_names():
    payloads = {
        "0": _payload(
            *[_journey("0", [("a0", "Centre"), ("b0", "Nord"), ("c0", "Branche 1")])] * 5,
            *[_journey("0", [("a0", "Centre"), ("d0", "Branche 2")])] * 5,
        ),
        "1": _payload(
            *[_journey("1", [("c1", "Branche 1"), ("b1", "Nord"), ("a1", "Centre")])] * 5
        ),
    }
    stops = {s.name: s for s in parse_line_stops_from_timetable(payloads, LINE)}
    # « Branche 2 » n'est qu'un terminus : aucun départ, donc pas proposé.
    assert set(stops) == {"Centre", "Nord", "Branche 1"}
    centre = stops["Centre"].directions
    assert centre["0"].label in ("Vers Branche 1 / Branche 2", "Vers Branche 2 / Branche 1")
    # Terminus du sens 1 : pas de départ dans ce sens.
    assert "1" not in centre
    assert stops["Branche 1"].directions["1"].label == "Vers Centre"


def test_marginal_stop_dropped():
    """Courses techniques (ex. sortie de dépôt à « La Vache » sur le tram C)."""
    regular = [
        _journey("0", [("p", "Pyrénées"), ("m", "Milieu"), ("e", "Expos")])
        for _ in range(20)
    ]
    depot = [
        _journey("0", [("v", "La Vache"), ("p", "Pyrénées"), ("m", "Milieu"), ("e", "Expos")])
        for _ in range(2)
    ]
    short = [_journey("0", [("p", "Pyrénées"), ("m", "Milieu")]) for _ in range(1)]
    stops = {
        s.name: s
        for s in parse_line_stops_from_timetable({"0": _payload(*regular, *depot, *short)}, LINE)
    }
    assert "La Vache" not in stops
    # Terminus rare (1 course sur 23) absent du libellé.
    assert stops["Pyrénées"].directions["0"].label == "Vers Expos"


def test_departures_filtering():
    now = datetime.now(UTC)
    iso = lambda m: (now + timedelta(minutes=m)).isoformat()  # noqa: E731

    def visit(direction, dest, minutes, order, journey):
        return {
            "MonitoringRef": V("S"),
            "MonitoredVehicleJourney": {
                "LineRef": V("L"),
                "DirectionRef": V(direction),
                "DestinationRef": V(dest),
                "DestinationName": [V("LA GARDETTE")],
                "FramedVehicleJourneyRef": {"DatedVehicleJourneyRef": journey},
                "MonitoredCall": {"Order": order, "ExpectedDepartureTime": iso(minutes)},
            },
        }

    payload = {"Siri": {"ServiceDelivery": {"StopMonitoringDelivery": [{"MonitoredStopVisit": [
        visit("1", "T", 5, 0, "j1"),
        visit("0", "S", 3, 39, "j2"),  # arrivée au terminus : ignorée
        visit("0", "X", 2, 4, "j3"),  # autre sens : ignoré
    ]}]}}}
    deps = parse_departures(payload, "L", direction_ref="1", monitoring_ref="S")
    assert [d.journey_ref for d in deps] == ["j1"]
    assert deps[0].destination == "La Gardette"
    assert len(merge_departures([deps, deps])) == 1


def test_pretty_name():
    assert pretty_name("HAILLAN-ROSTAND") == "Haillan-Rostand"
    assert pretty_name("FONTAINE D'ARLAC") == "Fontaine d'Arlac"
    assert pretty_name("Pessac Centre") == "Pessac Centre"
