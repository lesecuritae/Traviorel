from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .config import DB_API_URL, DB_SEARCH_TIMEOUT, DB_SPLIT_TIMEOUT, TZ
from .location_resolver import location_candidates, location_match_is_safe
from .models import DeutschlandticketRequest, ReiseRequest
from .utils import as_float, as_int, local_iso


def build_manual_db_url(
    origin: str,
    destination: str,
    travel_date: str,
    departure_after: str,
    *,
    best_price: bool = False,
    departure_iso: str | None = None,
) -> str:
    departure_value = f"{travel_date}T{departure_after}:00"
    if departure_iso:
        try:
            parsed = datetime.fromisoformat(str(departure_iso).replace("Z", "+00:00"))
            parsed = parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)
            departure_value = parsed.strftime("%Y-%m-%dT%H:%M:%S")
        except (TypeError, ValueError):
            pass
    fragment = urlencode({
        "sts": "true",
        "so": origin,
        "zo": destination,
        "soid": f"O={origin}",
        "zoid": f"O={destination}",
        "hd": departure_value,
        "hza": "D",
        "ar": "false",
        "s": "true",
        "d": "false",
        "fm": "false",
        "bp": "true" if best_price else "false",
        "dlt": "false",
        "dltv": "false",
    }, quote_via=quote)
    return "https://www.bahn.de/buchung/fahrplan/suche#" + fragment


def build_manual_db_links(
    origin: str,
    destination: str,
    travel_date: str,
    departure_after: str,
    *,
    departure_iso: str | None = None,
) -> dict[str, dict[str, str]]:
    return {
        "normal": {
            "label": "Normale DB-Suche",
            "url": build_manual_db_url(
                origin, destination, travel_date, departure_after,
                best_price=False, departure_iso=departure_iso,
            ),
            "purpose": "Die konkrete Verbindung auf bahn.de öffnen und den aktuellen Endpreis prüfen.",
        },
        "best_price": {
            "label": "DB-Bestpreissuche",
            "url": build_manual_db_url(
                origin, destination, travel_date, departure_after,
                best_price=True, departure_iso=departure_iso,
            ),
            "purpose": "Günstigere DB-Angebote am selben Reisetag über verschiedene Abfahrtszeiten vergleichen.",
        },
    }


async def db_search(
    *,
    origin: str,
    destination: str,
    travel_date: str,
    departure_after: str,
    mode: str,
    max_transfers: int | None,
    results: int,
    arrival_before: datetime | None = None,
    bestprice: bool = False,
    not_only_fast_routes: bool = False,
    origin_id: str | None = None,
    destination_id: str | None = None,
) -> dict[str, Any]:
    departure = datetime.fromisoformat(f"{travel_date}T{departure_after}:00").replace(tzinfo=TZ)
    payload: dict[str, Any] = {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "results": results,
        "with_prices": mode != "deutschlandticket",
        "bestprice": bool(bestprice and mode != "deutschlandticket"),
        "not_only_fast_routes": bool(not_only_fast_routes),
    }
    # departure_after is a hard lower bound and must never be replaced by an
    # arrival-based search. The backend receives the flight cutoff separately
    # and filters journeys after a departure-based query.
    payload["departure"] = departure.isoformat()
    if origin_id:
        payload["origin_id"] = origin_id
    if destination_id:
        payload["destination_id"] = destination_id
    if arrival_before is not None:
        payload["arrival_before"] = arrival_before.astimezone(TZ).isoformat()
    if max_transfers is not None:
        payload["max_transfers"] = max_transfers

    timeout = httpx.Timeout(DB_SEARCH_TIMEOUT, connect=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{DB_API_URL}/search", json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"DB-API HTTP {response.status_code}: {response.text[:1000]}")
        return response.json()


async def db_search_with_retry(
    *,
    origin: str,
    destination: str,
    travel_date: str,
    departure_after: str,
    mode: str,
    max_transfers: int | None,
    results: int,
    attempts: int = 1,
    arrival_before: datetime | None = None,
    bestprice: bool = False,
    not_only_fast_routes: bool = False,
    origin_id: str | None = None,
    destination_id: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    last: dict[str, Any] = {"status": "failed", "journeys": []}
    location_pairs = [(origin, destination)] if origin_id or destination_id else list(dict.fromkeys(
        (origin_candidate, destination_candidate)
        for origin_candidate in location_candidates(origin)
        for destination_candidate in location_candidates(destination)
    ))
    for attempt in range(1, max(1, attempts) + 1):
        for origin_candidate, destination_candidate in location_pairs:
            try:
                last = await db_search(
                    origin=origin_candidate,
                    destination=destination_candidate,
                    travel_date=travel_date,
                    departure_after=departure_after,
                    mode=mode,
                    max_transfers=max_transfers,
                    results=results,
                    arrival_before=arrival_before,
                    bestprice=bestprice,
                    not_only_fast_routes=not_only_fast_routes,
                    origin_id=origin_id,
                    destination_id=destination_id,
                )
                diagnostics.append({
                    "source": "db-api",
                    "attempt": attempt,
                    "lookup_origin": origin_candidate,
                    "lookup_destination": destination_candidate,
                    "ok": bool(last.get("journeys")),
                    "source_profile": last.get("source"),
                    "routes": len(last.get("journeys") or []),
                    "resolved_origin": (last.get("origin") or {}).get("name") if isinstance(last.get("origin"), dict) else None,
                    "resolved_destination": (last.get("destination") or {}).get("name") if isinstance(last.get("destination"), dict) else None,
                    "time_mode": (last.get("diagnostics") or {}).get("time_mode") if isinstance(last.get("diagnostics"), dict) else None,
                    "arrival_before": (last.get("diagnostics") or {}).get("arrival_before") if isinstance(last.get("diagnostics"), dict) else None,
                    "backend_attempts": last.get("attempts") or [],
                })
                resolved_origin = (last.get("origin") or {}).get("name") if isinstance(last.get("origin"), dict) else None
                resolved_destination = (last.get("destination") or {}).get("name") if isinstance(last.get("destination"), dict) else None
                safe_locations = (
                    location_match_is_safe(origin, resolved_origin)
                    and location_match_is_safe(destination, resolved_destination)
                )
                if last.get("journeys") and safe_locations:
                    return last, diagnostics
            except Exception as exc:
                diagnostics.append({
                    "source": "db-api",
                    "attempt": attempt,
                    "lookup_origin": origin_candidate,
                    "lookup_destination": destination_candidate,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        if attempt < attempts:
            await asyncio.sleep(min(attempt, 2))
    return last, diagnostics


async def db_split_analysis(analysis_token: str) -> dict[str, Any]:
    timeout = httpx.Timeout(DB_SPLIT_TIMEOUT, connect=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{DB_API_URL}/split", json={"analysis_token": analysis_token})
        if response.status_code >= 400:
            raise RuntimeError(f"Split-Ticket-Prüfung HTTP {response.status_code}: {response.text[:1000]}")
        return response.json()


def rank_routes(routes: list[dict[str, Any]], preference: str) -> list[dict[str, Any]]:
    def departure_priority(route: dict[str, Any]) -> tuple[int, int]:
        offset = route.get("departure_offset_minutes")
        if not isinstance(offset, int):
            return 3, 0
        if offset == 0:
            return 0, 0
        if offset < 0:
            return 1, abs(offset)
        return 2, offset

    def price(route: dict[str, Any]) -> float:
        if route.get("price_complete") is False:
            return 1_000_000.0
        value = as_float(route.get("price"))
        split_value = as_float(route.get("best_split_price"))
        if split_value > 0:
            value = min(value, split_value) if value > 0 else split_value
        return value if value > 0 else 1_000_000.0

    def duration(route: dict[str, Any]) -> int:
        value = as_int(route.get("duration_minutes"))
        return value if value > 0 else 1_000_000

    if preference == "cheapest":
        key = lambda route: (*departure_priority(route), price(route), duration(route))
    elif preference == "fastest":
        key = lambda route: (*departure_priority(route), duration(route), price(route))
    elif preference == "fewest_transfers":
        key = lambda route: (*departure_priority(route), as_int(route.get("transfers")), duration(route), price(route))
    else:
        key = lambda route: (*departure_priority(route),
            price(route) + duration(route) * 0.12 + as_int(route.get("transfers")) * 8,
            duration(route),
        )
    return sorted((dict(route) for route in routes), key=key)


def compact_route(route: dict[str, Any], *, include_legs: bool = True) -> dict[str, Any]:
    price = as_float(route.get("price"))
    split_price = as_float(route.get("best_split_price"))
    compact: dict[str, Any] = {
        "id": route.get("id"),
        "provider": route.get("provider"),
        "provider_code": route.get("provider_code"),
        "db_source": route.get("db_source"),
        "type": route.get("type"),
        "origin": route.get("origin") if isinstance(route.get("origin"), str) else None,
        "destination": route.get("destination") if isinstance(route.get("destination"), str) else None,
        "departure": local_iso(route.get("departure")),
        "arrival": local_iso(route.get("arrival")),
        "duration_minutes": as_int(route.get("duration_minutes")) or None,
        "transfers": as_int(route.get("transfers")),
        "price": round(price, 2) if price > 0 else None,
        "fare_name": route.get("fare_name"),
        "price_source": route.get("price_source"),
        "currency": route.get("currency") if price > 0 else None,
        "price_note": route.get("price_note") if price <= 0 else None,
        "best_split_price": round(split_price, 2) if split_price > 0 else None,
        "split_savings": route.get("split_savings") if split_price > 0 else None,
        "booking_url": route.get("booking_url"),
        "departure_offset_minutes": route.get("departure_offset_minutes"),
        "early_departure_minutes": route.get("early_departure_minutes"),
        "coverage_origin": route.get("coverage_origin"),
        "coverage_destination": route.get("coverage_destination"),
    }
    if include_legs:
        legs = []
        for leg in (route.get("legs") or [])[:8]:
            if not isinstance(leg, dict):
                continue
            origin = leg.get("origin") or {}
            destination = leg.get("destination") or {}
            stopovers = [
                {
                    key: value
                    for key, value in {
                        "name": stop.get("name"),
                        "station_id": stop.get("id"),
                        "latitude": stop.get("latitude"),
                        "longitude": stop.get("longitude"),
                    }.items()
                    if value not in (None, "")
                }
                for stop in (leg.get("stopovers") or [])[:40]
                if isinstance(stop, dict) and stop.get("name")
            ]
            legs.append({
                key: value for key, value in {
                    "mode": leg.get("mode") or leg.get("type"),
                    "line": leg.get("line"),
                    "line_name": leg.get("line_name"),
                    "train_number": leg.get("train_number") or leg.get("fahrt_nr"),
                    "train_type": leg.get("train_type"),
                    "product": leg.get("product"),
                    "operator": leg.get("operator"),
                    "provider": leg.get("provider"),
                    "origin": origin.get("name") if isinstance(origin, dict) else origin,
                    "origin_id": origin.get("id") if isinstance(origin, dict) else None,
                    "destination": destination.get("name") if isinstance(destination, dict) else destination,
                    "destination_id": destination.get("id") if isinstance(destination, dict) else None,
                    "departure": local_iso(leg.get("departure")),
                    "arrival": local_iso(leg.get("arrival")),
                    "cancelled": True if leg.get("cancelled") else None,
                    "departure_delay_minutes": leg.get("departure_delay_minutes"),
                    "arrival_delay_minutes": leg.get("arrival_delay_minutes"),
                    "platform": leg.get("platform"),
                    "planned_platform": leg.get("planned_platform"),
                    "reliability": leg.get("reliability"),
                    "connection_reliability": leg.get("connection_reliability"),
                    "stopovers": stopovers,
                }.items() if value not in (None, "", [])
            })
        if legs:
            compact["legs"] = legs
    if isinstance(route.get("reliability"), dict):
        compact["reliability"] = route["reliability"]
    return {key: value for key, value in compact.items() if value not in (None, "", [])}


def compact_attempts(attempts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for attempt in attempts[:3]:
        item = {
            "source": attempt.get("source"),
            "attempt": attempt.get("attempt"),
            "ok": attempt.get("ok"),
            "source_profile": attempt.get("source_profile"),
            "routes": attempt.get("routes"),
            "resolved_origin": attempt.get("resolved_origin"),
            "resolved_destination": attempt.get("resolved_destination"),
            "time_mode": attempt.get("time_mode"),
            "arrival_before": attempt.get("arrival_before"),
            "error": attempt.get("error"),
        }
        backend = []
        for candidate in (attempt.get("backend_attempts") or [])[:4]:
            if not isinstance(candidate, dict):
                continue
            backend.append({
                key: candidate.get(key)
                for key in ("profile", "ok", "routes", "priced", "error")
                if candidate.get(key) not in (None, "", [])
            })
        if backend:
            item["backend_attempts"] = backend
        output.append({key: value for key, value in item.items() if value not in (None, "", [])})
    return output


async def search_deutschlandticket(request: DeutschlandticketRequest) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return await db_search_with_retry(
        origin=request.origin,
        destination=request.destination,
        travel_date=request.travel_date,
        departure_after=request.departure_after,
        mode="deutschlandticket",
        max_transfers=request.max_transfers,
        results=request.max_results,
    )
