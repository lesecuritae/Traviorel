from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from .config import TZ
from .db import build_manual_db_links, compact_attempts, compact_route, rank_routes
from .feeder_common import (
    _best_price, _compact_feeder_segment, _departure_lower_bound, _feeder_handoffs,
    _feeder_option_score, diverse_feeder_options, _feeder_price_known, _flight_times, _route_duration,
    _route_price, _selected_price, valid_feeder_options,
)
from .feeder_db import _db_routes, _native_split_options, _outbound_via_options, _return_via_options
from .feeder_flix import _flix_outbound_feeder_options, _flix_return_feeder_options
from .airports import AIRPORT_CITY_NAMES
from .models import ReiseRequest
from .provider_cache import transitous_search
from .utils import as_float, as_int, local_clock, parse_datetime


async def _transitous_dticket_feeder_options(
    origin: str,
    destination: str,
    travel_date: str,
    departure_after: str,
    *,
    label: str,
    lower_bound: datetime,
    arrival_cutoff: datetime | None = None,
    expected_origin_station: str | None = None,
    expected_destination_station: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Nahverkehrs-Fallback, wenn DB im D-Ticket-Modus keine Route liefert."""
    request = ReiseRequest(
        origin=origin,
        destination=destination,
        travel_date=travel_date,
        departure_after=departure_after,
        preference="fastest",
        include_flixtrain=False,
        include_flixbus=False,
        max_transfers=6,
        max_results=8,
        split_ticket_check=False,
    )
    try:
        result = await transitous_search(request, deutschlandticket_only=True)
    except Exception as exc:
        return [], {"ok": False, "source": "transitous", "error": f"{type(exc).__name__}: {exc}"}

    options: list[dict[str, Any]] = []
    for route in (result.get("routes") or [])[:8]:
        if not isinstance(route, dict):
            continue
        departure = parse_datetime(route.get("departure"))
        arrival = parse_datetime(route.get("arrival"))
        options.append({
            "type": "deutschlandticket_transitous",
            "label": label,
            "departure": departure.astimezone(TZ).isoformat() if departure else None,
            "arrival": arrival.astimezone(TZ).isoformat() if arrival else None,
            "duration_minutes": _route_duration(route),
            "total_price": 0.0,
            "price_known": True,
            "currency": "EUR",
            "requires_deutschlandticket": True,
            "deutschlandticket_tariff_guaranteed": False,
            "price_note": (
                "0 EUR bedeutet 0 EUR zusätzliche Ticketkosten bei vorhandenem Deutschlandticket, "
                "sofern die konkrete Verbindung tariflich vom Deutschlandticket abgedeckt ist. "
                "Transitous filtert hier nur Nahverkehrsmodi und liefert keine Tarifgarantie."
            ),
            "self_managed_transfers": 0,
            "segments": [_compact_feeder_segment(route, deutschlandticket=True)],
        })
    options = valid_feeder_options(
        options,
        lower_bound=lower_bound,
        arrival_cutoff=arrival_cutoff,
        expected_origin_station=expected_origin_station,
        expected_destination_station=expected_destination_station,
    )
    diagnostic = result.get("diagnostic") if isinstance(result, dict) else None
    return options[:3], diagnostic


async def feeder_outbound(
    origin: str,
    airport_iata: str,
    airport_station: str,
    travel_date: str,
    departure_after: str,
    flight_departure: datetime | None,
    buffer_minutes: int,
    *,
    preference: str = "cheapest",
    deutschlandticket_available: bool = True,
    split_candidates: list[str] | None = None,
    transfer_minutes: int = 15,
    split_ticket_check: bool = True,
    include_flixbus: bool = True,
    include_flixtrain: bool = True,
) -> dict[str, Any]:
    cutoff = flight_departure - timedelta(minutes=buffer_minutes) if flight_departure else None
    direct_routes, attempts, source = await _db_routes(
        origin, airport_station, travel_date, departure_after,
        mode="all", results=12, arrival_before=cutoff,
        bestprice=False, not_only_fast_routes=True,
    )

    lower_bound = _departure_lower_bound(travel_date, departure_after)
    valid_direct = []
    for route in direct_routes:
        departure = parse_datetime(route.get("departure"))
        arrival = parse_datetime(route.get("arrival"))
        if departure and departure < lower_bound:
            continue
        if cutoff and arrival and arrival > cutoff:
            continue
        valid_direct.append(route)

    options: list[dict[str, Any]] = []
    # Provider-Reihenfolge ist keine Qualitätsreihenfolge. Erst alle gültigen
    # bepreisten Routen erhalten, dann nach Preis ranken und begrenzen. Sonst
    # können spätere echte DB-Preise hinter unbepreisten Zeilen verschwinden.
    priced_direct = [route for route in valid_direct if as_float(route.get("price")) > 0]
    for route in sorted(
        priced_direct,
        key=lambda item: (as_float(item.get("price")), _route_duration(item)),
    )[:6]:
        price = as_float(route.get("price"))
        options.append({
            "type": "db_direct",
            "label": "Durchgehendes DB-Ticket",
            "departure": parse_datetime(route.get("departure")).astimezone(TZ).isoformat()
            if parse_datetime(route.get("departure")) else None,
            "arrival": parse_datetime(route.get("arrival")).astimezone(TZ).isoformat()
            if parse_datetime(route.get("arrival")) else None,
            "duration_minutes": _route_duration(route),
            "total_price": round(price, 2),
            "price_known": True,
            "currency": route.get("currency") or "EUR",
            "fare_name": route.get("fare_name"),
            "price_source": route.get("price_source"),
            "self_managed_transfers": 0,
            "segments": [compact_route(route)],
            "booking_url": route.get("booking_url"),
        })

    dt_routes: list[dict[str, Any]] = []
    if deutschlandticket_available:
        dt_routes, _, _ = await _db_routes(
            origin, airport_station, travel_date, departure_after,
            mode="deutschlandticket", results=8, arrival_before=cutoff,
            not_only_fast_routes=True,
        )
        valid_dt = []
        for route in dt_routes:
            departure = parse_datetime(route.get("departure"))
            arrival = parse_datetime(route.get("arrival"))
            if departure and departure < lower_bound:
                continue
            if cutoff and arrival and arrival > cutoff:
                continue
            valid_dt.append(route)
        for route in rank_routes(valid_dt if cutoff else dt_routes, "fastest")[:3]:
            departure = parse_datetime(route.get("departure"))
            arrival = parse_datetime(route.get("arrival"))
            options.append({
                "type": "deutschlandticket_direct",
                "label": "Kompletter Bahn-Zubringer mit Deutschlandticket",
                "departure": departure.astimezone(TZ).isoformat() if departure else None,
                "arrival": arrival.astimezone(TZ).isoformat() if arrival else None,
                "duration_minutes": _route_duration(route),
                "total_price": 0.0,
                "price_known": True,
                "currency": "EUR",
                "requires_deutschlandticket": True,
                "price_note": "0 EUR bedeutet 0 EUR zusätzliche Ticketkosten bei bereits vorhandenem Deutschlandticket.",
                "self_managed_transfers": 0,
                "segments": [_compact_feeder_segment(route, deutschlandticket=True)],
            })

    dticket_fallback_diagnostic = None

    if split_ticket_check and valid_direct:
        options.extend(await _native_split_options(valid_direct))

    handoffs = _feeder_handoffs(origin, airport_station, valid_direct, valid_dt if deutschlandticket_available else [], split_candidates)
    if handoffs:
        db_via_results = await asyncio.gather(*(
            _outbound_via_options(
                origin, airport_station, handoff, travel_date, departure_after, cutoff,
                transfer_minutes, deutschlandticket_first=False,
            )
            for handoff in handoffs
        ), return_exceptions=True)
        for result in db_via_results:
            if isinstance(result, list):
                options.extend(result)

    if deutschlandticket_available and handoffs:
        mixed_results = await asyncio.gather(*(
            _outbound_via_options(
                origin, airport_station, handoff, travel_date, departure_after, cutoff,
                transfer_minutes, deutschlandticket_first=True,
            )
            for handoff in handoffs
        ), return_exceptions=True)
        for result in mixed_results:
            if isinstance(result, list):
                options.extend(result)

    flix_options, flix_diagnostics = await _flix_outbound_feeder_options(
        origin,
        airport_iata,
        airport_station,
        AIRPORT_CITY_NAMES.get(airport_iata),
        handoffs,
        travel_date,
        departure_after,
        cutoff,
        transfer_minutes,
        deutschlandticket_available=deutschlandticket_available,
        include_flixbus=include_flixbus,
        include_flixtrain=include_flixtrain,
    )
    options.extend(flix_options)

    options = valid_feeder_options(
        options,
        lower_bound=lower_bound,
        arrival_cutoff=cutoff,
        expected_destination_station=airport_station,
    )

    # Erst nach der endgültigen Zeit-/Terminalprüfung entscheiden, ob ein
    # D-Ticket-Fallback nötig ist. Eine später verworfene DB-D-Ticket-Route
    # darf den Transitous-Fallback nicht unterdrücken.
    if deutschlandticket_available and not any(
        option.get("requires_deutschlandticket") is True for option in options
    ):
        fallback_options, dticket_fallback_diagnostic = await _transitous_dticket_feeder_options(
            origin, airport_station, travel_date, departure_after,
            label="Nahverkehrs-Zubringer als Deutschlandticket-Kandidat",
            lower_bound=lower_bound,
            arrival_cutoff=cutoff,
            expected_destination_station=airport_station,
        )
        options.extend(fallback_options)
        options = valid_feeder_options(
            options,
            lower_bound=lower_bound,
            arrival_cutoff=cutoff,
            expected_destination_station=airport_station,
        )

    direct_prices = [as_float(item.get("total_price")) for item in options if item.get("type") == "db_direct" and as_float(item.get("total_price")) > 0]
    if direct_prices:
        cheapest_direct = min(direct_prices)
        options = [item for item in options if item.get("type") != "db_via_split" or as_float(item.get("total_price")) < cheapest_direct]

    # Deduplizieren nach Typ, Übergabebahnhof, Zeiten und Preis.
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for option in options:
        key = (
            option.get("type"),
            option.get("split_station"),
            option.get("departure"),
            option.get("arrival"),
            round(as_float(option.get("total_price")), 2),
        )
        unique.setdefault(key, option)
    ranked_options = diverse_feeder_options(list(unique.values()), preference, limit=8)
    selected = ranked_options[0] if ranked_options else None

    transitous = None
    if not selected and not direct_routes:
        req = ReiseRequest(
            origin=origin,
            destination=airport_station,
            travel_date=travel_date,
            departure_after=departure_after,
            preference="fastest",
            include_flixtrain=False,
            include_flixbus=False,
            max_transfers=5,
            max_results=8,
            split_ticket_check=False,
        )
        transitous = await transitous_search(req)

    links = build_manual_db_links(origin, airport_station, travel_date, departure_after)
    return {
        "status": "ok" if selected else "manual_required",
        "direction": "home_to_airport",
        "airport_iata": airport_iata,
        "airport_station": airport_station,
        "direct_db_query": {
            "origin": origin,
            "destination": airport_station,
            "forced_intermediate": None,
        },
        "source": source,
        "required_arrival_before": cutoff.isoformat() if cutoff else None,
        "timing_verified_against_flight": bool(cutoff),
        "buffer_minutes": buffer_minutes,
        "preference": preference,
        "selected_price": round(max(0.0, as_float(selected.get("total_price"))), 2) if selected else None,
        "selected_option": selected,
        "alternatives": ranked_options[1:8] if selected else [],
        "routes": [selected] if selected else [],
        "db_attempts": compact_attempts(attempts),
        "transitous": transitous.get("diagnostic") if isinstance(transitous, dict) else None,
        "manual_db_links": links,
        "split_candidates_checked": handoffs,
        "deutschlandticket_considered": deutschlandticket_available,
        "deutschlandticket_fallback": dticket_fallback_diagnostic,
        "flix_considered": flix_diagnostics,
    }


async def feeder_return(
    airport_iata: str,
    airport_station: str,
    destination: str,
    return_date: str,
    flight_arrival: datetime | None,
    buffer_minutes: int,
    *,
    preference: str = "cheapest",
    deutschlandticket_available: bool = True,
    split_candidates: list[str] | None = None,
    transfer_minutes: int = 15,
    split_ticket_check: bool = True,
    include_flixbus: bool = True,
    include_flixtrain: bool = True,
) -> dict[str, Any]:
    earliest = flight_arrival + timedelta(minutes=buffer_minutes) if flight_arrival else None
    search_date = return_date
    departure_after = "00:00"
    if earliest:
        local = earliest.astimezone(TZ)
        search_date = local.date().isoformat()
        departure_after = local.strftime("%H:%M")

    direct_routes, attempts, source = await _db_routes(
        airport_station, destination, search_date, departure_after,
        mode="all", results=12, bestprice=False, not_only_fast_routes=True,
    )
    lower_bound = _departure_lower_bound(search_date, departure_after)
    valid_direct = [
        route for route in direct_routes
        if not parse_datetime(route.get("departure"))
        or parse_datetime(route.get("departure")) >= lower_bound
    ]
    options: list[dict[str, Any]] = []
    # Provider-Reihenfolge ist keine Qualitätsreihenfolge. Erst alle gültigen
    # bepreisten Routen erhalten, dann nach Preis ranken und begrenzen. Sonst
    # können spätere echte DB-Preise hinter unbepreisten Zeilen verschwinden.
    priced_direct = [route for route in valid_direct if as_float(route.get("price")) > 0]
    for route in sorted(
        priced_direct,
        key=lambda item: (as_float(item.get("price")), _route_duration(item)),
    )[:6]:
        price = as_float(route.get("price"))
        options.append({
            "type": "db_direct",
            "label": "Durchgehendes DB-Ticket",
            "departure": parse_datetime(route.get("departure")).astimezone(TZ).isoformat()
            if parse_datetime(route.get("departure")) else None,
            "arrival": parse_datetime(route.get("arrival")).astimezone(TZ).isoformat()
            if parse_datetime(route.get("arrival")) else None,
            "duration_minutes": _route_duration(route),
            "total_price": round(price, 2),
            "price_known": True,
            "currency": route.get("currency") or "EUR",
            "fare_name": route.get("fare_name"),
            "price_source": route.get("price_source"),
            "self_managed_transfers": 0,
            "segments": [compact_route(route)],
            "booking_url": route.get("booking_url"),
        })

    dt_routes: list[dict[str, Any]] = []
    if deutschlandticket_available:
        dt_routes, _, _ = await _db_routes(
            airport_station, destination, search_date, departure_after,
            mode="deutschlandticket", results=8, not_only_fast_routes=True,
        )
        valid_dt = [
            route for route in dt_routes
            if not parse_datetime(route.get("departure"))
            or parse_datetime(route.get("departure")) >= lower_bound
        ]
        for route in rank_routes(valid_dt, "fastest")[:3]:
            departure = parse_datetime(route.get("departure"))
            arrival = parse_datetime(route.get("arrival"))
            options.append({
                "type": "deutschlandticket_direct",
                "label": "Komplette Bahn-Rückfahrt mit Deutschlandticket",
                "departure": departure.astimezone(TZ).isoformat() if departure else None,
                "arrival": arrival.astimezone(TZ).isoformat() if arrival else None,
                "duration_minutes": _route_duration(route),
                "total_price": 0.0,
                "price_known": True,
                "currency": "EUR",
                "requires_deutschlandticket": True,
                "price_note": "0 EUR bedeutet 0 EUR zusätzliche Ticketkosten bei bereits vorhandenem Deutschlandticket.",
                "self_managed_transfers": 0,
                "segments": [_compact_feeder_segment(route, deutschlandticket=True)],
            })

    dticket_fallback_diagnostic = None
    if deutschlandticket_available and not any(
        option.get("requires_deutschlandticket") is True for option in options
    ):
        fallback_options, dticket_fallback_diagnostic = await _transitous_dticket_feeder_options(
            airport_station, destination, search_date, departure_after,
            label="Nahverkehrs-Rückfahrt als Deutschlandticket-Kandidat",
            lower_bound=lower_bound,
            expected_origin_station=airport_station,
        )
        options.extend(fallback_options)

    if split_ticket_check and valid_direct:
        options.extend(await _native_split_options(valid_direct))

    handoffs = _feeder_handoffs(airport_station, destination, valid_direct, valid_dt if deutschlandticket_available else [], split_candidates)
    if handoffs:
        db_via_results = await asyncio.gather(*(
            _return_via_options(
                airport_station, destination, handoff, search_date, departure_after,
                transfer_minutes, deutschlandticket_last=False,
            )
            for handoff in handoffs
        ), return_exceptions=True)
        for result in db_via_results:
            if isinstance(result, list):
                options.extend(result)

    if deutschlandticket_available and handoffs:
        mixed_results = await asyncio.gather(*(
            _return_via_options(
                airport_station, destination, handoff, search_date, departure_after,
                transfer_minutes, deutschlandticket_last=True,
            )
            for handoff in handoffs
        ), return_exceptions=True)
        for result in mixed_results:
            if isinstance(result, list):
                options.extend(result)

    flix_options, flix_diagnostics = await _flix_return_feeder_options(
        airport_iata,
        airport_station,
        AIRPORT_CITY_NAMES.get(airport_iata),
        destination,
        handoffs,
        search_date,
        departure_after,
        transfer_minutes,
        deutschlandticket_available=deutschlandticket_available,
        include_flixbus=include_flixbus,
        include_flixtrain=include_flixtrain,
    )
    options.extend(flix_options)

    options = valid_feeder_options(
        options,
        lower_bound=lower_bound,
        expected_origin_station=airport_station,
    )

    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for option in options:
        key = (
            option.get("type"),
            option.get("split_station"),
            option.get("departure"),
            option.get("arrival"),
            round(as_float(option.get("total_price")), 2),
        )
        unique.setdefault(key, option)
    ranked_options = diverse_feeder_options(list(unique.values()), preference, limit=8)
    selected = ranked_options[0] if ranked_options else None

    transitous = None
    if not selected and not direct_routes:
        req = ReiseRequest(
            origin=airport_station,
            destination=destination,
            travel_date=search_date,
            departure_after=departure_after,
            preference="fastest",
            include_flixtrain=False,
            include_flixbus=False,
            max_transfers=5,
            max_results=8,
            split_ticket_check=False,
        )
        transitous = await transitous_search(req)

    links = build_manual_db_links(airport_station, destination, search_date, departure_after)
    return {
        "status": "ok" if selected else "manual_required",
        "direction": "airport_to_home",
        "airport_iata": airport_iata,
        "airport_station": airport_station,
        "direct_db_query": {
            "origin": airport_station,
            "destination": destination,
            "forced_intermediate": None,
        },
        "source": source,
        "earliest_departure": earliest.isoformat() if earliest else None,
        "timing_verified_against_flight": bool(earliest),
        "buffer_minutes": buffer_minutes,
        "preference": preference,
        "selected_price": round(max(0.0, as_float(selected.get("total_price"))), 2) if selected else None,
        "selected_option": selected,
        "alternatives": ranked_options[1:8] if selected else [],
        "routes": [selected] if selected else [],
        "db_attempts": compact_attempts(attempts),
        "transitous": transitous.get("diagnostic") if isinstance(transitous, dict) else None,
        "manual_db_links": links,
        "split_candidates_checked": handoffs,
        "deutschlandticket_considered": deutschlandticket_available,
        "deutschlandticket_fallback": dticket_fallback_diagnostic,
        "flix_considered": flix_diagnostics,
    }


async def _probe_outbound_feeder(
    origin: str,
    airport_station: str,
    travel_date: str,
    departure_after: str,
    flight_departure: datetime | None,
    buffer_minutes: int,
    *,
    deutschlandticket_available: bool = False,
) -> dict[str, Any]:
    cutoff = flight_departure - timedelta(minutes=buffer_minutes) if flight_departure else None
    lower_bound = _departure_lower_bound(travel_date, departure_after)
    routes, _, _ = await _db_routes(
        origin, airport_station, travel_date, departure_after,
        mode="all", results=12, arrival_before=cutoff,
        bestprice=False, not_only_fast_routes=True,
    )
    valid = []
    for route in routes:
        departure = parse_datetime(route.get("departure"))
        arrival = parse_datetime(route.get("arrival"))
        if departure and departure < lower_bound:
            continue
        if cutoff and arrival and arrival > cutoff:
            continue
        valid.append(route)
    ranked = rank_routes(valid if cutoff else routes, "cheapest")
    best = ranked[0] if ranked else None

    dt_best = None
    if deutschlandticket_available:
        dt_routes, _, _ = await _db_routes(
            origin, airport_station, travel_date, departure_after,
            mode="deutschlandticket", results=8, arrival_before=cutoff,
            not_only_fast_routes=True,
        )
        valid_dt = []
        for route in dt_routes:
            departure = parse_datetime(route.get("departure"))
            arrival = parse_datetime(route.get("arrival"))
            if departure and departure < lower_bound:
                continue
            if cutoff and arrival and arrival > cutoff:
                continue
            valid_dt.append(route)
        ranked_dt = rank_routes(valid_dt if cutoff else dt_routes, "fastest")
        dt_best = ranked_dt[0] if ranked_dt else None

    if dt_best is not None:
        return {
            "compatible": True,
            "price": 0.0,
            "duration_minutes": _route_duration(dt_best),
            "route": compact_route(dt_best),
            "required_arrival_before": cutoff.isoformat() if cutoff else None,
            "probe_basis": "deutschlandticket_direct",
        }

    return {
        "compatible": bool(best),
        "price": _route_price(best) if best else 0.0,
        "duration_minutes": _route_duration(best) if best else 0,
        "route": compact_route(best) if best else None,
        "required_arrival_before": cutoff.isoformat() if cutoff else None,
        "probe_basis": "db_direct",
    }


async def _probe_return_feeder(
    airport_station: str,
    destination: str,
    return_date: str,
    flight_arrival: datetime | None,
    buffer_minutes: int,
    *,
    deutschlandticket_available: bool = False,
) -> dict[str, Any]:
    earliest = flight_arrival + timedelta(minutes=buffer_minutes) if flight_arrival else None
    search_date = return_date
    departure_after = "00:00"
    if earliest:
        local = earliest.astimezone(TZ)
        search_date = local.date().isoformat()
        departure_after = local.strftime("%H:%M")
    lower_bound = _departure_lower_bound(search_date, departure_after)
    routes, _, _ = await _db_routes(
        airport_station, destination, search_date, departure_after,
        mode="all", results=12, bestprice=False, not_only_fast_routes=True,
    )
    valid = [
        route for route in routes
        if not parse_datetime(route.get("departure"))
        or parse_datetime(route.get("departure")) >= lower_bound
    ]
    ranked = rank_routes(valid, "cheapest")
    best = ranked[0] if ranked else None

    dt_best = None
    if deutschlandticket_available:
        dt_routes, _, _ = await _db_routes(
            airport_station, destination, search_date, departure_after,
            mode="deutschlandticket", results=8, not_only_fast_routes=True,
        )
        valid_dt = [
            route for route in dt_routes
            if not parse_datetime(route.get("departure"))
            or parse_datetime(route.get("departure")) >= lower_bound
        ]
        ranked_dt = rank_routes(valid_dt, "fastest")
        dt_best = ranked_dt[0] if ranked_dt else None

    if dt_best is not None:
        return {
            "compatible": True,
            "price": 0.0,
            "duration_minutes": _route_duration(dt_best),
            "route": compact_route(dt_best),
            "earliest_departure": earliest.isoformat() if earliest else None,
            "probe_basis": "deutschlandticket_direct",
        }

    return {
        "compatible": bool(best),
        "price": _route_price(best) if best else 0.0,
        "duration_minutes": _route_duration(best) if best else 0,
        "route": compact_route(best) if best else None,
        "earliest_departure": earliest.isoformat() if earliest else None,
        "probe_basis": "db_direct",
    }


def _flight_probe_score(item: dict[str, Any], needs_return: bool) -> tuple[int, float, int]:
    outbound = item.get("outbound_probe") or {}
    inbound = item.get("return_probe") or {}
    missing = 0
    if not outbound.get("compatible"):
        missing += 1
    if needs_return and not inbound.get("compatible"):
        missing += 1
    flight_price = as_float((item.get("flight") or {}).get("price"))
    known = flight_price + as_float(outbound.get("price")) + as_float(inbound.get("price"))
    if known <= 0:
        known = 1_000_000.0
    dep = parse_datetime((item.get("flight") or {}).get("outbound", {}).get("departure"))
    dep_minutes = dep.hour * 60 + dep.minute if dep else 1_000_000
    return missing, known, dep_minutes


