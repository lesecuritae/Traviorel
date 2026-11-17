from __future__ import annotations

import asyncio
from typing import Any

from .cache import get_or_analyze, route_hash, waypoint_hash
from .mapper import resolve_waypoints, route_waypoints, sample_route
from .provider import (
    OPERATORS_CONSIDERED,
    PROVIDERS,
    SOURCE_ATTRIBUTION,
    SOURCE_LICENSE,
    SOURCE_LICENSE_URL,
    SOURCE_NAME,
    SOURCE_REVISION,
    SOURCE_URL,
    sample,
)
from .opencellid import sample_operators


def _gaps(points: list[dict[str, Any]], values: list[bool | None]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    start: int | None = None
    for index, covered in enumerate([*values, True]):
        if covered is False and start is None:
            start = index
        elif covered is not False and start is not None:
            end = index - 1
            distance = max(0.0, float(points[end]["distance_km"]) - float(points[start]["distance_km"]))
            if distance >= 3:
                gaps.append({
                    "from_km": round(float(points[start]["distance_km"]), 1),
                    "to_km": round(float(points[end]["distance_km"]), 1),
                    "length_km": round(distance, 1),
                    "between": [points[start].get("from"), points[end].get("to")],
                })
            start = None
    return sorted(gaps, key=lambda item: item["length_km"], reverse=True)[:5]


async def analyze_route(route: dict[str, Any]) -> dict[str, Any]:
    try:
        request_identifier = waypoint_hash(route_waypoints(route))

        async def producer() -> dict[str, Any]:
            waypoints = await asyncio.wait_for(resolve_waypoints(route), timeout=8)
            points = sample_route(waypoints)
            if len(points) < 2:
                return {"status": "unavailable", "message": "Mobilfunkdaten für diese Strecke nicht verfügbar", "reason": "route_geometry_missing"}
            identifier = route_hash(points)
            coverage = await asyncio.wait_for(sample(points), timeout=12)
            operator_sample = await asyncio.wait_for(sample_operators(points), timeout=25)
            operator_networks = operator_sample.get("networks") or []
            operator_debug = operator_sample.get("debug") or {}
            networks: list[dict[str, Any]] = []
            evaluated_union = sum(
                any(coverage[key][index] is not None for key in PROVIDERS)
                for index in range(len(points))
            )
            for key, (label, _) in PROVIDERS.items():
                values = coverage[key]
                known = [value for value in values if value is not None]
                networks.append({
                    "id": key,
                    "name": label,
                    "coverage_percent": round(100 * sum(value is True for value in known) / len(known)) if known else None,
                    "evaluated_points": len(known),
                    "weak_sections": _gaps(points, values),
                })
            if not any(item["evaluated_points"] for item in networks):
                return {"status": "unavailable", "message": "Mobilfunkdaten für diese Strecke nicht verfügbar", "reason": "outside_germany_or_source_unavailable"}
            return {
                "status": "ok",
                "route_id": identifier,
                "route_distance_km": round(float(points[-1]["distance_km"]), 1),
                "sample_count": len(points),
                "evaluated_sample_count": evaluated_union,
                "outside_source_area_count": len(points) - evaluated_union,
                "scope": "germany_only" if evaluated_union < len(points) else "complete_sampled_route",
                "networks": networks,
                "operator_networks": operator_networks,
                "operator_specific": {
                    "status": "available" if operator_networks else "not_available",
                    "operators_considered": OPERATORS_CONSIDERED,
                    "source": "OpenCellID",
                    "evaluated_points": len(points),
                    "cell_count": sum(item.get("cell_count", 0) for item in operator_networks),
                    "data_quality": ("good" if operator_networks and all(item.get("data_quality") == "good" for item in operator_networks) else "limited" if operator_networks else "insufficient"),
                    "message": "Betreiberdaten nicht verfügbar" if not operator_networks else None,
                    "debug": operator_debug,
                },
                "tunnels": {"status": "not_available", "message": "Tunnel werden von der Abdeckungsquelle nicht separat ausgewiesen."},
                "method": "Konservative Mindestanzahl breitbandiger Netze aus 4G-/5G-Betreiberzahlen; Outdoor-Prognose, Verlauf zwischen Halten interpoliert.",
                "source": {
                    "name": SOURCE_NAME,
                    "url": SOURCE_URL,
                    "revision": SOURCE_REVISION,
                    "license": SOURCE_LICENSE,
                    "license_url": SOURCE_LICENSE_URL,
                    "attribution": SOURCE_ATTRIBUTION,
                },
            }

        return await get_or_analyze(request_identifier, producer)
    except Exception as exc:
        return {
            "status": "unavailable",
            "message": "Mobilfunkanalyse fehlgeschlagen",
            "reason": type(exc).__name__,
        }
