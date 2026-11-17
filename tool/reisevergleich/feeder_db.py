from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

from .config import TZ
from .db import compact_attempts, compact_route, rank_routes
from .feeder_common import _compact_feeder_segment, _departure_lower_bound, _route_candidate_stations, _route_duration, _route_price
from .models import ReiseRequest
from .provider_cache import db_search_with_retry, db_split_analysis, transitous_search
from .utils import as_float, as_int, local_iso, parse_datetime


def _requires_separate_ticket(route: dict[str, Any]) -> bool:
    """Return true only for an explicitly non-D-Ticket DB product."""
    for leg in route.get("legs") or []:
        if not isinstance(leg, dict) or leg.get("walking"):
            continue
        product = str(leg.get("product") or "").casefold()
        label = str(leg.get("line") or "").strip().upper()
        if product in {"national", "nationalexpress"}:
            return True
        if re.search(r"\bLH[- ]?EX\b", label):
            return True
        if re.match(r"^(ICE|IC|EC|ECE|TGV|RJ|RJX|NJ|FLX)\b", label):
            return True
    return False


async def _db_routes(
    origin: str,
    destination: str,
    travel_date: str,
    departure_after: str,
    *,
    mode: str,
    results: int = 8,
    arrival_before: datetime | None = None,
    bestprice: bool = False,
    not_only_fast_routes: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    result, attempts = await db_search_with_retry(
        origin=origin,
        destination=destination,
        travel_date=travel_date,
        departure_after=departure_after,
        mode=mode,
        max_transfers=6,
        results=results,
        arrival_before=arrival_before,
        bestprice=bestprice,
        not_only_fast_routes=not_only_fast_routes,
    )
    journeys = result.get("journeys") or []
    if mode == "deutschlandticket":
        journeys = [route for route in journeys if not _requires_separate_ticket(route)]
    return journeys, attempts, result.get("source")


async def _native_split_options(
    routes: list[dict[str, Any]],
    *,
    max_routes: int = 3,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    priced = [route for route in routes if _route_price(route) > 0 and route.get("analysis_token")]
    for route in rank_routes(priced, "cheapest")[:max_routes]:
        try:
            split = await db_split_analysis(str(route["analysis_token"]))
        except Exception:
            continue
        if split.get("status") != "success":
            continue
        original_price = as_float(split.get("original_price"))
        for candidate in (split.get("split_options") or [])[:3]:
            total = as_float(candidate.get("total_price"))
            if total <= 0:
                continue
            segments = [
                compact_route(segment)
                for segment in (candidate.get("segments") or [])
                if isinstance(segment, dict)
            ]
            options.append({
                "type": "db_native_split",
                "label": "Zwei getrennte DB-Tickets auf derselben Verbindung",
                "split_station": (candidate.get("split_station") or {}).get("name")
                if isinstance(candidate.get("split_station"), dict)
                else candidate.get("split_station"),
                "departure": local_iso(route.get("departure")),
                "arrival": local_iso(route.get("arrival")),
                "duration_minutes": _route_duration(route),
                "total_price": round(total, 2),
                "price_known": True,
                "currency": candidate.get("currency") or split.get("currency") or route.get("currency") or "EUR",
                "savings_vs_direct": round(original_price - total, 2) if original_price > total else None,
                "self_managed_transfers": 1,
                "segments": segments,
                "method": split.get("method"),
            })
    return options


async def _outbound_via_options(
    origin: str,
    airport_station: str,
    handoff: str,
    travel_date: str,
    departure_after: str,
    cutoff: datetime | None,
    transfer_minutes: int,
    *,
    deutschlandticket_first: bool,
) -> list[dict[str, Any]]:
    first_mode = "deutschlandticket" if deutschlandticket_first else "all"
    first_routes, _, _ = await _db_routes(
        origin, handoff, travel_date, departure_after, mode=first_mode, results=8,
        bestprice=False, not_only_fast_routes=True,
    )
    ranked_first = rank_routes(
        first_routes, "fastest" if deutschlandticket_first else "cheapest"
    )[:4]
    usable_first = []
    for first in ranked_first:
        first_arrival = parse_datetime(first.get("arrival"))
        first_departure = parse_datetime(first.get("departure"))
        if first_arrival and first_departure:
            usable_first.append((first, first_departure, first_arrival))
    if not usable_first:
        return []

    earliest_ready = min(arrival for _, _, arrival in usable_first) + timedelta(minutes=transfer_minutes)
    second_date = earliest_ready.astimezone(TZ).date().isoformat()
    second_after = earliest_ready.astimezone(TZ).strftime("%H:%M")
    second_routes, _, _ = await _db_routes(
        handoff, airport_station, second_date, second_after, mode="all", results=10,
        arrival_before=cutoff, bestprice=False, not_only_fast_routes=True,
    )
    ranked_second = rank_routes(second_routes, "cheapest")[:6]

    options: list[dict[str, Any]] = []
    for first, first_departure, first_arrival in usable_first:
        second_start = first_arrival + timedelta(minutes=transfer_minutes)
        for second in ranked_second:
            second_departure = parse_datetime(second.get("departure"))
            second_arrival = parse_datetime(second.get("arrival"))
            if not second_departure or not second_arrival:
                continue
            if second_departure < second_start:
                continue
            if cutoff and second_arrival > cutoff:
                continue
            first_price = 0.0 if deutschlandticket_first else _route_price(first)
            second_price = _route_price(second)
            if deutschlandticket_first and not _requires_separate_ticket(second):
                continue
            if second_price <= 0 or (not deutschlandticket_first and first_price <= 0):
                continue
            total = first_price + second_price
            options.append({
                "type": "deutschlandticket_plus_db" if deutschlandticket_first else "db_via_split",
                "label": (
                    "Deutschlandticket bis zum Übergabebahnhof, danach separates DB-Fernverkehrsticket"
                    if deutschlandticket_first
                    else "Zwei separat gesuchte DB-Tickets über einen Übergabebahnhof"
                ),
                "split_station": handoff,
                "departure": first_departure.astimezone(TZ).isoformat(),
                "arrival": second_arrival.astimezone(TZ).isoformat(),
                "duration_minutes": int((second_arrival - first_departure).total_seconds() // 60),
                "total_price": round(total, 2),
                "price_known": True,
                "currency": second.get("currency") or first.get("currency") or "EUR",
                "requires_deutschlandticket": deutschlandticket_first,
                "price_note": (
                    "Gesamtpreis enthält nur den bezahlten DB-Abschnitt; das vorhandene Deutschlandticket wird mit 0 EUR Zusatzkosten angesetzt."
                    if deutschlandticket_first else None
                ),
                "self_managed_transfers": 1,
                "transfer_minutes": int((second_departure - first_arrival).total_seconds() // 60),
                "segments": [
                    _compact_feeder_segment(first, deutschlandticket=deutschlandticket_first),
                    _compact_feeder_segment(second),
                ],
            })
    return options


async def _return_via_options(
    airport_station: str,
    destination: str,
    handoff: str,
    search_date: str,
    departure_after: str,
    transfer_minutes: int,
    *,
    deutschlandticket_last: bool,
) -> list[dict[str, Any]]:
    first_routes, _, _ = await _db_routes(
        airport_station, handoff, search_date, departure_after, mode="all", results=8,
        bestprice=False, not_only_fast_routes=True,
    )
    ranked_first = rank_routes(first_routes, "cheapest")[:4]
    usable_first = []
    for first in ranked_first:
        first_arrival = parse_datetime(first.get("arrival"))
        first_departure = parse_datetime(first.get("departure"))
        if first_arrival and first_departure:
            usable_first.append((first, first_departure, first_arrival))
    if not usable_first:
        return []

    earliest_ready = min(arrival for _, _, arrival in usable_first) + timedelta(minutes=transfer_minutes)
    second_date = earliest_ready.astimezone(TZ).date().isoformat()
    second_after = earliest_ready.astimezone(TZ).strftime("%H:%M")
    second_mode = "deutschlandticket" if deutschlandticket_last else "all"
    second_routes, _, _ = await _db_routes(
        handoff, destination, second_date, second_after, mode=second_mode, results=10,
        bestprice=False, not_only_fast_routes=True,
    )
    ranked_second = rank_routes(
        second_routes, "fastest" if deutschlandticket_last else "cheapest"
    )[:6]

    options: list[dict[str, Any]] = []
    for first, first_departure, first_arrival in usable_first:
        second_start = first_arrival + timedelta(minutes=transfer_minutes)
        for second in ranked_second:
            second_departure = parse_datetime(second.get("departure"))
            second_arrival = parse_datetime(second.get("arrival"))
            if not second_departure or not second_arrival or second_departure < second_start:
                continue
            first_price = _route_price(first)
            second_price = 0.0 if deutschlandticket_last else _route_price(second)
            if deutschlandticket_last and not _requires_separate_ticket(first):
                continue
            if first_price <= 0 or (not deutschlandticket_last and second_price <= 0):
                continue
            total = first_price + second_price
            options.append({
                "type": "db_plus_deutschlandticket" if deutschlandticket_last else "db_via_split",
                "label": (
                    "Separates DB-Fernverkehrsticket bis zum Übergabebahnhof, danach Deutschlandticket"
                    if deutschlandticket_last
                    else "Zwei separat gesuchte DB-Tickets über einen Übergabebahnhof"
                ),
                "split_station": handoff,
                "departure": first_departure.astimezone(TZ).isoformat(),
                "arrival": second_arrival.astimezone(TZ).isoformat(),
                "duration_minutes": int((second_arrival - first_departure).total_seconds() // 60),
                "total_price": round(total, 2),
                "price_known": True,
                "currency": first.get("currency") or second.get("currency") or "EUR",
                "requires_deutschlandticket": deutschlandticket_last,
                "price_note": (
                    "Gesamtpreis enthält nur den bezahlten DB-Abschnitt; das vorhandene Deutschlandticket wird mit 0 EUR Zusatzkosten angesetzt."
                    if deutschlandticket_last else None
                ),
                "self_managed_transfers": 1,
                "transfer_minutes": int((second_departure - first_arrival).total_seconds() // 60),
                "segments": [
                    _compact_feeder_segment(first),
                    _compact_feeder_segment(second, deutschlandticket=deutschlandticket_last),
                ],
            })
    return options


