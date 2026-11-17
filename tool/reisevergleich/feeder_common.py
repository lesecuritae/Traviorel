from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .airports import airport_identity_matches
from .config import MAX_FEEDER_HANDOFFS, TZ
from .db import compact_route
from .utils import as_float, as_int, parse_datetime

def _best_price(options: list[dict[str, Any]], key: str = "price") -> float:
    values = [as_float(item.get(key)) for item in options if as_float(item.get(key)) > 0]
    return min(values) if values else 0.0


def _flight_times(option: dict[str, Any]) -> tuple[datetime | None, datetime | None, datetime | None, datetime | None]:
    outbound = option.get("outbound") if isinstance(option.get("outbound"), dict) else {}
    inbound = option.get("return") if isinstance(option.get("return"), dict) else {}
    return (
        parse_datetime(outbound.get("departure")),
        parse_datetime(outbound.get("arrival")),
        parse_datetime(inbound.get("departure")),
        parse_datetime(inbound.get("arrival")),
    )


def _station_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _route_price(route: dict[str, Any]) -> float:
    price = as_float(route.get("price"))
    split_price = as_float(route.get("best_split_price"))
    values = [value for value in (price, split_price) if value > 0]
    return min(values) if values else 0.0


def _route_duration(route: dict[str, Any]) -> int:
    value = as_int(route.get("duration_minutes"))
    if value > 0:
        return value
    departure = parse_datetime(route.get("departure"))
    arrival = parse_datetime(route.get("arrival"))
    if departure and arrival:
        return max(0, int((arrival - departure).total_seconds() // 60))
    return 0


def _departure_lower_bound(travel_date: str, departure_after: str) -> datetime:
    return datetime.fromisoformat(f"{travel_date}T{departure_after}:00").replace(tzinfo=TZ)


def _selected_price(component: dict[str, Any] | None) -> float:
    if not isinstance(component, dict):
        return 0.0
    if component.get("selected_price") is not None:
        return max(0.0, as_float(component.get("selected_price")))
    selected_option = component.get("selected_option")
    if isinstance(selected_option, dict) and selected_option.get("total_price") is not None:
        return max(0.0, as_float(selected_option.get("total_price")))
    return 0.0


def _feeder_price_known(component: dict[str, Any] | None) -> bool:
    if not isinstance(component, dict):
        return False
    selected_option = component.get("selected_option")
    if isinstance(selected_option, dict) and selected_option.get("price_known") is True:
        return True
    return component.get("selected_price") is not None and component.get("status") == "ok"


def _route_candidate_stations(routes: list[dict[str, Any]], start: str, end: str) -> list[str]:
    blocked = {_station_key(start), _station_key(end)}
    scores: dict[str, tuple[int, str]] = {}

    def add(name: Any, weight: int) -> None:
        station = str(name or "").strip()
        key = _station_key(station)
        if not station or key in blocked:
            return
        bonus = 20 if re.search(r"\b(hbf|hauptbahnhof)\b", station, re.I) else 0
        current = scores.get(key)
        score = weight + bonus + (current[0] if current else 0)
        scores[key] = (score, station)

    for route in routes[:12]:
        legs = [leg for leg in (route.get("legs") or []) if isinstance(leg, dict) and not leg.get("walking")]
        for index, leg in enumerate(legs):
            origin = leg.get("origin") or {}
            destination = leg.get("destination") or {}
            if index > 0:
                add(origin.get("name") if isinstance(origin, dict) else origin, 100)
            if index < len(legs) - 1:
                add(destination.get("name") if isinstance(destination, dict) else destination, 120)

            product = str(leg.get("product") or "").casefold()
            stop_weight = 25 if product in {"national", "nationalexpress"} else 10
            stopovers = [s for s in (leg.get("stopovers") or []) if isinstance(s, dict)]
            for stop_index, stopover in enumerate(stopovers):
                # Endpunkte eines Legs sind bereits über origin/destination abgedeckt.
                if stop_index == 0 or stop_index == len(stopovers) - 1:
                    continue
                add(stopover.get("name"), stop_weight)

    ranked = sorted(scores.values(), key=lambda item: (-item[0], _station_key(item[1])))
    return [station for _, station in ranked[:6]]


def _feeder_handoffs(
    start: str,
    end: str,
    routes: list[dict[str, Any]],
    deutschlandticket_routes: list[dict[str, Any]],
    extra: list[str] | None,
) -> list[str]:
    candidates: list[str] = []
    for station in [*(extra or []), *_route_candidate_stations(routes, start, end), *_route_candidate_stations(deutschlandticket_routes, start, end)]:
        station = str(station).strip()
        if station and _station_key(station) not in {_station_key(item) for item in candidates}:
            candidates.append(station)
    blocked = {_station_key(start), _station_key(end)}
    return [station for station in candidates if _station_key(station) not in blocked][:MAX_FEEDER_HANDOFFS]


def _feeder_option_score(option: dict[str, Any], preference: str) -> tuple[float, ...]:
    price = as_float(option.get("total_price"))
    price_key = max(0.0, price) if option.get("price_known") is True else 1_000_000.0
    duration = as_int(option.get("duration_minutes")) or 1_000_000
    transfers = as_int(option.get("self_managed_transfers"))
    uses_dticket = bool(option.get("requires_deutschlandticket")) or "deutschlandticket" in str(option.get("type") or "")
    if preference == "dticket_first":
        return (0.0 if uses_dticket else 1.0, price_key, duration, transfers)
    if preference == "fastest":
        return (duration, price_key, transfers)
    if preference == "balanced":
        return (price_key + duration * 0.08 + transfers * 5, price_key, duration)
    return (price_key, duration, transfers)




def _uses_deutschlandticket(option: dict[str, Any]) -> bool:
    return bool(option.get("requires_deutschlandticket")) or "deutschlandticket" in str(option.get("type") or "").casefold()


def diverse_feeder_options(options: list[dict[str, Any]], preference: str, limit: int = 8) -> list[dict[str, Any]]:
    """Bewahre D-Ticket-Priorität, ohne günstige oder schnelle Alternativen wegzuschneiden."""
    if not options:
        return []
    ranked = sorted(options, key=lambda item: _feeder_option_score(item, preference))
    candidates: list[dict[str, Any] | None] = [
        ranked[0],
        min(options, key=lambda item: _feeder_option_score(item, "cheapest"), default=None),
        min(options, key=lambda item: _feeder_option_score(item, "fastest"), default=None),
        min((item for item in options if _uses_deutschlandticket(item)), key=lambda item: _feeder_option_score(item, "cheapest"), default=None),
        min((item for item in options if not _uses_deutschlandticket(item)), key=lambda item: _feeder_option_score(item, "cheapest"), default=None),
    ]
    candidates.extend(ranked)
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        key = (
            item.get("type"), item.get("split_station"), item.get("departure"),
            item.get("arrival"), round(as_float(item.get("total_price")), 2),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result



def _terminal_numbers(text: str) -> set[int]:
    """Extrahiere explizit genannte Terminalnummern aus Stationsnamen."""
    value = str(text or "")
    numbers: set[int] = set()
    for match in re.finditer(r"(?i)\b(?:terminal|t)\s*([0-9]+)(?:\s*[-–/]\s*([0-9]+))?", value):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if 0 < start <= 20 and 0 < end <= 20:
            lo, hi = sorted((start, end))
            numbers.update(range(lo, hi + 1))
    return numbers


def _option_endpoint_text(option: dict[str, Any], side: str) -> str:
    """Liefere den tatsächlich genutzten Start- oder Zielbahnhof einer Zubringeroption."""
    segments = [item for item in (option.get("segments") or []) if isinstance(item, dict)]
    if not segments:
        return ""
    segment = segments[0] if side == "origin" else segments[-1]
    value = segment.get(side)
    if isinstance(value, str) and value.strip():
        return value.strip()
    legs = [item for item in (segment.get("legs") or []) if isinstance(item, dict)]
    if legs:
        leg = legs[0] if side == "origin" else legs[-1]
        value = leg.get(side)
        if isinstance(value, str):
            return value.strip()
    return ""


def _terminal_matches(requested_station: str, candidate_station: str) -> bool:
    """Flughafenidentität ist hart; Terminalnummern werden erst danach verglichen."""
    if not airport_identity_matches(requested_station, candidate_station):
        return False
    requested = _terminal_numbers(requested_station)
    candidate = _terminal_numbers(candidate_station)
    if not requested or not candidate:
        return True
    return bool(requested & candidate)


def valid_feeder_options(
    options: list[dict[str, Any]],
    *,
    lower_bound: datetime | None = None,
    arrival_cutoff: datetime | None = None,
    expected_origin_station: str | None = None,
    expected_destination_station: str | None = None,
) -> list[dict[str, Any]]:
    """Zentrale harte Plausibilitätsgrenze vor Ranking und Präsentation."""
    valid: list[dict[str, Any]] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        departure = parse_datetime(option.get("departure"))
        arrival = parse_datetime(option.get("arrival"))
        if lower_bound and not departure:
            continue
        if lower_bound and departure < lower_bound:
            continue
        if arrival_cutoff and not arrival:
            continue
        if arrival_cutoff and arrival > arrival_cutoff:
            continue
        if expected_origin_station:
            actual_origin = _option_endpoint_text(option, "origin")
            if actual_origin and not _terminal_matches(expected_origin_station, actual_origin):
                continue
        if expected_destination_station:
            actual_destination = _option_endpoint_text(option, "destination")
            if actual_destination and not _terminal_matches(expected_destination_station, actual_destination):
                continue
        valid.append(option)
    return valid

def _compact_feeder_segment(route: dict[str, Any], *, deutschlandticket: bool = False) -> dict[str, Any]:
    compact = compact_route(route)
    if deutschlandticket:
        compact["ticket"] = "Deutschlandticket"
        compact["requires_deutschlandticket"] = True
        compact["paid_price"] = 0.0
    return compact


