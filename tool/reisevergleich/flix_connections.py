from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from .config import TZ
from .db import rank_routes
from .models import ReiseRequest
from .provider_cache import db_search_with_retry, transitous_search
from .utils import as_float, as_int, parse_datetime, route_departure_in_window
from .trvl import _normalized_station_name

TRANSFER_MINUTES = 30


def _plausible_detour(total_minutes: int, direct_minutes: int) -> bool:
    """Permit useful hubs while rejecting gross detours without city-specific rules."""
    if total_minutes <= 0 or direct_minutes <= 0:
        return True
    return total_minutes <= max(int(direct_minutes * 2.5), direct_minutes + 240)


def _route_time(route: dict[str, Any], side: str):
    value = route.get(side)
    if isinstance(value, dict):
        value = value.get("time")
    return parse_datetime(value)


def _segment(route: dict[str, Any], *, kind: str, covered: bool = False) -> dict[str, Any]:
    return {
        key: value for key, value in {
            "kind": kind,
            "provider": route.get("provider"),
            "departure": route.get("departure"),
            "arrival": route.get("arrival"),
            "duration_minutes": route.get("duration_minutes"),
            "price": 0.0 if covered else route.get("price"),
            "currency": route.get("currency"),
            "deutschlandticket_covered": covered or None,
            "legs": route.get("legs"),
        }.items() if value not in (None, "", [])
    }


async def _local_candidates(
    origin: str,
    destination: str,
    travel_date: str,
    departure_after: str,
    request: ReiseRequest,
    *,
    arrival_before=None,
) -> list[dict[str, Any]]:
    mode = "deutschlandticket" if request.deutschlandticket else "all"
    db_task = asyncio.create_task(db_search_with_retry(
        origin=origin,
        destination=destination,
        travel_date=travel_date,
        departure_after=departure_after,
        mode=mode,
        max_transfers=request.max_transfers,
        results=5,
        arrival_before=arrival_before,
        not_only_fast_routes=True,
    ))
    local_request = ReiseRequest(
        origin=origin,
        destination=destination,
        travel_date=travel_date,
        departure_after=departure_after,
        preference="fastest",
        include_flixtrain=False,
        include_flixbus=False,
        max_transfers=request.max_transfers,
        max_results=5,
        split_ticket_check=False,
        deutschlandticket=request.deutschlandticket,
    )
    transit_task = asyncio.create_task(transitous_search(
        local_request, deutschlandticket_only=request.deutschlandticket,
    ))
    try:
        db_result, _ = await db_task
        db_routes = db_result.get("journeys") or []
    except Exception:
        db_routes = []
    try:
        transit_result = await transit_task
        transit_routes = transit_result.get("routes") or []
    except Exception:
        transit_routes = []
    routes = db_routes or transit_routes

    def endpoint_name(route: dict[str, Any], side: str) -> str:
        legs = [leg for leg in (route.get("legs") or []) if isinstance(leg, dict)]
        if legs:
            endpoint = legs[0].get("origin") if side == "origin" else legs[-1].get("destination")
            if isinstance(endpoint, dict):
                return str(endpoint.get("name") or "")
            if isinstance(endpoint, str):
                return endpoint
        value = route.get("origin") if side == "origin" else route.get("destination")
        if isinstance(value, dict):
            return str(value.get("name") or value.get("station") or "")
        return str(value or "")

    expected_origin = _normalized_station_name(origin)
    expected_destination = _normalized_station_name(destination)
    routes = [route for route in routes if (
        not _normalized_station_name(endpoint_name(route, "origin"))
        or _normalized_station_name(endpoint_name(route, "origin")) == expected_origin
    ) and (
        not _normalized_station_name(endpoint_name(route, "destination"))
        or _normalized_station_name(endpoint_name(route, "destination")) == expected_destination
    )]
    routes = [
        route for route in routes
        if route_departure_in_window(route, travel_date, departure_after)
        and (arrival_before is None or (_route_time(route, "arrival") and _route_time(route, "arrival") <= arrival_before))
    ]
    return rank_routes(routes, "fastest")


async def complete_flix_route(route: dict[str, Any], request: ReiseRequest) -> dict[str, Any] | None:
    access_requirement = route.get("departure_access")
    egress_requirement = route.get("arrival_egress")
    if not access_requirement and not egress_requirement:
        return route
    if any(
        isinstance(requirement, dict) and requirement.get("status") == "unresolved_stop"
        for requirement in (access_requirement, egress_requirement)
    ):
        return None

    flix_departure = _route_time(route, "departure")
    flix_arrival = _route_time(route, "arrival")
    if not flix_departure or not flix_arrival:
        return None

    access = None
    if isinstance(access_requirement, dict):
        actual = str(access_requirement.get("actual_flix_stop") or "").strip()
        if not actual:
            return None
        cutoff = flix_departure - timedelta(minutes=TRANSFER_MINUTES)
        access_after = request.departure_after
        if access_requirement.get("same_city") is True and cutoff.astimezone(TZ).date().isoformat() == request.travel_date:
            near_connection = (cutoff - timedelta(hours=1)).astimezone(TZ).strftime("%H:%M")
            access_after = max(access_after, near_connection)
        candidates = await _local_candidates(
            request.origin, actual, request.travel_date, access_after,
            request, arrival_before=cutoff,
        )
        access = max(candidates, key=lambda item: _route_time(item, "arrival")) if candidates else None
        if not access or not _route_time(access, "departure") or not _route_time(access, "arrival"):
            return None
        if _route_time(access, "arrival") + timedelta(minutes=TRANSFER_MINUTES) > flix_departure:
            return None

    egress = None
    if isinstance(egress_requirement, dict):
        actual = str(egress_requirement.get("actual_flix_stop") or "").strip()
        if not actual:
            return None
        ready = flix_arrival + timedelta(minutes=TRANSFER_MINUTES)
        egress_date = ready.astimezone(TZ).date().isoformat()
        egress_after = ready.astimezone(TZ).strftime("%H:%M")
        candidates = await _local_candidates(
            actual, request.destination, egress_date, egress_after, request,
        )
        egress = candidates[0] if candidates else None
        if not egress or not _route_time(egress, "departure") or not _route_time(egress, "arrival"):
            return None
        if _route_time(egress, "departure") < ready:
            return None

    output = dict(route)
    segments: list[dict[str, Any]] = []
    if access:
        segments.append(_segment(access, kind="access", covered=request.deutschlandticket))
    segments.append(_segment(route, kind="flix"))
    if egress:
        segments.append(_segment(egress, kind="egress", covered=request.deutschlandticket))

    departure = _route_time(access, "departure") if access else flix_departure
    arrival = _route_time(egress, "arrival") if egress else flix_arrival
    if not departure or not arrival or departure >= arrival:
        return None
    output["departure"] = departure.astimezone(TZ).isoformat()
    output["arrival"] = arrival.astimezone(TZ).isoformat()
    output["duration_minutes"] = int((arrival - departure).total_seconds() // 60)
    if access and isinstance(access_requirement, dict) and access_requirement.get("same_city") is False:
        direct_candidates = await _local_candidates(
            request.origin, request.destination, request.travel_date, request.departure_after, request,
        )
        direct_minutes = min(
            (as_int(candidate.get("duration_minutes")) for candidate in direct_candidates if as_int(candidate.get("duration_minutes")) > 0),
            default=0,
        )
        if direct_minutes and not _plausible_detour(output["duration_minutes"], direct_minutes):
            return None
    output["segments"] = segments
    output["access_leg"] = segments[0] if access else None
    output["egress_leg"] = segments[-1] if egress else None
    output["complete_connection"] = True

    known_prices = [as_float(route.get("price"))]
    prices_complete = known_prices[0] > 0
    for local in (access, egress):
        if not local:
            continue
        if request.deutschlandticket:
            known_prices.append(0.0)
        else:
            value = as_float(local.get("price"))
            known_prices.append(value)
            prices_complete = prices_complete and value > 0
    output["known_price_subtotal"] = round(sum(known_prices), 2)
    output["price_complete"] = prices_complete
    if prices_complete:
        output["price"] = round(sum(known_prices), 2)
    else:
        output["price"] = None
        output["price_note"] = "Gesamtpreis teilweise unbekannt; kein lokaler Zubringer wird als kostenlos angenommen."
    output["transfers"] = as_int(route.get("flix_transfers")) + len(segments) - 1
    return output


async def complete_flix_routes(
    routes: list[dict[str, Any]], request: ReiseRequest,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(3)

    async def one(route: dict[str, Any]):
        async with semaphore:
            try:
                return await complete_flix_route(route, request)
            except Exception:
                return None

    completed = await asyncio.gather(*(one(route) for route in routes))
    return [route for route in completed if isinstance(route, dict)]
