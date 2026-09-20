from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import (
    FLIGHT_PROVIDER_CONCURRENCY, FLIGHT_PROVIDER_TIMEOUT, GROUND_PROVIDER_CONCURRENCY,
    GROUND_PROVIDER_TIMEOUT, HOTEL_ENRICH_TIMEOUT, HOTEL_HEADLINE_TIMEOUT,
    MAX_HOTEL_NIGHTLY_EUR, MAX_HOTEL_TOTAL_EUR, TRANSFER_PROVIDER_CONCURRENCY,
    TRANSFER_PROVIDER_TIMEOUT, TRVL_BIN, TRVL_ENABLED,
)
from . import fx, flix_api, skiplagged
from .models import FlightRequest, HotelRequest, ReiseRequest
from .db import rank_routes
from .airports import AIRPORT_TRANSIT_QUERIES, CITY_TRANSIT_QUERIES
from .transitous import search as transitous_direct_search
from .utils import (
    as_float,
    as_int,
    build_google_flights_url,
    build_google_hotels_url,
    build_google_maps_url,
    run_command,
    run_json_command,
    route_departure_in_window,
)



_FLIX_NETWORK_URL = os.environ.get(
    "FLIX_NETWORK_URL",
    "https://global.api.flixbus.com/search/autocomplete/stations",
)
_FLIX_NETWORK_TIMEOUT = max(2, min(int(os.environ.get("FLIX_NETWORK_TIMEOUT", "6")), 15))
_FLIX_NETWORK_SUCCESS_TTL = 6 * 60 * 60
_FLIX_NETWORK_FAILURE_TTL = 15 * 60
_flix_station_cache: dict[str, dict[str, dict[str, Any]]] = {}
_flix_station_cache_at = 0.0
_flix_station_cache_ok = False


def _looks_like_station_id(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.isdigit():
        return True
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}", text))


def _load_flix_station_directory_sync(queries: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """Resolve current Flix stop metadata for the requested places."""
    global _flix_station_cache_at, _flix_station_cache_ok
    now = time.monotonic()
    ttl = _FLIX_NETWORK_SUCCESS_TTL if _flix_station_cache_ok else _FLIX_NETWORK_FAILURE_TTL
    if _flix_station_cache_at and now - _flix_station_cache_at < ttl:
        return {key: value for query in queries for key, value in _flix_station_cache.get(query, {}).items()}

    _flix_station_cache_at = now
    directory: dict[str, dict[str, Any]] = {}
    for query in queries:
        try:
            url = _FLIX_NETWORK_URL + "?" + urllib.parse.urlencode({"q": query, "locale": "de"})
            request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=_FLIX_NETWORK_TIMEOUT) as response:
                rows = json.load(response)
        except Exception:
            continue
        if not isinstance(rows, list):
            continue
        query_directory: dict[str, dict[str, Any]] = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            station_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not station_id or not name:
                continue
            coordinates = item.get("location") if isinstance(item.get("location"), dict) else {}
            city = item.get("city") if isinstance(item.get("city"), dict) else {}
            query_directory[station_id] = {
                "name": name, "city": city.get("name"), "country": (item.get("country") or {}).get("code"),
                "address": item.get("address"), "latitude": coordinates.get("lat"), "longitude": coordinates.get("lon"),
            }
        _flix_station_cache[query] = query_directory
        directory.update(query_directory)
    _flix_station_cache_ok = bool(directory)
    return directory


async def _flix_station_directory(*queries: str) -> dict[str, dict[str, Any]]:
    normalized = tuple(dict.fromkeys(str(query or "").strip() for query in queries if str(query or "").strip()))
    return await asyncio.to_thread(_load_flix_station_directory_sync, normalized)


def _enrich_flix_stop(
    stop: dict[str, Any] | None,
    directory: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(stop, dict):
        return stop
    output = dict(stop)
    raw_station = str(output.get("station") or "").strip()
    if not raw_station:
        return output

    # trvl v1.21+ exposes Flix station IDs in GroundLeg. Preserve the ID even
    # when a human-readable station name can be resolved.
    if _looks_like_station_id(raw_station):
        output["station_id"] = raw_station
    meta = directory.get(raw_station)
    if not meta:
        return output

    output["station"] = meta.get("name") or raw_station
    if meta.get("city"):
        output["city"] = meta["city"]
    if meta.get("address"):
        output["address"] = meta["address"]
    if meta.get("latitude") not in (None, ""):
        output["latitude"] = meta["latitude"]
    if meta.get("longitude") not in (None, ""):
        output["longitude"] = meta["longitude"]
    return output


def _enrich_flix_routes(
    routes: list[dict[str, Any]],
    directory: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    for route in routes:
        legs = [leg for leg in (route.get("legs") or []) if isinstance(leg, dict)]
        for leg in legs:
            leg["departure"] = _enrich_flix_stop(leg.get("departure"), directory)
            leg["arrival"] = _enrich_flix_stop(leg.get("arrival"), directory)

        # GroundRoute itself only carries city+time for Flix in trvl v1.21+.
        # The first/last GroundLeg contain the actual station ID, so after
        # enrichment use them as the exact endpoints.
        if legs:
            first = legs[0].get("departure")
            last = legs[-1].get("arrival")
            if isinstance(first, dict):
                route["departure"] = first
            if isinstance(last, dict):
                route["arrival"] = last
    return routes


_FLIX_PLACE_ALIASES = {
    "hannover": "hanover",
    "koeln": "cologne",
    "koln": "cologne",
    "köln": "cologne",
    "muenchen": "munich",
    "munchen": "munich",
    "münchen": "munich",
    "nuernberg": "nuremberg",
    "nurnberg": "nuremberg",
    "nürnberg": "nuremberg",
}


def _flix_place_key(value: Any) -> str:
    text = str(value or "").casefold().replace("ß", "ss")
    text = re.sub(r"\b(hbf|hauptbahnhof|central station|zob)\b", " ", text)
    words = re.findall(r"[a-zäöü]+", text)
    if not words:
        return ""
    return _FLIX_PLACE_ALIASES.get(words[0], words[0])


def _normalized_station_name(value: Any) -> str:
    text = re.sub(r"[^a-z0-9äöüß]+", " ", str(value or "").casefold()).strip()
    text = re.sub(r"\b(?:hauptbahnhof|central station)\b", "hbf", text)
    text = re.sub(r"\bcentral bus station\b", "zob", text)
    return " ".join(text.split())


def _flix_city_query(value: str) -> str:
    """Use the place name for trvl's city search, not a rail terminal label.

    trvl currently selects the first Flix autocomplete result.  Queries such as
    ``Leipzig Hbf`` can therefore resolve to a similarly ranked, unrelated
    city.  Removing only terminal qualifiers makes the Flix city autocomplete
    deterministic while the returned station IDs still identify the concrete
    departure and arrival stops.
    """
    text = str(value or "").strip()
    text = re.sub(r"\s*\((?:FlixTrain|bus\s+station|busbahnhof)\)\s*$", "", text, flags=re.IGNORECASE).strip()
    normalized = re.sub(
        r"(?:\s*\([^)]*\))?\s+(?:hbf|hauptbahnhof|central\s+(?:train\s+)?station|zob|busbahnhof)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip(" ,-")
    return normalized or text


def _flix_exact_stop_matches_request(stop: Any, expected: str) -> bool:
    if not isinstance(stop, dict) or not stop.get("station"):
        return False
    actual = _normalized_station_name(stop["station"])
    requested = _normalized_station_name(expected)
    return bool(actual and requested and actual == requested)


def _flix_transfer_requirement(
    stop: Any,
    requested: str,
    kind: str,
    accepted_station_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(stop, dict) or not stop.get("station"):
        return None
    station_id = str(stop.get("station_id") or "").strip()
    if station_id and station_id in (accepted_station_ids or set()):
        return None
    if _flix_exact_stop_matches_request(stop, requested):
        return None
    return {
        "required": True,
        "kind": kind,
        "requested_station": requested,
        "actual_flix_stop": stop.get("station"),
        "same_city": _flix_endpoint_matches_request(stop, requested),
        "status": "unresolved_stop" if _looks_like_station_id(stop.get("station")) else "connection_required",
        "minimum_transfer_minutes": 15,
        "additional_cost": None,
        "price_note": "Zubringerpreis ist nicht enthalten; 0 EUR nur nach separater Prüfung mit vorhandenem Deutschlandticket.",
    }


def _flix_endpoint_candidate_allowed(stop: Any, expected: str) -> bool:
    """Allow a different city only when a concrete Flix stop can anchor a feeder."""
    if isinstance(stop, dict) and str(stop.get("station") or "").strip():
        return True
    return _flix_endpoint_matches_request(stop, expected)


def _flix_endpoint_matches_request(stop: Any, expected: str) -> bool:
    if not isinstance(stop, dict):
        return True
    actual = stop.get("city") or stop.get("station") or stop.get("address")
    if not actual or _looks_like_station_id(actual):
        return True
    actual_key, expected_key = _flix_place_key(actual), _flix_place_key(expected)
    return not actual_key or not expected_key or actual_key == expected_key


def _compact_ground_leg(leg: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "type": leg.get("type") or leg.get("mode"),
            "provider": leg.get("provider"),
            "departure": _ground_stop(leg.get("departure")),
            "arrival": _ground_stop(leg.get("arrival")),
            "duration_minutes": as_int(leg.get("duration_minutes") or leg.get("duration")) or None,
        }.items()
        if value not in (None, "", [])
    }


def provider_local_iso(value: Any) -> str | None:
    """Preserve the provider's local wall-clock time instead of converting it to Europe/Berlin."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return parsed.isoformat(timespec="minutes")


def _wall_datetime(value: Any, fallback_date: str | None = None, timezone_name: str | None = None) -> datetime | None:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None and timezone_name:
                parsed = parsed.astimezone(ZoneInfo(timezone_name))
            return parsed.replace(tzinfo=None)
        except ValueError:
            pass
    if fallback_date and isinstance(value, str):
        try:
            return datetime.fromisoformat(f"{fallback_date}T{value.strip()}:00")
        except ValueError:
            return None
    return None


def _filter_ground_window(
    routes: list[dict[str, Any]],
    travel_date: str,
    *,
    depart_after: str | None = None,
    arrive_before: str | None = None,
) -> list[dict[str, Any]]:
    lower = _wall_datetime(depart_after, travel_date) if depart_after else None
    upper = _wall_datetime(arrive_before, travel_date) if arrive_before else None
    if lower is None and upper is None:
        return routes
    output: list[dict[str, Any]] = []
    for route in routes:
        departure_value = route.get("departure")
        departure = ((departure_value or {}).get("time") if isinstance(departure_value, dict) else departure_value)
        arrival_value = route.get("arrival")
        arrival = ((arrival_value or {}).get("time") if isinstance(arrival_value, dict) else arrival_value)
        timezone_name = route.get("timezone") if isinstance(route.get("timezone"), str) else None
        dep_dt = _wall_datetime(departure, timezone_name=timezone_name)
        arr_dt = _wall_datetime(arrival, timezone_name=timezone_name)
        if lower is not None and (dep_dt is None or dep_dt < lower):
            continue
        if upper is not None and (arr_dt is None or arr_dt > upper):
            continue
        output.append(route)
    return output

def _list_from(data: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    nested = data.get("data")
    if nested is not None and nested is not data:
        return _list_from(nested, keys)
    return []


def _airport_code(value: Any) -> str | None:
    if isinstance(value, dict):
        code = value.get("code") or value.get("iata") or value.get("id")
        return str(code).upper() if code else None
    if isinstance(value, str):
        return value.upper()
    return None


def _compact_flight_leg(leg: dict[str, Any]) -> dict[str, Any]:
    departure_airport = leg.get("departure_airport") or leg.get("origin") or {}
    arrival_airport = leg.get("arrival_airport") or leg.get("destination") or {}
    return {
        key: value for key, value in {
            "direction": leg.get("direction"),
            "departure_airport": _airport_code(departure_airport),
            "arrival_airport": _airport_code(arrival_airport),
            "departure": provider_local_iso(leg.get("departure_time") or leg.get("departure")),
            "arrival": provider_local_iso(leg.get("arrival_time") or leg.get("arrival")),
            "duration_minutes": as_int(leg.get("duration") or leg.get("duration_minutes")) or None,
            "airline": leg.get("airline"),
            "airline_code": leg.get("airline_code"),
            "flight_number": leg.get("flight_number"),
        }.items() if value not in (None, "", [])
    }


def _flight_direction_summary(
    legs: list[dict[str, Any]],
    origin_iata: str,
    destination_iata: str,
    direction: str,
) -> dict[str, Any] | None:
    selected = [leg for leg in legs if leg.get("direction") == direction]
    if not selected and direction == "outbound":
        selected = []
        started = False
        for leg in legs:
            dep = leg.get("departure_airport")
            arr = leg.get("arrival_airport")
            if dep == origin_iata:
                started = True
            if started:
                selected.append(leg)
            if started and arr == destination_iata:
                break
    if not selected and direction == "inbound":
        selected = []
        started = False
        for leg in legs:
            dep = leg.get("departure_airport")
            arr = leg.get("arrival_airport")
            if dep == destination_iata:
                started = True
            if started:
                selected.append(leg)
            if started and arr == origin_iata:
                break
    if not selected:
        return None
    return {
        "departure_airport": selected[0].get("departure_airport"),
        "arrival_airport": selected[-1].get("arrival_airport"),
        "departure": selected[0].get("departure"),
        "arrival": selected[-1].get("arrival"),
        "stops": max(0, len(selected) - 1),
        "legs": selected,
    }


def _flight_route_matches(option: dict[str, Any], origin_iata: str, destination_iata: str) -> bool:
    outbound = option.get("outbound") if isinstance(option.get("outbound"), dict) else {}
    inbound = option.get("return") if isinstance(option.get("return"), dict) else {}
    if outbound.get("departure_airport") != origin_iata or outbound.get("arrival_airport") != destination_iata:
        return False
    if inbound and (inbound.get("departure_airport") != destination_iata or inbound.get("arrival_airport") != origin_iata):
        return False
    return True


def _stop_floor_from_warnings(warnings: Any) -> int:
    if not isinstance(warnings, list):
        return 0
    text = " ".join(str(item).casefold() for item in warnings)
    if re.search(r"(?:two[- ]stop|2\s*stops?)", text):
        return 2
    if re.search(r"(?:one[- ]stop|1\s*stops?|multi[- ]stop)", text):
        return 1
    return 0


def _bounded_price(value: Any, limit: float) -> float:
    price = as_float(value)
    return price if 0 < price <= limit else 0.0


def compact_flight_options(
    data: Any,
    origin_iata: str,
    destination_iata: str,
    max_results: int,
) -> list[dict[str, Any]]:
    rows = _list_from(data, ("flights", "results", "offers", "itineraries"))
    output: list[dict[str, Any]] = []
    for item in rows:
        raw_legs = item.get("legs") if isinstance(item.get("legs"), list) else []
        legs = [_compact_flight_leg(leg) for leg in raw_legs if isinstance(leg, dict)]
        price = as_float(item.get("price"))
        currency = item.get("currency") or ("EUR" if price > 0 else None)
        original: dict[str, Any] = {}
        if price > 0 and currency and str(currency).upper() != "EUR":
            # Dollar-Preise (Skiplagged) sonst nicht mit Euro-Preisen vergleichbar; ohne EZB-Kurs bleibt der Preis unverändert.
            converted = fx.to_eur(price, currency)
            if converted is not None:
                original = {"original_price": round(price, 2), "original_currency": str(currency).upper()}
                price, currency = converted, "EUR"
        option: dict[str, Any] = {
            **original,
            "price": round(price, 2) if price > 0 else None,
            "currency": currency,
            "duration_minutes": as_int(item.get("duration")) or None,
            "stops": as_int(item.get("stops")),
            "provider": item.get("provider") or item.get("airline") or item.get("cheapest_source"),
            "fare_type": item.get("fare_type"),
            "booking_url": item.get("booking_url"),
            "outbound": _flight_direction_summary(legs, origin_iata, destination_iata, "outbound"),
            "return": _flight_direction_summary(legs, origin_iata, destination_iata, "inbound"),
            "warnings": item.get("warnings") if isinstance(item.get("warnings"), list) else None,
            "confidence": item.get("confidence") if isinstance(item.get("confidence"), dict) else None,
        }
        stop_floor = _stop_floor_from_warnings(option.get("warnings"))
        option["stops"] = max(as_int(option.get("stops")), stop_floor)
        for side in ("outbound", "return"):
            section = option.get(side)
            if isinstance(section, dict):
                section["stops"] = max(as_int(section.get("stops")), stop_floor)
        if not _flight_route_matches(option, origin_iata, destination_iata):
            continue
        output.append({key: value for key, value in option.items() if value not in (None, "", [])})
        if len(output) >= max_results:
            break
    return output


def _chronology_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _ordered_before(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return False
    if (left.tzinfo is None) != (right.tzinfo is None):
        left = left.replace(tzinfo=None)
        right = right.replace(tzinfo=None)
    return left < right


def flight_chronology_issues(option: dict[str, Any], expected_return_date: str | None = None) -> list[str]:
    """Verwirf unmögliche Hin-/Rückflüge vor der öffentlichen Ausgabe."""
    outbound = option.get("outbound") if isinstance(option.get("outbound"), dict) else {}
    inbound = option.get("return") if isinstance(option.get("return"), dict) else {}
    out_dep = _chronology_datetime(outbound.get("departure"))
    out_arr = _chronology_datetime(outbound.get("arrival"))
    ret_dep = _chronology_datetime(inbound.get("departure"))
    ret_arr = _chronology_datetime(inbound.get("arrival"))
    issues: list[str] = []

    if out_dep and out_arr and not _ordered_before(out_dep, out_arr):
        issues.append("outbound_arrival_not_after_departure")
    if expected_return_date:
        if ret_dep is None:
            issues.append("return_departure_missing")
        elif ret_dep.date().isoformat() != expected_return_date:
            issues.append("return_date_mismatch")
    if ret_dep is not None and out_arr is not None and not _ordered_before(out_arr, ret_dep):
        issues.append("return_not_after_outbound_arrival")
    if ret_dep is not None and ret_arr is not None and not _ordered_before(ret_dep, ret_arr):
        issues.append("return_arrival_not_after_departure")
    for direction, section, departure, arrival in (
        ("outbound", outbound, out_dep, out_arr),
        ("return", inbound, ret_dep, ret_arr),
    ):
        if departure is None or arrival is None or as_int(section.get("stops")) != 0:
            continue
        if (arrival - departure).total_seconds() > 20 * 60 * 60:
            issues.append(f"{direction}_nonstop_duration_implausible")
    return issues


_FLIGHT_PRIMARY_PROVIDERS = ("skiplagged", "ryanair", "vueling", "easyjet")
_FLIGHT_SECONDARY_PROVIDERS = ("transavia", "norwegian", "afklm", "wizzair")


def _command_status(provider: str, result: dict[str, Any], elapsed: float, result_count: int = 0) -> dict[str, Any]:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    code = raw.get("code")
    error = result.get("error") if not result.get("ok") else None
    return {
        "provider": provider,
        "ok": bool(result.get("ok")),
        "timed_out": code == 124,
        "result_count": result_count,
        "elapsed_ms": int(elapsed * 1000),
        "error": error,
    }


async def _run_provider_json(
    provider: str,
    command: list[str],
    timeout: int,
    semaphore: asyncio.Semaphore,
) -> tuple[str, dict[str, Any], float]:
    async with semaphore:
        started = time.monotonic()
        result = await asyncio.to_thread(run_json_command, command, timeout)
        return provider, result, time.monotonic() - started


def _flight_fingerprint(option: dict[str, Any]) -> tuple[Any, ...]:
    outbound = option.get("outbound") if isinstance(option.get("outbound"), dict) else {}
    inbound = option.get("return") if isinstance(option.get("return"), dict) else {}
    return (
        outbound.get("departure_airport"), outbound.get("arrival_airport"),
        outbound.get("departure"), outbound.get("arrival"),
        inbound.get("departure_airport"), inbound.get("arrival_airport"),
        inbound.get("departure"), inbound.get("arrival"),
    )


def _merge_flight_options(options: list[dict[str, Any]], max_results: int) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for option in options:
        key = _flight_fingerprint(option)
        if not all(key[:4]):
            continue
        existing = merged.get(key)
        source = str(option.get("provider") or "trvl").strip()
        if existing is None:
            item = dict(option)
            item["sources"] = [source] if source else []
            merged[key] = item
            continue
        sources = list(existing.get("sources") or [])
        if source and source not in sources:
            sources.append(source)
        old_price = as_float(existing.get("price")) or 1_000_000
        new_price = as_float(option.get("price")) or 1_000_000
        if new_price < old_price:
            replacement = dict(option)
            replacement["sources"] = sources
            merged[key] = replacement
        else:
            existing["sources"] = sources

    rows = list(merged.values())
    rows.sort(key=lambda item: (
        as_float(item.get("price")) or 1_000_000,
        as_int(item.get("duration_minutes")) or 1_000_000,
        as_int(item.get("stops")),
    ))
    return rows[:max_results]


async def flight_search(request: FlightRequest) -> dict[str, Any]:
    base = [
        TRVL_BIN,
        "flights",
        request.origin_iata,
        request.destination_iata,
        request.departure_date,
        "--format", "json",
        "--currency", "EUR",
        "--sort", "cheapest",
        "--adults", str(request.adults),
        "--cabin", request.cabin,
        "--stops", request.stops,
    ]
    if request.return_date:
        base.extend(["--return", request.return_date])
    if request.max_price is not None:
        base.extend(["--max-price", str(request.max_price)])

    semaphore = asyncio.Semaphore(FLIGHT_PROVIDER_CONCURRENCY)
    await fx.ensure_rates()

    async def run_group(providers: tuple[str, ...]):
        tasks = [asyncio.create_task(run_one(provider)) for provider in providers]
        return await asyncio.gather(*tasks)

    async def run_one(provider: str):
        if provider == "skiplagged" and os.environ.get("FLIGHT_NATIVE", "1") != "0":
            started = time.monotonic()
            native = await skiplagged.flights(
                request.origin_iata, request.destination_iata, request.departure_date, request.return_date,
                request.adults, request.cabin, request.stops,
            )
            if native.get("ok"):
                return provider, native, time.monotonic() - started
            # Direkter Weg fehlgeschlagen (zum Beispiel Ratenbegrenzung): wie bisher über trvl.
        return await _run_provider_json(provider, base + ["--provider", provider], FLIGHT_PROVIDER_TIMEOUT, semaphore)

    # Erste Welle: breit nutzbarer Aggregator plus die typischen europäischen
    # Low-Cost-Provider. Die zweite Welle startet nur, wenn noch zu wenige
    # verwertbare Flüge vorhanden sind. Das begrenzt CPU/RAM/Swap-Last.
    provider_results = list(await run_group(_FLIGHT_PRIMARY_PROVIDERS))

    def compact_results(rows):
        statuses: list[dict[str, Any]] = []
        valid_options: list[dict[str, Any]] = []
        rejected = 0
        for provider, result, elapsed in rows:
            options = compact_flight_options(
                result.get("data") if result.get("ok") else None,
                request.origin_iata,
                request.destination_iata,
                max(request.max_results * 4, 12),
            )
            for option in options:
                if not option.get("provider"):
                    option["provider"] = provider
                option["trvl_provider"] = provider
                issues = flight_chronology_issues(option, request.return_date)
                if issues:
                    rejected += 1
                    continue
                valid_options.append(option)
            statuses.append(_command_status(provider, result, elapsed, len(options)))
        return valid_options, statuses, rejected

    raw_options, provider_statuses, chronology_rejected = compact_results(provider_results)
    enough = len(_merge_flight_options(raw_options, max(request.max_results * 2, 4))) >= max(request.max_results, 3)

    if not enough:
        secondary_results = list(await run_group(_FLIGHT_SECONDARY_PROVIDERS))
        second_options, second_statuses, second_rejected = compact_results(secondary_results)
        raw_options.extend(second_options)
        provider_statuses.extend(second_statuses)
        chronology_rejected += second_rejected
    else:
        provider_statuses.extend({
            "provider": provider,
            "ok": False,
            "skipped": True,
            "skipped_reason": "primary_providers_already_returned_enough_options",
            "result_count": 0,
        } for provider in _FLIGHT_SECONDARY_PROVIDERS)

    options = _merge_flight_options(raw_options, request.max_results)
    manual_url = build_google_flights_url(
        request.origin_iata, request.destination_iata, request.departure_date, request.return_date
    )
    successful = [item for item in provider_statuses if item.get("ok")]
    timed_out = [item["provider"] for item in provider_statuses if item.get("timed_out")]
    return {
        "status": "ok" if options else "manual_required",
        "query": request.model_dump(),
        "flight_options": options,
        "result_count": len(options),
        "chronology_rejected_count": chronology_rejected,
        "error": None if options else (
            "Keine chronologisch konsistenten Flugangebote aus den einzeln abgefragten Providern geliefert."
        ),
        "manual_booking_url": manual_url,
        "provider_statuses": provider_statuses,
        "provider_summary": {
            "queried": sum(1 for item in provider_statuses if not item.get("skipped")),
            "succeeded": len(successful),
            "timed_out": timed_out,
            "default_aggregate_used": False,
        },
    }

def _hotel_verified_total(item: dict[str, Any]) -> float:
    totals: list[float] = []
    for room in item.get("room_types") or []:
        if isinstance(room, dict):
            value = _bounded_price(room.get("total_price"), MAX_HOTEL_TOTAL_EUR)
            if value > 0:
                totals.append(value)
    for source in item.get("sources") or []:
        if not isinstance(source, dict):
            continue
        basis = str(source.get("price_basis") or "").casefold()
        if "total" in basis:
            value = _bounded_price(source.get("price"), MAX_HOTEL_TOTAL_EUR)
            if value > 0:
                totals.append(value)
    return min(totals) if totals else 0.0


def _hotel_nightly(item: dict[str, Any]) -> float:
    values: list[float] = []
    for room in item.get("room_types") or []:
        if isinstance(room, dict):
            value = _bounded_price(room.get("nightly_price"), MAX_HOTEL_NIGHTLY_EUR)
            if value > 0:
                values.append(value)
    basis = str(item.get("price_basis") or "").casefold()
    if "night" in basis:
        value = _bounded_price(item.get("price"), MAX_HOTEL_NIGHTLY_EUR)
        if value > 0:
            values.append(value)
    return min(values) if values else 0.0


# Anbieter für Monats- und Langzeitmieten: Ihr „Preis“ ist eine Monatsmiete, keine Übernachtung (Spotahome zeigt
# zum Beispiel 1600 € für eine Wohnung in Rom). Solche Treffer gehören nicht in einen Reisevergleich.
LONG_TERM_RENTAL_PROVIDERS = frozenset({
    "spotahome", "uniplaces", "housinganywhere", "flatio", "wunderflats", "landing", "blueground",
})


def _is_long_term_rental(item: dict[str, Any]) -> bool:
    """True, wenn der Treffer nur von Langzeitmiet-Anbietern stammt oder als Monatspreis ausgewiesen ist."""
    if str(item.get("price_basis") or "").casefold() == "monthly":
        return True
    providers = {
        str(source.get("provider") or "").casefold()
        for source in item.get("sources") or []
        if isinstance(source, dict)
    }
    providers.discard("")
    return bool(providers) and providers <= LONG_TERM_RENTAL_PROVIDERS


def compact_hotel_options(data: Any, max_results: int) -> list[dict[str, Any]]:
    rows = [item for item in _list_from(data, ("hotels", "results", "properties", "offers")) if not _is_long_term_rental(item)]
    output: list[dict[str, Any]] = []
    for item in rows:
        total = _hotel_verified_total(item)
        nightly = _hotel_nightly(item)
        headline = _bounded_price(item.get("price"), MAX_HOTEL_TOTAL_EUR)
        option = {
            "name": item.get("name"),
            "rating": as_float(item.get("rating")) or None,
            "review_count": as_int(item.get("review_count")) or None,
            "stars": as_int(item.get("stars")) or None,
            "headline_price": round(headline, 2) if headline > 0 else None,
            "nightly_price": round(nightly, 2) if nightly > 0 else None,
            "verified_total_price": round(total, 2) if total > 0 else None,
            "currency": item.get("currency") or ("EUR" if headline > 0 or total > 0 else None),
            "price_basis": item.get("price_basis"),
            "price_confidence": item.get("price_confidence"),
            "address": item.get("address"),
            "property_type": item.get("property_type"),
            "booking_url": item.get("booking_url"),
            "freshness": item.get("freshness"),
        }
        compact = {key: value for key, value in option.items() if value not in (None, "", [])}
        if not any(compact.get(key) for key in ("verified_total_price", "nightly_price", "headline_price")):
            compact["price_status"] = "unverified"
        output.append(compact)

    def rank(item: dict[str, Any]) -> tuple[int, float, float]:
        verified = as_float(item.get("verified_total_price"))
        nightly = as_float(item.get("nightly_price"))
        headline = as_float(item.get("headline_price"))
        known = verified or nightly or headline or 1_000_000.0
        return (0 if verified > 0 else 1, known, -(as_float(item.get("rating"))))

    output.sort(key=rank)
    return output[:max_results]


def _hotel_option_matches(item: dict[str, Any], min_stars: int, property_type: str) -> bool:
    stars = as_int(item.get("stars"))
    if min_stars > 0 and stars < min_stars:
        return False

    requested = str(property_type or "any").casefold()
    if requested == "any":
        return True

    raw_type = str(item.get("property_type") or "").casefold().strip()
    text = " ".join([raw_type, str(item.get("name") or "").casefold(), str(item.get("booking_url") or "").casefold()])
    type_tokens = {
        "hostel": ("hostel", "jugendherberge"),
        "apartment": ("apartment", "appartement", "ferienwohnung"),
        "resort": ("resort",),
        "bnb": ("bnb", "b&b", "bed and breakfast"),
        "villa": ("villa",),
    }
    if requested in type_tokens:
        return any(token in text for token in type_tokens[requested])
    if requested == "hotel":
        if any(token in text for token in ("hostel", "jugendherberge", "apartment", "appartement", "ferienwohnung", "bnb", "b&b", "villa")):
            return False
        # Die Sternanforderung ist der harte Hotelstandard. Das bleibt korrekt,
        # auch wenn ein Provider property_type nicht mitsendet.
        return stars >= max(1, min_stars)
    return raw_type == requested



def _normalize_property_types(options: list[dict[str, Any]], requested: str) -> list[dict[str, Any]]:
    if requested == "any":
        return options
    return [{**item, "property_type": requested} for item in options]

def _hotel_rank(item: dict[str, Any]) -> tuple[int, float, float]:
    return (
        0 if as_float(item.get("verified_total_price")) > 0 else 1,
        as_float(item.get("verified_total_price"))
        or as_float(item.get("nightly_price"))
        or as_float(item.get("headline_price"))
        or 1_000_000.0,
        -(as_float(item.get("rating"))),
    )


async def hotel_search(request: HotelRequest) -> dict[str, Any]:
    base = [
        TRVL_BIN,
        "hotels",
        request.location,
        "--checkin", request.checkin_date,
        "--checkout", request.checkout_date,
        "--guests", str(request.adults),
        "--currency", "EUR",
        "--sort", "cheapest",
        "--format", "json",
    ]
    if request.min_stars > 0:
        base.extend(["--stars", str(request.min_stars)])
    if request.property_type != "any":
        base.extend(["--property-type", request.property_type])
    if request.min_rating is not None:
        base.extend(["--min-rating", str(request.min_rating)])
    if request.max_nightly_price is not None:
        base.extend(["--max-price", str(request.max_nightly_price)])

    attempts: list[dict[str, Any]] = []

    options: list[dict[str, Any]] = []
    if os.environ.get("HOTEL_NATIVE", "1") != "0" and request.property_type in ("hotel", "any"):
        # Zuerst der offene Dienst von Skiplagged: echte Hotels mit Nacht- und Gesamtpreis, ohne trvl.
        started = time.monotonic()
        native = await skiplagged.hotels(request.location, request.checkin_date, request.checkout_date, request.adults)
        native_options = _normalize_property_types([
            item for item in compact_hotel_options(
                native.get("data") if native.get("ok") else None, max(request.max_results * 5, request.max_results),
            )
            if _hotel_option_matches(item, request.min_stars, request.property_type)
        ], request.property_type)
        attempts.append(_command_status("skiplagged_hotels", native, time.monotonic() - started, len(native_options)))
        options = native_options[: request.max_results]

    if options:
        return _hotel_result(request, options, attempts)

    # Zuerst die vollständige Suche mit Zimmerpreisen. Sie hat ein hartes Limit.
    started = time.monotonic()
    enriched = await asyncio.to_thread(run_json_command, base, HOTEL_ENRICH_TIMEOUT)
    elapsed = time.monotonic() - started
    enriched_raw = compact_hotel_options(
        enriched.get("data") if enriched.get("ok") else None,
        max(request.max_results * 5, request.max_results),
    )
    enriched_options = _normalize_property_types([
        item for item in enriched_raw
        if _hotel_option_matches(item, request.min_stars, request.property_type)
    ], request.property_type)
    attempts.append(_command_status("trvl_hotels_enriched", enriched, elapsed, len(enriched_options)))

    options = enriched_options[: request.max_results]
    has_verified = any(as_float(item.get("verified_total_price")) > 0 for item in options)

    # Wenn die Zimmeranreicherung hängt oder keine passenden Ergebnisse liefert,
    # fällt die Suche auf den schnelleren Headline-Modus zurück. Dieser ist klar
    # als nicht vollständig verifizierter Preis gekennzeichnet.
    if not options or not has_verified:
        headline_command = base + ["--enrich-rooms=false"]
        started = time.monotonic()
        headline = await asyncio.to_thread(run_json_command, headline_command, HOTEL_HEADLINE_TIMEOUT)
        elapsed = time.monotonic() - started
        headline_raw = compact_hotel_options(
            headline.get("data") if headline.get("ok") else None,
            max(request.max_results * 5, request.max_results),
        )
        headline_options = _normalize_property_types([
            item for item in headline_raw
            if _hotel_option_matches(item, request.min_stars, request.property_type)
        ], request.property_type)
        attempts.append(_command_status("trvl_hotels_headline", headline, elapsed, len(headline_options)))

        if not options:
            options = headline_options[: request.max_results]
        elif len(options) < request.max_results:
            known = {(str(item.get("name") or "").casefold(), str(item.get("address") or "").casefold()) for item in options}
            for item in headline_options:
                key = (str(item.get("name") or "").casefold(), str(item.get("address") or "").casefold())
                if key in known:
                    continue
                options.append(item)
                known.add(key)
                if len(options) >= request.max_results:
                    break

    return _hotel_result(request, options, attempts)


def _hotel_result(request: HotelRequest, options: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    manual_url = build_google_hotels_url(request.location, request.checkin_date, request.checkout_date)
    return {
        "status": "ok" if options else "manual_required",
        "query": request.model_dump(),
        "hotel_options": options,
        "result_count": len(options),
        "verified_total_count": sum(1 for item in options if as_float(item.get("verified_total_price")) > 0),
        "error": None if options else "Keine auswertbaren Hotelangebote innerhalb der Provider-Zeitlimits geliefert.",
        "manual_booking_url": manual_url,
        "provider_statuses": attempts,
        "price_note": (
            "Nur verified_total_price wird als vollständiger Aufenthaltspreis gewertet; "
            "Headline- oder Nachtpreise bleiben als nicht vollständig verifiziert markiert."
        ),
    }


def _ground_stop(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: result for key, result in {
            "city": value.get("city"),
            "station": value.get("station"),
            "station_id": value.get("station_id"),
            "address": value.get("address"),
            "latitude": value.get("latitude"),
            "longitude": value.get("longitude"),
            "time": provider_local_iso(value.get("time")),
        }.items() if result not in (None, "", [])
    }


def compact_ground_options(data: Any, max_results: int | None = 6) -> list[dict[str, Any]]:
    rows = _list_from(data, ("routes", "results", "trips", "itineraries"))
    output: list[dict[str, Any]] = []
    for item in rows:
        price = as_float(item.get("comparable_price")) or as_float(item.get("price"))
        option = {
            "provider": item.get("provider"),
            "type": item.get("type") or item.get("mode"),
            "direction": item.get("direction"),
            "departure": _ground_stop(item.get("departure")),
            "arrival": _ground_stop(item.get("arrival")),
            "duration_minutes": as_int(item.get("duration_minutes") or item.get("duration")) or None,
            "transfers": as_int(item.get("transfers")),
            "price": round(price, 2) if price > 0 else None,
            "currency": item.get("currency") or ("EUR" if price > 0 else None),
            "booking_url": item.get("booking_url") or item.get("book_url"),
            "legs": [
                _compact_ground_leg(leg)
                for leg in (item.get("legs") or [])
                if isinstance(leg, dict)
            ],
        }
        output.append({key: value for key, value in option.items() if value not in (None, "", [])})
        if max_results is not None and len(output) >= max_results:
            break
    return output


def compact_route_transfer_options(data: Any, max_results: int = 6) -> list[dict[str, Any]]:
    """Normalisiert trvl route --arrive-by als Rücktransfer-Fallback."""
    rows = _list_from(data, ("itineraries",))
    output: list[dict[str, Any]] = []
    for item in rows:
        legs = [leg for leg in (item.get("legs") or []) if isinstance(leg, dict)]
        if not legs:
            continue
        if any(str(leg.get("mode") or "").casefold() == "flight" for leg in legs):
            continue
        first = legs[0]
        last = legs[-1]
        providers: list[str] = []
        modes: list[str] = []
        booking_url = None
        for leg in legs:
            provider = str(leg.get("provider") or "").strip()
            mode = str(leg.get("mode") or "").strip()
            if provider and provider not in providers:
                providers.append(provider)
            if mode and mode not in modes:
                modes.append(mode)
            if not booking_url and leg.get("booking_url"):
                booking_url = leg.get("booking_url")
        price = as_float(item.get("total_price"))
        option = {
            "provider": " + ".join(providers) if providers else "trvl route",
            "type": modes[0] if len(modes) == 1 else "mixed",
            "direction": "destination_to_airport",
            "departure": {
                "station": first.get("from") or first.get("from_code"),
                "time": provider_local_iso(item.get("depart_time") or first.get("departure")),
            },
            "arrival": {
                "station": last.get("to") or last.get("to_code"),
                "time": provider_local_iso(item.get("arrive_time") or last.get("arrival")),
            },
            "duration_minutes": as_int(item.get("total_duration")) or None,
            "transfers": as_int(item.get("transfers")),
            "price": round(price, 2) if price > 0 else None,
            "currency": item.get("currency") or ("EUR" if price > 0 else None),
            "booking_url": booking_url,
            "route_fallback": True,
        }
        output.append({key: value for key, value in option.items() if value not in (None, "", [])})
        if len(output) >= max_results:
            break
    return output


async def flix_search(request: ReiseRequest) -> dict[str, Any]:
    origin_query = _flix_city_query(request.origin)
    destination_query = _flix_city_query(request.destination)
    command = [
        TRVL_BIN,
        "ground",
        origin_query,
        destination_query,
        request.travel_date,
        "--provider", "flixbus",
        "--currency", "EUR",
        "--format", "json",
    ]
    started = time.monotonic()
    source = "flix-api"
    result: dict[str, Any] = {"ok": False}
    if os.environ.get("FLIX_NATIVE", "1") != "0":
        result = await flix_api.search(origin_query, destination_query, request.travel_date)
    if not result.get("ok") or not (result.get("data") or {}).get("routes"):
        # Direkte Flix-Abfrage ohne Ergebnis oder fehlgeschlagen: wie bisher über trvl.
        source = "trvl"
        result = await asyncio.to_thread(run_json_command, command, GROUND_PROVIDER_TIMEOUT)
    elapsed = time.monotonic() - started
    routes = compact_ground_options(
        result.get("data") if result.get("ok") else None,
        None,
    )

    # Exakte Flix-Haltestellen-IDs werden über das öffentliche Netz in Namen
    # und Koordinaten aufgelöst. Die Anreicherung ist optional; ein Ausfall der
    # Metadaten macht eine ansonsten gültige Flix-Suche nicht unbrauchbar.
    station_directory: dict[str, dict[str, Any]] = {}
    if routes and any(
        _looks_like_station_id(
            ((leg.get(side) or {}).get("station"))
        )
        for route in routes
        for leg in (route.get("legs") or [])
        if isinstance(leg, dict)
        for side in ("departure", "arrival")
    ):
        station_directory = await _flix_station_directory(request.origin, request.destination)
        if station_directory:
            routes = _enrich_flix_routes(routes, station_directory)

    train_routes: list[dict[str, Any]] = []
    bus_routes: list[dict[str, Any]] = []
    mixed_routes: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    origin_station_ids = set(request.origin_station.ids_for("flix") if request.origin_station else [])
    destination_station_ids = set(request.destination_station.ids_for("flix") if request.destination_station else [])
    if request.flix_origin_stop_id:
        origin_station_ids.add(request.flix_origin_stop_id)
    if request.flix_destination_stop_id:
        destination_station_ids.add(request.flix_destination_stop_id)
    for route in routes:
        if request.flix_origin_stop_id and str((route.get("departure") or {}).get("station_id") or "") != request.flix_origin_stop_id:
            continue
        if request.flix_destination_stop_id and str((route.get("arrival") or {}).get("station_id") or "") != request.flix_destination_stop_id:
            continue
        if not _flix_endpoint_candidate_allowed(route.get("departure"), request.origin):
            continue
        if not _flix_endpoint_candidate_allowed(route.get("arrival"), request.destination):
            continue
        if not route_departure_in_window(route, request.travel_date, request.departure_after):
            continue
        mode = str(route.get("type") or "").casefold()
        if mode == "train":
            kind = "train"
        elif mode == "bus":
            kind = "bus"
        elif mode == "mixed":
            kind = "mixed"
        else:
            legs = [leg for leg in (route.get("legs") or []) if isinstance(leg, dict)]
            leg_modes = {str(leg.get("type") or "").casefold() for leg in legs}
            if leg_modes == {"train"}:
                kind = "train"
            elif leg_modes == {"bus"}:
                kind = "bus"
            elif "train" in leg_modes and "bus" in leg_modes:
                kind = "mixed"
            else:
                kind = "bus"

        if kind == "train" and not request.include_flixtrain:
            continue
        if kind == "bus" and not request.include_flixbus:
            continue
        if kind == "mixed" and not (request.include_flixbus and request.include_flixtrain):
            continue
        if request.max_transfers is not None and as_int(route.get("transfers")) > request.max_transfers:
            continue

        route["provider_code"] = "flix"
        route["flix_kind"] = kind
        route["provider"] = (
            "FlixTrain" if kind == "train"
            else "FlixBus" if kind == "bus"
            else "FlixBus/FlixTrain"
        )
        departure_access = _flix_transfer_requirement(
            route.get("departure"), request.origin, "origin_to_flix_stop", origin_station_ids,
        )
        arrival_egress = _flix_transfer_requirement(
            route.get("arrival"), request.destination, "flix_stop_to_destination", destination_station_ids,
        )
        if departure_access:
            route["departure_access"] = departure_access
            route["direct_from_requested_origin"] = False
        else:
            route["direct_from_requested_origin"] = True
        if arrival_egress:
            route["arrival_egress"] = arrival_egress
            route["direct_to_requested_destination"] = False
        else:
            route["direct_to_requested_destination"] = True
        self_managed = int(bool(departure_access)) + int(bool(arrival_egress))
        if self_managed:
            route["flix_transfers"] = as_int(route.get("transfers"))
            route["self_managed_transfers"] = self_managed
            route["transfers"] = as_int(route.get("transfers")) + self_managed
            route["price_complete"] = False
            route["price_note"] = "Flix-Preis ohne erforderlichen Zubringer/Weitertransfer; Gesamtpreis offen."
        else:
            route["price_complete"] = True
        fingerprint = _ground_fingerprint(route)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        if kind == "train":
            train_routes.append(route)
        elif kind == "bus":
            bus_routes.append(route)
        else:
            mixed_routes.append(route)

    ranked_train = rank_routes(train_routes, request.preference)
    ranked_bus = rank_routes(bus_routes, request.preference)
    ranked_mixed = rank_routes(mixed_routes, request.preference)
    ranked_all = rank_routes(ranked_train + ranked_bus + ranked_mixed, request.preference)
    selected: list[dict[str, Any]] = []
    if request.max_results == 1:
        selected = ranked_all[:1]
    else:
        for index in range(2):
            for group in (ranked_train[:2], ranked_bus[:2]):
                if index < len(group) and len(selected) < request.max_results:
                    selected.append(group[index])
        selected_keys = {_ground_fingerprint(route) for route in selected}
        for route in ([] if len(selected) >= request.max_results else ranked_all):
            key = _ground_fingerprint(route)
            if key in selected_keys:
                continue
            selected.append(route)
            selected_keys.add(key)
            if len(selected) >= request.max_results:
                break

    return {
        "status": "ok" if selected else "empty",
        "routes": selected,
        "candidate_routes": ranked_all,
        "candidate_counts": {
            "train": len(ranked_train),
            "bus": len(ranked_bus),
            "mixed": len(ranked_mixed),
        },
        "raw_route_count": len(routes),
        "station_metadata_resolved": bool(station_directory),
        "provider_status": {**_command_status("flixbus", result, elapsed, len(routes)), "source": source},
        "error": None if selected else result.get("error"),
    }


def _ground_fingerprint(route: dict[str, Any]) -> tuple[Any, ...]:
    departure = route.get("departure")
    arrival = route.get("arrival")
    departure_stop = departure if isinstance(departure, dict) else {}
    arrival_stop = arrival if isinstance(arrival, dict) else {}
    return (
        str(route.get("type") or "").casefold(),
        departure_stop.get("station") or departure_stop.get("city") or route.get("origin"),
        departure_stop.get("time") or (departure if isinstance(departure, str) else None),
        arrival_stop.get("station") or arrival_stop.get("city") or route.get("destination"),
        arrival_stop.get("time") or (arrival if isinstance(arrival, str) else None),
    )


def _merge_ground_options(routes: list[dict[str, Any]], max_results: int) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for route in routes:
        key = _ground_fingerprint(route)
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(route)
            continue
        old_price = as_float(existing.get("price")) or 1_000_000
        new_price = as_float(route.get("price")) or 1_000_000
        if new_price < old_price:
            merged[key] = dict(route)
    rows = list(merged.values())
    rows.sort(key=lambda item: (
        as_float(item.get("price")) or 1_000_000,
        as_int(item.get("duration_minutes")) or 1_000_000,
        as_int(item.get("transfers")),
    ))
    return rows[:max_results]


async def _provider_ground_commands(
    providers: tuple[str, ...],
    command_builder,
    *,
    timeout: int,
    concurrency: int,
    compact_limit: int,
    native: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Bodenanbieter abfragen. ``native`` bildet Anbieter auf eigene Direktabfragen ab (sie laufen vor trvl);
    ohne trvl (``TRVL_ENABLED=0``) werden Anbieter ohne eigenen Weg übersprungen."""
    semaphore = asyncio.Semaphore(concurrency)
    native = native or {}

    async def one(provider: str):
        if provider in native:
            started = time.monotonic()
            result = await native[provider]()
            if result.get("ok") and (result.get("data") or {}).get("routes"):
                return provider, result, time.monotonic() - started
        if not TRVL_ENABLED:
            return provider, {"ok": False, "error": "kein eigener Weg; trvl ist abgeschaltet", "skipped": True}, 0.0
        return await _run_provider_json(provider, command_builder(provider), timeout, semaphore)

    results = await asyncio.gather(*(asyncio.create_task(one(provider)) for provider in providers))
    routes: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for provider, result, elapsed in results:
        found = compact_ground_options(result.get("data") if result.get("ok") else None, compact_limit)
        for route in found:
            if not route.get("provider"):
                route["provider"] = provider
            route["trvl_provider"] = provider
        routes.extend(found)
        status = _command_status(provider, result, elapsed, len(found))
        if result.get("skipped"):
            status["skipped"] = True
        statuses.append(status)
    return routes, statuses


async def _direct_transitous_transfer(
    airport_iata: str, destination: str, travel_date: str,
    *, depart_after: str | None = None, arrive_before: str | None = None, reverse: bool = False,
    max_results: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    destination_query = CITY_TRANSIT_QUERIES.get(destination.casefold().strip(), destination)
    airport_query = AIRPORT_TRANSIT_QUERIES.get(
        airport_iata.upper(), f"{destination} Airport {airport_iata.upper()}"
    )
    request = ReiseRequest(
        origin=destination_query if reverse else airport_query,
        destination=airport_query if reverse else destination_query,
        travel_date=travel_date,
        departure_after=("00:00" if reverse else (depart_after or "00:00")),
        include_flixtrain=False,
        include_flixbus=False,
        split_ticket_check=False,
        max_results=max_results,
    )
    started = time.monotonic()
    result = await transitous_direct_search(request)
    elapsed = time.monotonic() - started
    routes = result.get("routes") or []
    routes = _filter_ground_window(
        routes, travel_date, depart_after=depart_after if not reverse else None,
        arrive_before=arrive_before if reverse else None,
    )
    diagnostic = result.get("diagnostic") or {}
    status = {
        "provider": "transitous_direct",
        "ok": bool(routes),
        "timed_out": False,
        "result_count": len(routes),
        "elapsed_ms": round(elapsed * 1000),
        "error": None if routes else diagnostic.get("error") or "Keine passende Transitous-Route",
    }
    return routes, {key: value for key, value in status.items() if value is not None}


async def airport_transfer_search(
    airport_iata: str,
    destination: str,
    travel_date: str,
    *,
    arrival_after: str | None = None,
    max_results: int = 6,
) -> dict[str, Any]:
    # Kein Provider-Sammelpfad. Transitous deckt den öffentlichen Nahverkehr ab,
    # Taxi liefert eine unabhängige Alternative und Flix optional den Fernbus.
    providers = ("transitous", "taxi", "flixbus")

    def command(provider: str) -> list[str]:
        cmd = [
            TRVL_BIN, "airport-transfer", airport_iata, destination, travel_date,
            "--provider", provider,
            "--currency", "EUR", "--format", "json",
        ]
        if arrival_after:
            cmd.extend(["--arrival-after", arrival_after])
        return cmd

    airport_query = AIRPORT_TRANSIT_QUERIES.get(airport_iata.upper(), f"{airport_iata.upper()} Airport")
    raw_routes, statuses = await _provider_ground_commands(
        providers, command,
        timeout=TRANSFER_PROVIDER_TIMEOUT,
        concurrency=TRANSFER_PROVIDER_CONCURRENCY,
        compact_limit=max_results * 3,
        native={"flixbus": lambda: flix_api.search(_flix_city_query(airport_query), _flix_city_query(destination), travel_date)},
    )
    direct_done = False
    if not any(route.get("trvl_provider") == "transitous" for route in raw_routes):
        # Der öffentliche Nahverkehr ist der wichtigste Transfer: ohne trvl-Ergebnis fragt Traviorel Transitous selbst.
        direct_routes, direct_status = await _direct_transitous_transfer(
            airport_iata, destination, travel_date, depart_after=arrival_after, max_results=max_results,
        )
        raw_routes = raw_routes + direct_routes
        statuses.append(direct_status)
        direct_done = True
    routes = _filter_ground_window(raw_routes, travel_date, depart_after=arrival_after)
    routes = _merge_ground_options(routes, max_results)
    if not routes and not direct_done:
        fallback_routes, fallback_status = await _direct_transitous_transfer(
            airport_iata, destination, travel_date, depart_after=arrival_after, max_results=max_results,
        )
        routes = _merge_ground_options(fallback_routes, max_results)
        statuses.append(fallback_status)
    return {
        "status": "ok" if routes else "manual_required",
        "direction": "airport_to_destination",
        "airport_iata": airport_iata,
        "destination": destination,
        "date": travel_date,
        "depart_after": arrival_after,
        "options": routes,
        "provider_statuses": statuses,
        "error": None if routes else "Kein Transfer-Provider lieferte innerhalb seines Zeitlimits eine passende Verbindung.",
        "manual_url": build_google_maps_url(f"{airport_iata} Airport", destination),
    }


async def return_transfer_search(
    origin: str,
    airport_iata: str,
    travel_date: str,
    *,
    arrive_before: str | None = None,
    max_results: int = 6,
) -> dict[str, Any]:
    destination = f"{airport_iata} Airport"
    providers = ("transitous", "flixbus", "db")

    def command(provider: str) -> list[str]:
        return [
            TRVL_BIN, "ground", origin, destination, travel_date,
            "--provider", provider,
            "--currency", "EUR", "--format", "json",
        ]

    airport_query = AIRPORT_TRANSIT_QUERIES.get(airport_iata.upper(), f"{airport_iata.upper()} Airport")
    raw_routes, statuses = await _provider_ground_commands(
        providers, command,
        timeout=TRANSFER_PROVIDER_TIMEOUT,
        concurrency=TRANSFER_PROVIDER_CONCURRENCY,
        compact_limit=max_results * 3,
        native={"flixbus": lambda: flix_api.search(_flix_city_query(origin), _flix_city_query(airport_query), travel_date)},
    )
    direct_done = False
    if not any(route.get("trvl_provider") == "transitous" for route in raw_routes):
        direct_routes, direct_status = await _direct_transitous_transfer(
            airport_iata, origin, travel_date, arrive_before=arrive_before, reverse=True, max_results=max_results,
        )
        raw_routes = raw_routes + direct_routes
        statuses.append(direct_status)
        direct_done = True
    routes = _filter_ground_window(raw_routes, travel_date, arrive_before=arrive_before)
    routes = _merge_ground_options(routes, max_results)
    if not routes and not direct_done:
        fallback_routes, fallback_status = await _direct_transitous_transfer(
            airport_iata, origin, travel_date, arrive_before=arrive_before, reverse=True, max_results=max_results,
        )
        routes = _merge_ground_options(fallback_routes, max_results)
        statuses.append(fallback_status)
    source = "provider_parallel"

    # Nur als streng begrenzter letzter Versuch: trvl route kann zusätzliche
    # multimodale Optionen finden, darf aber maximal einen Provider-Timeout lang laufen.
    if not routes and arrive_before and TRVL_ENABLED:
        route_command = [
            TRVL_BIN, "route", origin, airport_iata, travel_date,
            "--arrive-by", arrive_before,
            "--avoid", "flight",
            "--currency", "EUR",
            "--sort", "price",
            "--format", "json",
        ]
        started = time.monotonic()
        route_result = await asyncio.to_thread(run_json_command, route_command, TRANSFER_PROVIDER_TIMEOUT)
        elapsed = time.monotonic() - started
        route_options = compact_route_transfer_options(
            route_result.get("data") if route_result.get("ok") else None,
            max_results * 2,
        )
        route_options = _filter_ground_window(route_options, travel_date, arrive_before=arrive_before)
        routes = _merge_ground_options(route_options, max_results)
        statuses.append(_command_status("route_arrive_by", route_result, elapsed, len(route_options)))
        if routes:
            source = "route_arrive_by"

    return {
        "status": "ok" if routes else "manual_required",
        "direction": "destination_to_airport",
        "origin": origin,
        "airport_iata": airport_iata,
        "date": travel_date,
        "arrive_before": arrive_before,
        "source": source,
        "options": routes,
        "provider_statuses": statuses,
        "error": None if routes else (
            "Kein Rücktransfer-Provider lieferte innerhalb seines Zeitlimits eine nachweisbar rechtzeitige Verbindung."
        ),
        "manual_url": build_google_maps_url(origin, destination),
    }

async def capability_report() -> dict[str, Any]:
    if not TRVL_ENABLED:
        return {"enabled": False, "note": "trvl ist abgeschaltet; Direktwege sind aktiv"}
    commands = ["flights", "hotels", "airport-transfer", "ground", "route"]
    report: dict[str, Any] = {}
    for command in commands:
        result = await asyncio.to_thread(run_command, [TRVL_BIN, command, "--help"], 20)
        report[command] = {"ok": bool(result.get("ok")), "error": result.get("stderr") if not result.get("ok") else None}
    return report


async def discover_flix_stops(request: ReiseRequest) -> dict[str, Any]:
    """Return only uniquely identified, resolved stops seen in current Flix routes."""
    probe = request.model_copy(update={
        "flix_origin_stop_id": None,
        "flix_destination_stop_id": None,
        "max_results": 12,
    })
    result = await flix_search(probe)

    def collect(side: str) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for route in result.get("candidate_routes") or []:
            stop = route.get(side)
            if not isinstance(stop, dict):
                continue
            station_id = str(stop.get("station_id") or "").strip()
            name = str(stop.get("station") or "").strip()
            if not station_id or not name or _looks_like_station_id(name):
                continue
            item = found.setdefault(station_id, {
                key: value for key, value in {
                    "station_id": station_id,
                    "name": name,
                    "city": stop.get("city"),
                    "latitude": stop.get("latitude"),
                    "longitude": stop.get("longitude"),
                    "address": stop.get("address"),
                    "types": [],
                }.items() if value not in (None, "", [])
            })
            item.setdefault("types", [])
            kind = route.get("flix_kind")
            if kind and kind not in item["types"]:
                item["types"].append(kind)
        return sorted(found.values(), key=lambda item: (str(item.get("name") or "").casefold(), item["station_id"]))

    return {
        "status": result.get("status"),
        "origin_stops": collect("departure"),
        "destination_stops": collect("arrival"),
        "raw_route_count": result.get("raw_route_count", 0),
        "provider_status": result.get("provider_status"),
    }
