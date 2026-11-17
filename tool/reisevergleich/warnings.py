from __future__ import annotations

import asyncio
import html
import math
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .cache import cached_call
from .airports import provider_location_query, resolve_feeder_airport_station
from .config import (
    APP_VERSION, NINA_API_URL, NINA_CACHE_TTL, NINA_TIMEOUT,
    TRANSITOUS_TIMEOUT, TRANSITOUS_URL, TRANSITOUS_USER_AGENT,
)
from .coverage.mapper import route_waypoints
from .models import TravelWarning
from .transitous import choose_match

SOURCES = ("mowas", "katwarn", "biwapp", "dwd", "lhp", "police")
MAX_ROUTE_POINTS = 18
MAX_WARNING_CONCURRENCY = 4
MAX_RELEVANT_WARNINGS = 12
MAX_AREA_BBOX_KM2 = 250_000


async def _get_json(path: str) -> Any:
    headers = {"User-Agent": f"Traviorel/{APP_VERSION}", "Accept": "application/json"}
    timeout = httpx.Timeout(NINA_TIMEOUT, connect=min(5.0, NINA_TIMEOUT))
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        response = await client.get(f"{NINA_API_URL}/{path.lstrip('/')}")
        response.raise_for_status()
        return response.json()


async def _cached_json(namespace: str, path: str) -> Any:
    return await cached_call(namespace, {"path": path}, NINA_CACHE_TTL, lambda: _get_json(path))


async def _airport_points(route: dict[str, Any]) -> list[dict[str, Any]]:
    names: list[str] = []
    for endpoint in ("origin", "destination"):
        value = route.get(endpoint)
        name = str(value.get("name") or "") if isinstance(value, dict) else ""
        match = re.fullmatch(r"Flughafen ([A-Z]{3})", name)
        if match:
            name, _ = resolve_feeder_airport_station(match.group(1), None)
            name = provider_location_query(name)
        if name:
            names.append(name)

    async def producer() -> list[dict[str, Any]]:
        headers = {"User-Agent": TRANSITOUS_USER_AGENT, "Accept": "application/json"}
        timeout = httpx.Timeout(TRANSITOUS_TIMEOUT, connect=min(8, TRANSITOUS_TIMEOUT))
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            async def resolve(name: str) -> dict[str, Any] | None:
                response = await client.get(
                    f"{TRANSITOUS_URL}/api/v1/geocode",
                    params={"text": name, "type": "STOP", "language": "de", "numResults": 12},
                )
                response.raise_for_status()
                return choose_match(response.json(), name)

            matches = await asyncio.gather(*(resolve(name) for name in names), return_exceptions=True)
        points: list[dict[str, Any]] = []
        for name, result in zip(names, matches, strict=True):
            if isinstance(result, Exception):
                continue
            selected = result
            if not isinstance(selected, dict):
                continue
            latitude = selected.get("latitude", selected.get("lat"))
            longitude = selected.get("longitude", selected.get("lon"))
            point = {
                "name": str(selected.get("name") or name),
                "latitude": latitude,
                "longitude": longitude,
            }
            if _coordinate(point):
                points.append(point)
        return points

    return await cached_call("nina-airport-points-v1", {"names": names}, 86_400, producer)


def _coordinate(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        latitude = float(value.get("latitude"))
        longitude = float(value.get("longitude"))
    except (TypeError, ValueError):
        return None
    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
        return latitude, longitude
    return None


async def _route_points(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()
    for route in routes:
        extracted = route_waypoints(route)
        # trvl liefert für Flüge IATA-Codes, aber keine Flughafenkoordinaten.
        # Nur ausdrücklich markierte Flughafenpunkte verwenden den vorhandenen
        # Transitous-Resolver als Fallback. Bodenrouten bleiben vollständig bei
        # den bereits gelieferten Providerkoordinaten.
        if route.get("warning_geocode") and not any(_coordinate(item) for item in extracted):
            try:
                extracted = await _airport_points(route)
            except (httpx.HTTPError, ValueError, TypeError):
                extracted = []
        waypoints = [item for item in extracted if _coordinate(item)]
        if len(waypoints) > MAX_ROUTE_POINTS:
            step = (len(waypoints) - 1) / (MAX_ROUTE_POINTS - 1)
            waypoints = [waypoints[round(index * step)] for index in range(MAX_ROUTE_POINTS)]
        for item in waypoints:
            coordinate = _coordinate(item)
            if coordinate is None:
                continue
            key = round(coordinate[0], 4), round(coordinate[1], 4)
            if key in seen:
                continue
            seen.add(key)
            points.append({
                "name": str(item.get("name") or "").strip() or None,
                "latitude": coordinate[0],
                "longitude": coordinate[1],
            })
    if len(points) <= MAX_ROUTE_POINTS:
        return points
    step = (len(points) - 1) / (MAX_ROUTE_POINTS - 1)
    return [points[round(index * step)] for index in range(MAX_ROUTE_POINTS)]


def _polygons(geometry: Any) -> list[list[list[list[float]]]]:
    if not isinstance(geometry, dict):
        return []
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Polygon" and isinstance(coordinates, list):
        return [[ring for ring in coordinates if isinstance(ring, list)]]
    if geometry.get("type") == "MultiPolygon" and isinstance(coordinates, list):
        return [
            [ring for ring in polygon if isinstance(ring, list)]
            for polygon in coordinates
            if isinstance(polygon, list)
        ]
    return []


def _bbox_area_km2(rings: list[list[list[float]]]) -> float:
    coordinates = [point for ring in rings for point in ring if isinstance(point, list) and len(point) >= 2]
    if not coordinates:
        return math.inf
    longitudes = [float(point[0]) for point in coordinates]
    latitudes = [float(point[1]) for point in coordinates]
    mean_latitude = math.radians(sum(latitudes) / len(latitudes))
    width = (max(longitudes) - min(longitudes)) * 111.32 * max(0.1, math.cos(mean_latitude))
    height = (max(latitudes) - min(latitudes)) * 111.32
    return abs(width * height)


def _point_in_ring(latitude: float, longitude: float, ring: list[list[float]]) -> bool:
    inside = False
    previous = ring[-1] if ring else None
    for current in ring:
        if not previous or len(current) < 2 or len(previous) < 2:
            previous = current
            continue
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if (y1 > latitude) != (y2 > latitude):
            crossing = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < crossing:
                inside = not inside
        previous = current
    return inside


def _affected_points(geojson: Any, points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    features = geojson.get("features") if isinstance(geojson, dict) else []
    result: list[dict[str, Any]] = []
    for feature in features or []:
        polygons = _polygons(feature.get("geometry") if isinstance(feature, dict) else None)
        feature_rings = [ring for polygon in polygons for ring in polygon]
        if not feature_rings or _bbox_area_km2(feature_rings) > MAX_AREA_BBOX_KM2:
            continue
        for rings in polygons:
            if not rings:
                continue
            outer, holes = rings[0], rings[1:]
            for point in points:
                coordinate = _coordinate(point)
                if not coordinate:
                    continue
                in_area = _point_in_ring(coordinate[0], coordinate[1], outer)
                in_hole = any(_point_in_ring(coordinate[0], coordinate[1], hole) for hole in holes)
                if in_area and not in_hole and point not in result:
                    result.append(point)
    return result


def _german_info(detail: dict[str, Any]) -> dict[str, Any]:
    infos = [item for item in detail.get("info") or [] if isinstance(item, dict)]
    return next((item for item in infos if str(item.get("language") or "").casefold().startswith("de")), infos[0] if infos else {})


def _plain_text(value: Any, limit: int = 320) -> str | None:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    text = " ".join(text.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _warning(entry: dict[str, Any], detail: dict[str, Any], affected: list[dict[str, Any]]) -> dict[str, Any]:
    info = _german_info(detail)
    areas = [str(item.get("areaDesc") or "").strip() for item in info.get("area") or [] if isinstance(item, dict)]
    warning = TravelWarning(
        id=str(entry.get("id") or detail.get("identifier")),
        title=str(info.get("headline") or ((entry.get("i18nTitle") or {}).get("de")) or info.get("event") or "Amtliche Warnung"),
        description=_plain_text(info.get("description")),
        location=", ".join(dict.fromkeys(area for area in areas if area)) or None,
        severity=str(info.get("severity") or entry.get("severity") or "").strip() or None,
        issuer=str(info.get("senderName") or detail.get("sender") or "").strip() or None,
        starts_at=info.get("onset") or info.get("effective") or entry.get("startDate"),
        expires_at=info.get("expires") or entry.get("expiresDate"),
        affected_stops=list(dict.fromkeys(str(point.get("name") or "").strip() for point in affected if point.get("name"))),
    )
    return warning.model_dump(exclude_none=True)


def _is_active(entry: dict[str, Any]) -> bool:
    if str(entry.get("type") or "").casefold() == "cancel":
        return False
    expires = entry.get("expiresDate")
    if not expires:
        return True
    try:
        return datetime.fromisoformat(str(expires).replace("Z", "+00:00")) > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


async def warnings_for_routes(routes: list[dict[str, Any]]) -> dict[str, Any]:
    points = await _route_points(routes)
    if not points:
        return {"status": "empty", "warnings": [], "source": "NINA/BBK"}
    feeds = await asyncio.gather(
        *(_cached_json("nina-map-v1", f"{source}/mapData.json") for source in SOURCES),
        return_exceptions=True,
    )
    available = [feed for feed in feeds if isinstance(feed, list)]
    if not available:
        return {"status": "unavailable", "warnings": [], "source": "NINA/BBK"}
    entries: dict[str, dict[str, Any]] = {}
    for feed in available:
        for entry in feed:
            if isinstance(entry, dict) and entry.get("id") and _is_active(entry):
                entries.setdefault(str(entry["id"]), entry)

    semaphore = asyncio.Semaphore(MAX_WARNING_CONCURRENCY)

    async def match(entry: dict[str, Any]) -> dict[str, Any] | None:
        identifier = str(entry["id"])
        async with semaphore:
            try:
                geojson = await _cached_json("nina-geo-v1", f"warnings/{identifier}.geojson")
                affected = _affected_points(geojson, points)
                if not affected:
                    return None
                detail = await _cached_json("nina-detail-v1", f"warnings/{identifier}.json")
                return _warning(entry, detail, affected)
            except (httpx.HTTPError, ValueError, TypeError):
                return None

    matched = [item for item in await asyncio.gather(*(match(entry) for entry in entries.values())) if item]
    severity_order = {"extreme": 0, "severe": 1, "moderate": 2, "minor": 3}
    matched.sort(key=lambda item: (severity_order.get(str(item.get("severity") or "").casefold(), 9), item.get("title") or ""))
    warnings = matched[:MAX_RELEVANT_WARNINGS]
    return {"status": "ok" if warnings else "empty", "warnings": warnings, "source": "NINA/BBK"}
