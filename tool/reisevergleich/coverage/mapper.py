from __future__ import annotations

import asyncio
import math
from typing import Any

import httpx

from ..config import TRANSITOUS_URL, TRANSITOUS_USER_AGENT

MAX_ROUTE_POINTS = 180
SAMPLE_DISTANCE_KM = 4.0


def _coordinate(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    lat = value.get("latitude", value.get("lat"))
    lon = value.get("longitude", value.get("lon"))
    try:
        point = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if -90 <= point[0] <= 90 and -180 <= point[1] <= 180:
        return point
    return None


def _name(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return str(value.get("name") or value.get("station") or value.get("city") or "").strip() or None
    return None


def route_waypoints(route: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract ordered geometry/stops without depending on a provider schema."""
    geometry = route.get("geometry") or route.get("shape") or route.get("polyline")
    if isinstance(geometry, dict):
        geometry = geometry.get("coordinates")
    if isinstance(geometry, list) and geometry:
        points: list[dict[str, Any]] = []
        for item in geometry:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    # GeoJSON order is longitude, latitude.
                    points.append({"latitude": float(item[1]), "longitude": float(item[0])})
                except (TypeError, ValueError):
                    continue
            elif (coordinate := _coordinate(item)) is not None:
                points.append({"latitude": coordinate[0], "longitude": coordinate[1], "name": _name(item)})
        if len(points) >= 2:
            return points

    legs = route.get("legs") if isinstance(route.get("legs"), list) else []
    if isinstance(route.get("segments"), list) and route["segments"]:
        legs = [
            child
            for segment in route["segments"]
            if isinstance(segment, dict)
            for child in (segment.get("legs") or [segment])
            if isinstance(child, dict)
        ]
    values: list[Any] = []
    if route.get("coverage_origin"):
        values.append(route["coverage_origin"])
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        stopovers = leg.get("stopovers") if isinstance(leg.get("stopovers"), list) else []
        if stopovers:
            values.extend(stopovers)
        else:
            values.extend((
                leg.get("origin") or leg.get("from") or leg.get("departure"),
                leg.get("destination") or leg.get("to") or leg.get("arrival"),
            ))
    if route.get("coverage_destination"):
        values.append(route["coverage_destination"])
    if not values:
        values = [route.get("origin"), route.get("destination")]

    output: list[dict[str, Any]] = []
    for value in values:
        coordinate = _coordinate(value)
        item = {
            "name": _name(value),
            "latitude": coordinate[0] if coordinate else None,
            "longitude": coordinate[1] if coordinate else None,
        }
        signature = (item["name"], item["latitude"], item["longitude"])
        if not output or signature != (output[-1].get("name"), output[-1].get("latitude"), output[-1].get("longitude")):
            output.append(item)
    return output


async def _geocode(client: httpx.AsyncClient, name: str) -> tuple[float, float] | None:
    response = await client.get(
        f"{TRANSITOUS_URL}/api/v1/geocode",
        params={"text": name, "type": "STOP", "language": "de", "numResults": 1},
    )
    response.raise_for_status()
    matches = response.json()
    if not isinstance(matches, list) or not matches:
        return None
    return _coordinate(matches[0])


async def resolve_waypoints(route: dict[str, Any]) -> list[dict[str, Any]]:
    waypoints = route_waypoints(route)
    unresolved = {str(item.get("name")): item for item in waypoints if _coordinate(item) is None and item.get("name")}
    if unresolved:
        headers = {"User-Agent": TRANSITOUS_USER_AGENT, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), headers=headers, follow_redirects=True) as client:
            results = await asyncio.gather(*(_geocode(client, name) for name in unresolved), return_exceptions=True)
        for (name, _), result in zip(unresolved.items(), results, strict=True):
            if isinstance(result, tuple):
                for item in waypoints:
                    if item.get("name") == name:
                        item["latitude"], item["longitude"] = result
    return [item for item in waypoints if _coordinate(item) is not None]


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(value)))


def sample_route(waypoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(waypoints) < 2:
        return []
    samples: list[dict[str, Any]] = []
    travelled = 0.0
    for index, (left, right) in enumerate(zip(waypoints, waypoints[1:])):
        a, b = _coordinate(left), _coordinate(right)
        if a is None or b is None:
            continue
        distance = _distance_km(a, b)
        steps = max(1, math.ceil(distance / SAMPLE_DISTANCE_KM))
        for step in range(steps):
            fraction = step / steps
            samples.append({
                "latitude": a[0] + (b[0] - a[0]) * fraction,
                "longitude": a[1] + (b[1] - a[1]) * fraction,
                "distance_km": travelled + distance * fraction,
                "from": left.get("name"),
                "to": right.get("name"),
                "interpolated": True,
            })
        travelled += distance
    final = _coordinate(waypoints[-1])
    if final:
        samples.append({"latitude": final[0], "longitude": final[1], "distance_km": travelled, "name": waypoints[-1].get("name")})
    if len(samples) > MAX_ROUTE_POINTS:
        step = (len(samples) - 1) / (MAX_ROUTE_POINTS - 1)
        samples = [samples[round(index * step)] for index in range(MAX_ROUTE_POINTS)]
    return samples
