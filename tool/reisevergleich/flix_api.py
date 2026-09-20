"""FlixBus/FlixTrain-Preise direkt bei Flix, ohne trvl und ohne Schlüssel.

Genutzt werden die öffentlichen Endpunkte, die auch die Flix-Webseite selbst verwendet: die Ortssuche
(``search/autocomplete/cities``) und die Verbindungssuche (``search/service/v4/search``). Beide antworten ohne
Anmeldung und ohne Browserprüfung. Die Antwort wird in dasselbe Routenformat gebracht, das die Auswertung in
``trvl.flix_search`` bisher von trvl bekam; schlägt die Abfrage fehl, greift dort weiter trvl.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any

_BASE = os.environ.get("FLIX_API_BASE", "https://global.api.flixbus.com")
_TIMEOUT = max(3, min(int(os.environ.get("FLIX_API_TIMEOUT", "12")), 30))
_CITY_TTL = 24 * 3600
_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; Traviorel)"}
_city_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _get_json(path: str, params: dict[str, Any]) -> Any:
    url = f"{_BASE}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=_HEADERS)  # nosec B310 - feste https-Adresse
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # nosec B310
        return json.load(response)


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", " ", str(value or "").casefold()).strip()


def pick_city(rows: Any, query: str) -> dict[str, Any] | None:
    """Beste Stadt der Ortssuche: exakter Name vor bestem Treffer; nur Ortschaften mit ID."""
    cities = [row for row in rows if isinstance(row, dict) and row.get("id")] if isinstance(rows, list) else []
    if not cities:
        return None
    wanted = _norm(query)
    for row in cities:
        if _norm(row.get("name")) == wanted:
            return row
    return cities[0]


def _resolve_city(query: str) -> dict[str, Any] | None:
    key = _norm(query)
    cached = _city_cache.get(key)
    if cached and time.monotonic() - cached[0] < _CITY_TTL:
        return cached[1]
    rows = _get_json("/search/autocomplete/cities", {"q": query, "lang": "de", "country": "de", "flixbus_cities_only": "false"})
    city = pick_city(rows, query)
    _city_cache[key] = (time.monotonic(), city)
    return city


def _minutes(duration: Any) -> int | None:
    if isinstance(duration, dict):
        return int(duration.get("hours") or 0) * 60 + int(duration.get("minutes") or 0)
    return None


def _stop(point: dict[str, Any], stations: dict[str, Any], cities: dict[str, Any]) -> dict[str, Any]:
    station_id = str(point.get("station_id") or "")
    station = stations.get(station_id) if isinstance(stations.get(station_id), dict) else {}
    city = cities.get(str(point.get("city_id") or "")) if isinstance(cities.get(str(point.get("city_id") or "")), dict) else {}
    stop = {"city": city.get("name"), "station": station.get("name") or station_id, "station_id": station_id, "time": point.get("date")}
    return {key: value for key, value in stop.items() if value not in (None, "")}


def routes_from_response(data: dict[str, Any], origin: dict[str, Any], destination: dict[str, Any], travel_date: str) -> list[dict[str, Any]]:
    stations = data.get("stations") if isinstance(data.get("stations"), dict) else {}
    cities = {str(c.get("id")): c for c in data.get("cities", []) if isinstance(c, dict)} if isinstance(data.get("cities"), list) else {}
    day = date.fromisoformat(travel_date).strftime("%d.%m.%Y")
    booking = "https://shop.global.flixbus.com/search?" + urllib.parse.urlencode({
        "departureCity": origin.get("legacy_id") or "", "arrivalCity": destination.get("legacy_id") or "",
        "rideDate": day, "adult": 1, "_locale": "de",
    })
    routes: list[dict[str, Any]] = []
    for trip in data.get("trips") or []:
        for result in (trip.get("results") or {}).values() if isinstance(trip, dict) else []:
            if not isinstance(result, dict) or result.get("status") != "available":
                continue
            price = (result.get("price") or {}).get("total")
            legs_raw = [leg for leg in result.get("legs") or [] if isinstance(leg, dict)]
            if not isinstance(price, (int, float)) or price <= 0 or not legs_raw:
                continue
            modes = {str(leg.get("means_of_transport") or "bus").casefold() for leg in legs_raw}
            kind = "train" if modes == {"train"} else "bus" if modes == {"bus"} else "mixed"
            legs = [
                {"type": str(leg.get("means_of_transport") or "bus").casefold(), "provider": "flixbus",
                 "departure": _stop(leg.get("departure") or {}, stations, cities), "arrival": _stop(leg.get("arrival") or {}, stations, cities)}
                for leg in legs_raw
            ]
            routes.append({
                "provider": "flixbus", "type": kind, "price": float(price), "currency": "EUR", "comparable_price": float(price),
                "duration_minutes": _minutes(result.get("duration")),
                "departure": _stop(result.get("departure") or {}, stations, cities), "arrival": _stop(result.get("arrival") or {}, stations, cities),
                "transfers": max(0, len(legs) - 1), "legs": legs, "booking_url": booking,
            })
    return routes


def _search_sync(origin_query: str, destination_query: str, travel_date: str) -> dict[str, Any]:
    origin, destination = _resolve_city(origin_query), _resolve_city(destination_query)
    if not origin or not destination:
        return {"ok": False, "error": "Flix kennt einen der beiden Orte nicht"}
    day = date.fromisoformat(travel_date).strftime("%d.%m.%Y")
    data = _get_json("/search/service/v4/search", {
        "from_city_id": origin["id"], "to_city_id": destination["id"], "departure_date": day,
        "products": json.dumps({"adult": 1}), "currency": "EUR", "locale": "de", "search_by": "cities", "include_after_midnight_rides": 1,
    })
    if not isinstance(data, dict) or "trips" not in data:
        return {"ok": False, "error": "Flix lieferte ein unerwartetes Format"}
    return {"ok": True, "data": {"routes": routes_from_response(data, origin, destination, travel_date)}, "raw": {}}


async def search(origin_query: str, destination_query: str, travel_date: str) -> dict[str, Any]:
    """Wie ``run_json_command`` für trvl: ``{"ok": bool, "data": {"routes": [...]}, "error": ...}``."""
    try:
        return await asyncio.to_thread(_search_sync, origin_query, destination_query, travel_date)
    except Exception as exc:  # noqa: BLE001 - jeder Fehler führt nur zum Rückfall auf trvl
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
