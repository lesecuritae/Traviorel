from __future__ import annotations

import hashlib
import json
from typing import Any, Awaitable, Callable

from ..cache import cached_call

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def route_hash(points: list[dict[str, Any]]) -> str:
    stable = [[round(float(item["latitude"]), 4), round(float(item["longitude"]), 4)] for item in points]
    return hashlib.sha256(json.dumps(stable, separators=(",", ":")).encode()).hexdigest()[:20]


def waypoint_hash(waypoints: list[dict[str, Any]]) -> str:
    stable = [
        [
            str(item.get("name") or "").casefold().strip(),
            round(float(item["latitude"]), 4) if item.get("latitude") is not None else None,
            round(float(item["longitude"]), 4) if item.get("longitude") is not None else None,
        ]
        for item in waypoints
    ]
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:20]


async def get_or_analyze(route_id: str, producer: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    # Namespace is an explicit result-schema/algorithm generation. Bumping it
    # invalidates derived values without touching any user's persisted data.
    return await cached_call("coverage-v1.4.0", {"route_id": route_id}, CACHE_TTL_SECONDS, producer)
