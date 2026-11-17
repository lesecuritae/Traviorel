from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

from .config import MAX_FLIX_FEEDER_DISCOVERED, MAX_FLIX_FEEDER_TARGETS, TZ
from .db import rank_routes
from .feeder_common import _compact_feeder_segment, _departure_lower_bound, _route_duration, _route_price, _station_key
from .feeder_db import _db_routes, _native_split_options
from .airports import AIRPORT_CITY_NAMES, airport_from_station
from .models import ReiseRequest
from .provider_cache import flix_search
from .utils import as_float, as_int, parse_datetime

def _flix_exact_station(stop: Any) -> str | None:
    if not isinstance(stop, dict):
        return None
    station = str(stop.get("station") or "").strip()
    if not station:
        return None
    # Unresolved Flix IDs must never be handed to DB as if they were stations.
    if station.isdigit() or re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}", station):
        return None
    return station


def _flix_airport_search_targets(
    airport_iata: str,
    airport_station: str,
    airport_city: str | None,
    extra: list[str] | None = None,
) -> list[str]:
    """Build provider queries without assuming a particular city or airport."""
    raw = [
        airport_station,
        airport_iata,
        airport_city or "",
        f"{airport_city} Airport" if airport_city else "",
        *(extra or []),
    ]
    targets: list[str] = []
    seen: set[str] = set()
    for value in raw:
        item = str(value or "").strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            targets.append(item)
    return targets[:8]


def _flix_exact_route_stations(route: dict[str, Any], side: str) -> list[str]:
    """Liefert von trvl eindeutig aufgelöste Flix-Endpunkte und Übergabestellen."""
    stops: list[Any] = [route.get(side)]
    for leg in route.get("legs") or []:
        if isinstance(leg, dict):
            stops.append(leg.get(side))
    result: list[str] = []
    seen: set[str] = set()
    for stop in stops:
        station = _flix_exact_station(stop)
        if station and station.casefold() not in seen:
            seen.add(station.casefold())
            result.append(station)
    return result


def _flix_kind_label(route: dict[str, Any]) -> str:
    kind = str(route.get("flix_kind") or route.get("type") or "").casefold()
    if kind == "train":
        return "FlixTrain"
    if kind == "bus":
        return "FlixBus"
    return str(route.get("provider") or "Flix")


def _flix_route_datetime(route: dict[str, Any], key: str) -> datetime | None:
    stop = route.get(key)
    if not isinstance(stop, dict):
        return None
    return parse_datetime(stop.get("time"))


def _compact_flix_feeder_segment(route: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "provider": route.get("provider"),
            "provider_code": "flix",
            "type": route.get("type"),
            "flix_kind": route.get("flix_kind"),
            "departure": route.get("departure"),
            "arrival": route.get("arrival"),
            "duration_minutes": as_int(route.get("duration_minutes")) or None,
            "price": as_float(route.get("price")) or None,
            "currency": route.get("currency") or "EUR",
            "booking_url": route.get("booking_url"),
            "legs": route.get("legs"),
        }.items()
        if value not in (None, "", [])
    }


def _unique_flix_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for route in routes:
        dep = route.get("departure") if isinstance(route.get("departure"), dict) else {}
        arr = route.get("arrival") if isinstance(route.get("arrival"), dict) else {}
        key = (
            route.get("provider"),
            route.get("type"),
            dep.get("station_id") or dep.get("station") or dep.get("city"),
            dep.get("time"),
            arr.get("station_id") or arr.get("station") or arr.get("city"),
            arr.get("time"),
            round(as_float(route.get("price")), 2),
        )
        unique.setdefault(key, route)
    return sorted(
        unique.values(),
        key=lambda route: (
            as_float(route.get("price")) if as_float(route.get("price")) > 0 else 1_000_000,
            as_int(route.get("duration_minutes")) or 1_000_000,
        ),
    )


async def _flix_routes_for_targets(
    origin: str,
    targets: list[str],
    travel_date: str,
    departure_after: str,
    *,
    include_flixbus: bool,
    include_flixtrain: bool,
    max_results: int = 10,
) -> tuple[list[dict[str, Any]], bool]:
    if not (include_flixbus or include_flixtrain):
        return [], False

    clean_targets: list[str] = []
    for raw in targets:
        target = str(raw or "").strip()
        if target and target.casefold() not in {item.casefold() for item in clean_targets}:
            clean_targets.append(target)
    clean_targets = clean_targets[:MAX_FLIX_FEEDER_TARGETS]
    if not clean_targets:
        return [], False

    async def one(target: str) -> dict[str, Any]:
        try:
            return await flix_search(ReiseRequest(
                origin=origin,
                destination=target,
                travel_date=travel_date,
                departure_after=departure_after,
                preference="cheapest",
                include_flixtrain=include_flixtrain,
                include_flixbus=include_flixbus,
                max_transfers=4,
                max_results=max_results,
                split_ticket_check=False,
            ))
        except Exception as exc:
            return {"status": "error", "routes": [], "error": str(exc)}

    results = await asyncio.gather(*(one(target) for target in clean_targets))
    routes: list[dict[str, Any]] = []
    metadata_resolved = False
    for result in results:
        if not isinstance(result, dict):
            continue
        metadata_resolved = metadata_resolved or bool(result.get("station_metadata_resolved"))
        routes.extend(route for route in (result.get("routes") or []) if isinstance(route, dict))

    # Exakte Endpunkte aus den Flix-Segmenten werden nur weiterverwendet, wenn
    # sie sich als eigenständig buchbares Ziel mit eigenen Zeiten und Preisen
    # bestätigen lassen. Ein physischer Halt gilt nicht automatisch als buchbar.
    discovered: list[str] = []
    existing = {item.casefold() for item in clean_targets}
    for route in routes:
        for station in _flix_exact_route_stations(route, "arrival"):
            key = station.casefold()
            if key not in existing and key not in {x.casefold() for x in discovered}:
                discovered.append(station)
        if len(discovered) >= MAX_FLIX_FEEDER_DISCOVERED:
            break
    if discovered:
        verified = await asyncio.gather(*(one(target) for target in discovered[:MAX_FLIX_FEEDER_DISCOVERED]))
        for result in verified:
            if not isinstance(result, dict):
                continue
            metadata_resolved = metadata_resolved or bool(result.get("station_metadata_resolved"))
            routes.extend(route for route in (result.get("routes") or []) if isinstance(route, dict))

    return _unique_flix_routes(routes), metadata_resolved


async def _flix_outbound_feeder_options(
    origin: str,
    airport_iata: str,
    airport_station: str,
    airport_city: str | None,
    handoffs: list[str],
    travel_date: str,
    departure_after: str,
    cutoff: datetime | None,
    transfer_minutes: int,
    *,
    deutschlandticket_available: bool,
    include_flixbus: bool,
    include_flixtrain: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    targets = _flix_airport_search_targets(
        airport_iata,
        airport_station,
        airport_city,
        handoffs,
    )
    routes, metadata_resolved = await _flix_routes_for_targets(
        origin,
        targets,
        travel_date,
        departure_after,
        include_flixbus=include_flixbus,
        include_flixtrain=include_flixtrain,
        max_results=12,
    )

    lower_bound = _departure_lower_bound(travel_date, departure_after)
    usable: list[tuple[dict[str, Any], datetime, datetime, str]] = []
    unresolved = 0
    for route in routes:
        price = as_float(route.get("price"))
        departure = _flix_route_datetime(route, "departure")
        arrival = _flix_route_datetime(route, "arrival")
        station = _flix_exact_station(route.get("arrival"))
        if price <= 0 or not departure or not arrival:
            continue
        if departure < lower_bound:
            continue
        if cutoff and arrival > cutoff:
            continue
        if not station:
            unresolved += 1
            continue
        usable.append((route, departure, arrival, station))

    # Die Zahl paralleler Folgeabfragen bleibt begrenzt; unterschiedliche Terminals bleiben erhalten.
    picked: list[tuple[dict[str, Any], datetime, datetime, str]] = []
    seen_station: set[str] = set()
    for item in sorted(
        usable,
        key=lambda row: (
            0 if airport_from_station(row[3]) == airport_iata else 1,
            as_float(row[0].get("price")),
            row[2],
            row[1],
        ),
    ):
        key = _station_key(item[3])
        if key not in seen_station or len(picked) < 3:
            picked.append(item)
            seen_station.add(key)
        if len(picked) >= MAX_FLIX_FEEDER_TARGETS + 1:
            break

    options: list[dict[str, Any]] = []
    for route, first_departure, first_arrival, handoff in picked:
        flix_price = as_float(route.get("price"))
        kind = _flix_kind_label(route)

        if airport_from_station(handoff) == airport_iata:
            options.append({
                "type": "flix_direct_airport",
                "label": f"{kind} direkt zum Abflughafen",
                "split_station": None,
                "departure": first_departure.isoformat(),
                "arrival": first_arrival.isoformat(),
                "duration_minutes": int((first_arrival - first_departure).total_seconds() // 60),
                "total_price": round(flix_price, 2),
                "price_known": True,
                "currency": route.get("currency") or "EUR",
                "requires_deutschlandticket": False,
                "self_managed_transfers": 0,
                "segments": [_compact_flix_feeder_segment(route)],
                "booking_url": route.get("booking_url"),
            })
            continue

        ready = first_arrival + timedelta(minutes=transfer_minutes)
        second_date = ready.astimezone(TZ).date().isoformat()
        second_after = ready.astimezone(TZ).strftime("%H:%M")

        paid_routes, _, _ = await _db_routes(
            handoff,
            airport_station,
            second_date,
            second_after,
            mode="all",
            results=8,
            arrival_before=cutoff,
            bestprice=False,
            not_only_fast_routes=True,
        )
        for second in rank_routes(paid_routes, "cheapest")[:2]:
            second_departure = parse_datetime(second.get("departure"))
            second_arrival = parse_datetime(second.get("arrival"))
            second_price = _route_price(second)
            if (
                not second_departure
                or not second_arrival
                or second_departure < ready
                or (cutoff and second_arrival > cutoff)
                or second_price <= 0
            ):
                continue
            options.append({
                "type": "flix_plus_db",
                "label": f"{kind} bis {handoff}, danach separates DB-Ticket zum Flughafen",
                "split_station": handoff,
                "departure": first_departure.isoformat(),
                "arrival": second_arrival.astimezone(TZ).isoformat(),
                "duration_minutes": int((second_arrival - first_departure).total_seconds() // 60),
                "total_price": round(flix_price + second_price, 2),
                "price_known": True,
                "currency": second.get("currency") or route.get("currency") or "EUR",
                "requires_deutschlandticket": False,
                "self_managed_transfers": 1,
                "transfer_minutes": int((second_departure - first_arrival).total_seconds() // 60),
                "segments": [
                    _compact_flix_feeder_segment(route),
                    _compact_feeder_segment(second),
                ],
            })

        if deutschlandticket_available:
            dt_routes, _, _ = await _db_routes(
                handoff,
                airport_station,
                second_date,
                second_after,
                mode="deutschlandticket",
                results=6,
                arrival_before=cutoff,
                not_only_fast_routes=True,
            )
            for second in rank_routes(dt_routes, "fastest")[:2]:
                second_departure = parse_datetime(second.get("departure"))
                second_arrival = parse_datetime(second.get("arrival"))
                if (
                    not second_departure
                    or not second_arrival
                    or second_departure < ready
                    or (cutoff and second_arrival > cutoff)
                ):
                    continue
                options.append({
                    "type": "flix_plus_deutschlandticket",
                    "label": f"{kind} bis {handoff}, danach Deutschlandticket zum Flughafen",
                    "split_station": handoff,
                    "departure": first_departure.isoformat(),
                    "arrival": second_arrival.astimezone(TZ).isoformat(),
                    "duration_minutes": int((second_arrival - first_departure).total_seconds() // 60),
                    "total_price": round(flix_price, 2),
                    "price_known": True,
                    "currency": route.get("currency") or "EUR",
                    "requires_deutschlandticket": True,
                    "price_note": "Gesamtpreis enthält nur den Flix-Abschnitt; das vorhandene Deutschlandticket wird mit 0 EUR Zusatzkosten angesetzt.",
                    "self_managed_transfers": 1,
                    "transfer_minutes": int((second_departure - first_arrival).total_seconds() // 60),
                    "segments": [
                        _compact_flix_feeder_segment(route),
                        _compact_feeder_segment(second, deutschlandticket=True),
                    ],
                })

    return options, {
        "enabled": bool(include_flixbus or include_flixtrain),
        "routes_seen": len(routes),
        "exact_station_routes": len(usable),
        "unresolved_station_routes": unresolved,
        "station_metadata_resolved": metadata_resolved,
        "search_targets": targets,
        "exact_stations_seen": sorted({
            station
            for route in routes
            for station in _flix_exact_route_stations(route, "arrival")
        }),
        "airport_stop_detected": any(
            airport_from_station(station) == airport_iata
            for route in routes
            for station in _flix_exact_route_stations(route, "arrival")
        ),
    }


async def _flix_return_feeder_options(
    airport_iata: str,
    airport_station: str,
    airport_city: str | None,
    destination: str,
    handoffs: list[str],
    search_date: str,
    departure_after: str,
    transfer_minutes: int,
    *,
    deutschlandticket_available: bool,
    include_flixbus: bool,
    include_flixtrain: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not (include_flixbus or include_flixtrain):
        return [], {"enabled": False}

    origins = _flix_airport_search_targets(
        airport_iata,
        airport_station,
        airport_city,
        handoffs,
    )
    clean_origins: list[str] = []
    for raw in origins:
        origin = str(raw or "").strip()
        if origin and origin.casefold() not in {item.casefold() for item in clean_origins}:
            clean_origins.append(origin)
    clean_origins = clean_origins[:5]

    async def one(origin: str) -> dict[str, Any]:
        try:
            return await flix_search(ReiseRequest(
                origin=origin,
                destination=destination,
                travel_date=search_date,
                departure_after=departure_after,
                preference="cheapest",
                include_flixtrain=include_flixtrain,
                include_flixbus=include_flixbus,
                max_transfers=4,
                max_results=12,
                split_ticket_check=False,
            ))
        except Exception as exc:
            return {"status": "error", "routes": [], "error": str(exc)}

    results = await asyncio.gather(*(one(origin) for origin in clean_origins))
    all_routes: list[dict[str, Any]] = []
    metadata_resolved = False
    for result in results:
        if isinstance(result, dict):
            metadata_resolved = metadata_resolved or bool(result.get("station_metadata_resolved"))
            all_routes.extend(route for route in (result.get("routes") or []) if isinstance(route, dict))
    routes = _unique_flix_routes(all_routes)

    lower_bound = _departure_lower_bound(search_date, departure_after)
    usable: list[tuple[dict[str, Any], datetime, datetime, str]] = []
    unresolved = 0
    for route in routes:
        price = as_float(route.get("price"))
        departure = _flix_route_datetime(route, "departure")
        arrival = _flix_route_datetime(route, "arrival")
        station = _flix_exact_station(route.get("departure"))
        if price <= 0 or not departure or not arrival:
            continue
        if departure < lower_bound:
            continue
        if not station:
            unresolved += 1
            continue
        usable.append((route, departure, arrival, station))

    picked = sorted(
        usable,
        key=lambda row: (
            0 if airport_from_station(row[3]) == airport_iata else 1,
            as_float(row[0].get("price")),
            row[1],
            row[2],
        ),
    )[:6]
    options: list[dict[str, Any]] = []

    for route, flix_departure, flix_arrival, handoff in picked:
        flix_price = as_float(route.get("price"))
        kind = _flix_kind_label(route)

        if airport_from_station(handoff) == airport_iata:
            options.append({
                "type": "flix_direct_airport_return",
                "label": f"{kind} direkt vom Ankunftsflughafen",
                "split_station": None,
                "departure": flix_departure.isoformat(),
                "arrival": flix_arrival.isoformat(),
                "duration_minutes": int((flix_arrival - flix_departure).total_seconds() // 60),
                "total_price": round(flix_price, 2),
                "price_known": True,
                "currency": route.get("currency") or "EUR",
                "requires_deutschlandticket": False,
                "self_managed_transfers": 0,
                "segments": [_compact_flix_feeder_segment(route)],
                "booking_url": route.get("booking_url"),
            })
            continue

        latest_handoff_arrival = flix_departure - timedelta(minutes=transfer_minutes)

        paid_routes, _, _ = await _db_routes(
            airport_station,
            handoff,
            search_date,
            departure_after,
            mode="all",
            results=8,
            arrival_before=latest_handoff_arrival,
            bestprice=False,
            not_only_fast_routes=True,
        )
        for first in rank_routes(paid_routes, "cheapest")[:2]:
            first_departure = parse_datetime(first.get("departure"))
            first_arrival = parse_datetime(first.get("arrival"))
            first_price = _route_price(first)
            if (
                not first_departure
                or not first_arrival
                or first_departure < lower_bound
                or first_arrival > latest_handoff_arrival
                or first_price <= 0
            ):
                continue
            options.append({
                "type": "db_plus_flix",
                "label": f"DB bis {handoff}, danach {kind}",
                "split_station": handoff,
                "departure": first_departure.astimezone(TZ).isoformat(),
                "arrival": flix_arrival.isoformat(),
                "duration_minutes": int((flix_arrival - first_departure).total_seconds() // 60),
                "total_price": round(first_price + flix_price, 2),
                "price_known": True,
                "currency": first.get("currency") or route.get("currency") or "EUR",
                "requires_deutschlandticket": False,
                "self_managed_transfers": 1,
                "transfer_minutes": int((flix_departure - first_arrival).total_seconds() // 60),
                "segments": [
                    _compact_feeder_segment(first),
                    _compact_flix_feeder_segment(route),
                ],
            })

        if deutschlandticket_available:
            dt_routes, _, _ = await _db_routes(
                airport_station,
                handoff,
                search_date,
                departure_after,
                mode="deutschlandticket",
                results=6,
                arrival_before=latest_handoff_arrival,
                not_only_fast_routes=True,
            )
            for first in rank_routes(dt_routes, "fastest")[:2]:
                first_departure = parse_datetime(first.get("departure"))
                first_arrival = parse_datetime(first.get("arrival"))
                if (
                    not first_departure
                    or not first_arrival
                    or first_departure < lower_bound
                    or first_arrival > latest_handoff_arrival
                ):
                    continue
                options.append({
                    "type": "deutschlandticket_plus_flix",
                    "label": f"Deutschlandticket bis {handoff}, danach {kind}",
                    "split_station": handoff,
                    "departure": first_departure.astimezone(TZ).isoformat(),
                    "arrival": flix_arrival.isoformat(),
                    "duration_minutes": int((flix_arrival - first_departure).total_seconds() // 60),
                    "total_price": round(flix_price, 2),
                    "price_known": True,
                    "currency": route.get("currency") or "EUR",
                    "requires_deutschlandticket": True,
                    "price_note": "Gesamtpreis enthält nur den Flix-Abschnitt; das vorhandene Deutschlandticket wird mit 0 EUR Zusatzkosten angesetzt.",
                    "self_managed_transfers": 1,
                    "transfer_minutes": int((flix_departure - first_arrival).total_seconds() // 60),
                    "segments": [
                        _compact_feeder_segment(first, deutschlandticket=True),
                        _compact_flix_feeder_segment(route),
                    ],
                })

    return options, {
        "enabled": True,
        "routes_seen": len(routes),
        "exact_station_routes": len(usable),
        "unresolved_station_routes": unresolved,
        "station_metadata_resolved": metadata_resolved,
        "search_origins": clean_origins,
        "exact_stations_seen": sorted({
            station
            for route in routes
            for station in _flix_exact_route_stations(route, "departure")
        }),
        "airport_stop_detected": any(
            airport_from_station(station) == airport_iata
            for route in routes
            for station in _flix_exact_route_stations(route, "departure")
        ),
    }


