from __future__ import annotations

import asyncio
import re
from math import asin, cos, radians, sin, sqrt
from typing import Any

import httpx

from .cache import cached_call
from .config import APP_VERSION, DB_API_URL, TRANSITOUS_TIMEOUT, TRANSITOUS_URL, TRANSITOUS_USER_AGENT
from .gtfs_flix import discover_stops
from .location_resolver import exact_location_key, has_airport_context, location_candidates, location_key
from .ground_transport import GroundTransportMode

_CACHE_TTL = 300
_RANKING_GENERATION = "stations-v12"


def _base_station_query(value: str) -> str:
    return " ".join(word for word in value.split() if exact_location_key(word) not in {"hbf", "hauptbahnhof", "bahnhof", "station"})


def _distance_km(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    try:
        lat1, lon1 = radians(float(left["latitude"])), radians(float(left["longitude"]))
        lat2, lon2 = radians(float(right["latitude"])), radians(float(right["longitude"]))
    except (KeyError, TypeError, ValueError):
        return None
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371 * 2 * asin(sqrt(value))


_SECONDARY_STATION_WORDS = {
    "ausgang", "südausgang", "nordausgang", "ostausgang", "westausgang",
    "eingang", "zugang", "bahnsteig", "gleis", "vorplatz", "parkplatz", "park",
    "nebenhaltestelle",
}


def _station_role_score(name: str, item: dict[str, Any] | None = None) -> int:
    words = set(exact_location_key(name).replace("-", " ").split())
    score = 400 if words & {"hbf", "hauptbahnhof", "central", "centrale", "centraal"} else 0
    if "bahnhof" in words:
        score += 220
    if words & _SECONDARY_STATION_WORDS:
        score -= 450
    item = item or {}
    modes = {str(mode).upper() for mode in item.get("modes") or []}
    if modes & {"HIGHSPEED_RAIL", "LONG_DISTANCE", "NIGHT_RAIL"}:
        score += 260
    elif "REGIONAL_RAIL" in modes:
        score += 180
    if "COACH" in modes:
        score += 120
    if item.get("parent_station"):
        score -= 220
    if str(item.get("location_type") or "") == "1" or item.get("is_station") is True:
        score += 100
    return score


def _score(query: str, name: str, provider: str, item: dict[str, Any] | None = None) -> int:
    exact_query, exact_name = exact_location_key(query), exact_location_key(name)
    alias_query, alias_name = location_key(query), location_key(name)
    score = {"db": 30, "transitous": 20, "flix": 10}.get(provider, 0)
    if exact_query == exact_name:
        city_only_penalty = 450 if len(exact_query.split()) == 1 else 0
        return score + 1000 - city_only_penalty + _station_role_score(name, item)
    if sorted(exact_query.split()) == sorted(exact_name.split()):
        return score + 950 + _station_role_score(name, item)
    if exact_location_key(_base_station_query(query)) == exact_name:
        return score + 850 + _station_role_score(name, item)
    if exact_name.startswith(exact_query + " "):
        score += 600
    elif alias_query == alias_name:
        score += 500
    elif alias_name.startswith(alias_query + " "):
        score += 350
    elif set(alias_query.split()) <= set(alias_name.split()):
        score += 200
    return score + _station_role_score(name, item)


async def _db_locations(query: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{DB_API_URL}/locations", params={"q": query})
        response.raise_for_status()
    data = response.json()
    if data.get("status") == "failed":
        raise RuntimeError("DB location sources failed")
    return data.get("locations") or []


async def _transitous_locations(query: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(
        timeout=TRANSITOUS_TIMEOUT,
        headers={"User-Agent": TRANSITOUS_USER_AGENT, "Accept": "application/json"},
    ) as client:
        response = await client.get(
            f"{TRANSITOUS_URL}/api/v1/geocode",
            params={"text": query, "type": "STOP", "language": "de", "numResults": 20},
        )
        response.raise_for_status()
    items = response.json()
    return [{
        "provider": "transitous",
        "provider_id": str(item["id"]),
        "name": str(item.get("name") or item["id"]),
        "latitude": item.get("lat", item.get("latitude")),
        "longitude": item.get("lon", item.get("longitude")),
        "country": item.get("country"),
        "region": next((area.get("name") for area in item.get("areas") or [] if area.get("unique")), None),
        "modes": item.get("modes") or [],
        "parent_station": item.get("parentStation") or item.get("parent_station"),
        "location_type": item.get("locationType") or item.get("location_type"),
    } for item in items if isinstance(item, dict) and item.get("id")]


async def _flix_locations(query: str) -> list[dict[str, Any]]:
    result = await discover_stops(query, query)
    return [{
        "provider": "flix", "provider_id": item["station_id"], "name": item["name"],
        "latitude": item.get("latitude"), "longitude": item.get("longitude"),
        "parent_station": item.get("parent_station"), "location_type": item.get("location_type"),
    } for item in result.get("origin_stops") or []]


async def _all_queries(loader, queries: tuple[str, ...]) -> list[dict[str, Any]]:
    results = await asyncio.gather(*(loader(query) for query in queries), return_exceptions=True)
    output: list[dict[str, Any]] = []
    if results and all(isinstance(result, BaseException) for result in results):
        raise results[0]
    for result in results:
        if not isinstance(result, BaseException): output.extend(result)
    return output


def _city_first_queries(query: str, candidates: list[dict[str, Any]], seen: list[str]) -> list[str]:
    """Use provider-supplied regions, not a hardcoded city/station map."""
    known = {exact_location_key(value) for value in seen}
    for item in candidates:
        region = str(item.get("region") or "").strip()
        if not region:
            continue
        match = re.fullmatch(r"(.+?)\s*,?\s+" + re.escape(region), query, flags=re.IGNORECASE)
        if match:
            flipped = region + " " + match[1].rstrip(", ")
            if exact_location_key(flipped) not in known:
                return [flipped]  # At most one extra lookup per provider.
    return []


def _station_mode_match(item: dict[str, Any], transport_mode: GroundTransportMode) -> bool | None:
    if transport_mode == "all":
        return True
    modes = {str(mode).upper() for mode in item.get("modes") or []}
    if not modes:
        return None
    selected = {"suburban": {"SUBURBAN"}, "bus": {"BUS", "COACH"},
                "local_train": {"SUBURBAN", "REGIONAL_RAIL"}}[transport_mode]
    return bool(modes & selected)


def _query_matches_station(query: str, item: dict[str, Any]) -> bool:
    anchors = set(location_key(_base_station_query(query)).split())
    if not anchors:
        return True
    offered = set(location_key(item["name"]).split()) | set(location_key(item.get("region")).split())
    return anchors <= offered


def nearby_modal_options(result: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """At most one co-located bus/rail pair, never a choice between distant places."""
    if result.get("transport_mode", "all") != "all":
        return []
    rows = result.get("stations") or []
    query = location_key(result.get("query"))
    explicit = bool(result.get("auto_selection")) or bool(rows and sorted(query.split()) == sorted(location_key(rows[0]["name"]).split()))
    if not explicit or any(query == location_key(row.get("region")) for row in rows if row.get("region")):
        return []
    buses = [row for row in rows if _station_mode_match(row, "bus") is True and _station_mode_match(row, "local_train") is not True]
    trains = [row for row in rows if _station_mode_match(row, "local_train") is True]
    for bus in buses:
        for train in trains:
            distance = _distance_km(bus, train)
            if distance is not None and distance <= 0.3 and bus.get("provider_id") != train.get("provider_id"):
                return [("bus", bus), ("local_train", train)]
    return []


def _station_identity(name: str) -> tuple[str, ...]:
    """Conservative spelling/word-order aliases, preserving stop qualifiers."""
    value = location_key(name)
    value = re.sub(r"\bcentral (?:train station|bus stop|station)\b", "hbf", value)
    value = re.sub(r"\b(?:hauptbahnhof|centrale|centraal)\b", "hbf", value)
    return tuple(sorted(word for word in value.split() if word not in {"bahnhof", "station"}))


def _same_group(group: dict[str, Any], item: dict[str, Any], transport_mode: GroundTransportMode = "all") -> bool:
    left_modes, right_modes = set(group.get("modes") or []), set(item.get("modes") or [])
    if left_modes and right_modes and not left_modes & right_modes:
        return False
    distance = _distance_km(group, item)
    if distance is None:
        return exact_location_key(group["name"]) == exact_location_key(item["name"])
    if distance > 0.75:
        return False
    provider = item["provider"]
    existing_id = group["provider_ids"].get(provider)
    if existing_id and existing_id != item["provider_id"] and location_key(group["name"]) != location_key(item["name"]):
        # A nearby tram stop is not an alias of a railway station. Keep multiple
        # platforms only when that provider explicitly supplies a shared parent.
        parent = item.get("parent_station")
        return bool(parent and parent == group.get("provider_parent_stations", {}).get(provider))
    # Proximity alone is not identity: a bus stop, station and nearby street
    # can all be within the radius. Never attach another provider's ID merely
    # because it was encountered first in the sorted catalog.
    return _station_identity(group["name"]) == _station_identity(item["name"])


async def _search_uncached(normalized: str, limit: int, transport_mode: GroundTransportMode = "all") -> dict[str, Any]:
    queries = list(location_candidates(normalized)[:2])
    base_query = _base_station_query(normalized)
    if base_query and exact_location_key(base_query) not in {exact_location_key(query) for query in queries}:
        queries.append(base_query)
    results = await asyncio.gather(
        _all_queries(_db_locations, queries),
        _all_queries(_transitous_locations, queries),
        _all_queries(_flix_locations, queries),
        return_exceptions=True,
    )
    candidates: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    for provider, result in zip(("db", "transitous", "flix"), results):
        if isinstance(result, BaseException):
            statuses[provider] = "failed"
            continue
        statuses[provider] = "ok"
        candidates.extend(result)

    extra_queries = _city_first_queries(normalized, candidates, queries)
    if extra_queries:
        extra = await asyncio.gather(_all_queries(_db_locations, extra_queries),
                                     _all_queries(_transitous_locations, extra_queries), return_exceptions=True)
        for provider, result in zip(("db", "transitous"), extra):
            if not isinstance(result, BaseException):
                candidates.extend(result)
                statuses[provider] = "ok"

    groups: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in sorted(candidates, key=lambda entry: (-_score(normalized, entry["name"], entry["provider"], entry), entry["name"])):
        if not _query_matches_station(normalized, item):
            continue
        if not has_airport_context(normalized) and has_airport_context(item["name"]):
            continue
        if (
            item.get("is_station") is True
            and exact_location_key(item["name"]) == exact_location_key(base_query)
            and any(token in exact_location_key(normalized).split() for token in {"hbf", "hauptbahnhof"})
        ):
            item = {**item, "name": normalized}
        key = (item["provider"], item["provider_id"])
        if key in seen: continue
        seen.add(key)
        group = next((entry for entry in groups if _same_group(entry, item, transport_mode)), None)
        if group:
            provider = item["provider"]
            group["provider_ids"].setdefault(provider, item["provider_id"])
            aliases = group.setdefault("provider_alias_ids", {}).setdefault(provider, [])
            if item["provider_id"] not in aliases:
                aliases.append(item["provider_id"])
            if item.get("parent_station"):
                group.setdefault("provider_parent_stations", {}).setdefault(provider, item["parent_station"])
            group["modes"] = sorted(set(group.get("modes") or []) | set(item.get("modes") or []))
            for field in ("region", "country", "latitude", "longitude"):
                if group.get(field) is None and item.get(field) is not None: group[field] = item[field]
            continue
        groups.append({
            **item,
            "provider_ids": {item["provider"]: item["provider_id"]},
            "provider_alias_ids": {item["provider"]: [item["provider_id"]]},
            "provider_parent_stations": {item["provider"]: item["parent_station"]} if item.get("parent_station") else {},
        })

    if transport_mode != "all":
        groups = [item for item in groups if _station_mode_match(item, transport_mode) is not False]
        groups.sort(key=lambda item: (_station_mode_match(item, transport_mode) is not True,
                                      -_score(normalized, item["name"], item["provider"], item), item["name"]))
    ranked: list[dict[str, Any]] = []
    for item in groups[:min(max(limit, 1), 20)]:
        detail = ", ".join(str(part) for part in (item.get("region"), item.get("country")) if part)
        ranked.append({**item, "label": f"{item['name']} — {detail}" if detail else item["name"], "id": f"{item['provider']}:{item['provider_id']}"})
    scores = [_score(normalized, item["name"], item["provider"], item) for item in ranked]
    for item, score in zip(ranked, scores):
        item["type"] = "airport" if "airport" in location_key(item["name"]).split() or "flughafen" in location_key(item["name"]).split() else "station"
        item["confidence"] = round(min(0.99, max(0.01, score / 1050)), 2)
    normalized_key = exact_location_key(normalized)
    explicit_station = any(token in normalized_key.split() for token in {"hbf", "hauptbahnhof", "bahnhof", "station", "zob", "terminal", "airport", "flughafen"})
    exact_confirmed_station = bool(
        len(ranked) == 1
        and exact_location_key(ranked[0]["name"]) == normalized_key
        and (ranked[0].get("is_station") is True or str(ranked[0].get("location_type") or "") == "1")
    )
    explicit_station = explicit_station or exact_confirmed_station
    if transport_mode != "all" and ranked:
        exact_matches = [item for item in ranked if sorted(location_key(item["name"]).split()) == sorted(location_key(normalized).split())]
        explicit_station = explicit_station or (len(exact_matches) == 1 and exact_matches[0] is ranked[0])
    auto = ranked[0] if ranked and explicit_station and location_key(_base_station_query(normalized)) else None
    return {
        "status": ("failed" if not ranked and statuses and all(value == "failed" for value in statuses.values())
                   else "partial" if any(value == "failed" for value in statuses.values()) else "ok"),
        "query": normalized, "stations": ranked, "provider_status": statuses,
        "requires_selection": bool(ranked and not auto),
        "auto_selection": auto,
        "transport_mode": transport_mode,
    }


async def search_stations(query: str, limit: int = 12, transport_mode: GroundTransportMode = "all") -> dict[str, Any]:
    normalized = " ".join(str(query or "").split())
    key = {"generation": APP_VERSION, "ranking": _RANKING_GENERATION, "exact_query": exact_location_key(normalized), "alias_query": location_key(normalized), "limit": limit, "transport_mode": transport_mode}
    return await cached_call("locations.resolve", key, _CACHE_TTL, lambda: _search_uncached(normalized, limit, transport_mode))
