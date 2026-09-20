from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from . import price_history
from .cache import begin_scope, end_scope, get_cached_journey, save_journey, stats as cache_stats
from .compare import compare_ground_round_trip
from .config import TRIP_TIMEOUT, today_iso
from .models import PriceCalendarRequest, TripRequest
from .planner import complete_trip
from .presentation import public_result


def _ground_return_date(request: TripRequest) -> tuple[str | None, int | None]:
    if request.journey_type == "one_way":
        return None, 0
    if request.return_mode == "date":
        return request.return_date, None
    return None, request.stay_nights


async def _compute(request: TripRequest) -> dict[str, Any]:
    if request.travel_mode == "ground":
        return_date, stay_nights = _ground_return_date(request)
        try:
            return await asyncio.wait_for(compare_ground_round_trip(
                origin=request.origin,
                destination=request.destination,
                outbound_date=request.departure_date,
                return_date=return_date,
                stay_nights=stay_nights,
                departure_after=request.departure_after,
                preference="cheapest" if request.effective_feeder_preference == "dticket_first" else request.effective_feeder_preference,
                include_flixtrain=request.include_flixtrain,
                include_flixbus=request.include_flixbus,
                include_train=request.include_train,
                include_bus=request.include_bus,
                max_transfers=None,
                max_results=request.max_results,
                split_ticket_check=request.split_ticket_check,
                flix_origin_stop_id=request.flix_origin_stop_id,
                flix_destination_stop_id=request.flix_destination_stop_id,
                origin_station=request.origin_station,
                destination_station=request.destination_station,
                deutschlandticket_mode="only" if request.deutschlandticket_only else ("include" if request.deutschlandticket else "exclude"),
                split_candidates=request.feeder_split_candidates,
                one_way=request.journey_type == "one_way",
            ), timeout=TRIP_TIMEOUT)
        except TimeoutError:
            return {
                "status": "partial",
                "search_mode": "ground_trip",
                "current_date": today_iso(),
                "error": f"Die Bahn-/Bus-Reise hat das Gesamtzeitlimit von {TRIP_TIMEOUT} Sekunden erreicht.",
            }

    try:
        return await asyncio.wait_for(complete_trip(request), timeout=TRIP_TIMEOUT)
    except TimeoutError:
        return {
            "status": "partial",
            "search_mode": "trip_plan",
            "current_date": today_iso(),
            "error": f"Die vollständige Reisekette hat das Zeitlimit von {TRIP_TIMEOUT} Sekunden erreicht.",
        }


async def search(request: TripRequest) -> dict[str, Any]:
    token = begin_scope(refresh=request.refresh_cache)
    try:
        if not request.refresh_cache and not request.include_hotel:
            try:
                cached = await get_cached_journey(request)
            except (OSError, TimeoutError):
                cached = None
            if cached is not None:
                journey_id, result = cached
                result = {**result, "journey_id": journey_id, "cache": {**cache_stats(), "journey_hit": True}}
                return public_result(result)

        result = await _compute(request)
        if result.get("status") not in {"missing_fields", "needs_clarification"} and not request.include_hotel:
            try:
                journey_id = await save_journey(request, result)
                result = {**result, "journey_id": journey_id}
            except (OSError, TimeoutError):
                pass
        result["cache"] = {**cache_stats(), "journey_hit": False}
        public = public_result(result)
        price_history.record_search(public)
        return public
    finally:
        end_scope(token)


def _calendar_day(result: dict[str, Any], travel_date: str) -> dict[str, Any]:
    context = result.get("response_context") or {}
    outbound = context.get("outbound") or {}
    connections = outbound.get("connections") or []
    priced: list[tuple[float, str]] = []
    summary = context.get("price_summary") or {}
    round_trip = bool((context.get("route") or {}).get("return_date"))
    summary_price = summary.get("round_trip_live_price") if round_trip else summary.get("known_total_price", summary.get("outbound_live_price"))
    if isinstance(summary_price, (int, float)) and (not round_trip or summary.get("complete") is True):
        priced.append((float(summary_price), str(summary.get("currency") or "EUR")))
    elif not round_trip:
        for connection in connections:
            if connection.get("deutschlandticket_covered") is True:
                priced.append((0.0, "EUR"))
            elif isinstance(connection.get("price"), (int, float)):
                priced.append((float(connection["price"]), str(connection.get("currency") or "EUR")))
    cheapest = min(priced, key=lambda item: item[0]) if priced else None
    return {
        "date": travel_date,
        "status": "available" if connections else "unavailable",
        "connection_count": len(connections),
        "price": round(cheapest[0], 2) if cheapest else None,
        "currency": cheapest[1] if cheapest else None,
        "price_available": cheapest is not None,
        "cache_hit": bool((result.get("cache") or {}).get("journey_hit")),
    }


async def price_calendar(request: PriceCalendarRequest) -> dict[str, Any]:
    """Compare up to 14 dates without bypassing normal routing or price adapters."""
    start = date.fromisoformat(request.departure_date)
    original_return = date.fromisoformat(request.return_date) if request.return_date else None
    semaphore = asyncio.Semaphore(2)

    async def one_day(offset: int) -> dict[str, Any]:
        outbound = start + timedelta(days=offset)
        updates: dict[str, Any] = {"departure_date": outbound.isoformat()}
        if original_return:
            updates["return_date"] = (original_return + timedelta(days=offset)).isoformat()
        day_request = TripRequest.model_validate(request.model_dump(exclude={"calendar_days"}) | updates)
        async with semaphore:
            try:
                return _calendar_day(await search(day_request), outbound.isoformat())
            except Exception:
                return {
                    "date": outbound.isoformat(), "status": "failed", "connection_count": 0,
                    "price": None, "currency": None, "price_available": False,
                    "error": "Tagesabfrage fehlgeschlagen.",
                }

    days = await asyncio.gather(*(one_day(offset) for offset in range(request.calendar_days)))
    priced_days = [item for item in days if item["price_available"]]
    cheapest_date = min(priced_days, key=lambda item: (item["price"], item["date"]))["date"] if priced_days else None
    for item in days:
        item["cheapest"] = item["date"] == cheapest_date
    return {
        "status": "ok" if any(item["status"] == "available" for item in days) else "unavailable",
        "origin": request.origin, "destination": request.destination,
        "start_date": request.departure_date, "calendar_days": request.calendar_days,
        "cheapest_date": cheapest_date, "days": days,
    }
