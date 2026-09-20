from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Query

from .airports import AIRPORT_CITY_NAMES, AIRPORT_STATIONS
from .cache import health as cache_health
from .config import APP_VERSION, DB_API_URL, TRANSITOUS_URL, TRANSITOUS_USER_AGENT, today_iso
from .models import CoverageRequest, PriceCalendarRequest, ReiseRequest, TripRequest, WarningRouteRequest
from .coverage import analyze_route
from .warnings import warnings_for_routes
from .service import price_calendar, search
from .progress import begin as begin_progress, end as end_progress, get as get_progress, valid_search_id
from .gtfs_flix import discover_stops as discover_gtfs_flix_stops
from .station_catalog import search_stations
from .trvl import capability_report

router = APIRouter()


@router.post(
    "/api/search",
    operation_id="search_trip",
    summary="Reise deterministisch suchen und vergleichen",
)
async def search_trip(request: TripRequest, x_search_id: str | None = Header(default=None)) -> dict[str, Any]:
    search_id = valid_search_id(x_search_id)
    if not search_id:
        return await search(request)
    token = begin_progress(search_id)
    try:
        result = await search(request)
        end_progress(token, status="completed")
        return result
    except BaseException as exc:
        end_progress(token, status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed")
        raise


@router.get("/api/search-status/{search_id}")
async def search_status(search_id: str) -> dict[str, Any]:
    normalized = valid_search_id(search_id)
    result = get_progress(normalized) if normalized else None
    if not result: raise HTTPException(status_code=404, detail="Unbekannte oder abgelaufene Suche")
    return result


@router.post("/api/coverage", summary="Mobiles Breitband entlang einer Fahrt analysieren")
async def coverage(request: CoverageRequest) -> dict[str, Any]:
    # Separate endpoint by design: provider latency or failure can never delay
    # the journey response that triggered this optional enrichment.
    return await analyze_route(request.route)


@router.post("/api/warnings", summary="Aktuelle NINA-/BBK-Warnungen entlang einer Reise")
async def travel_warnings(request: WarningRouteRequest) -> dict[str, Any]:
    return await warnings_for_routes(request.routes)


@router.post("/api/price-calendar", summary="Aktuelle Bodenreise-Preise über mehrere Tage vergleichen")
async def flexible_price_calendar(request: PriceCalendarRequest) -> dict[str, Any]:
    return await price_calendar(request)


@router.get("/api/flix-stops")
async def flix_stops(origin: str = Query(min_length=1), destination: str = Query(min_length=1)) -> dict[str, Any]:
    return await discover_gtfs_flix_stops(origin, destination)


@router.get("/api/stations")
async def stations(q: str = Query(min_length=2, max_length=120), limit: int = Query(default=12, ge=1, le=20)) -> dict[str, Any]:
    return await search_stations(q, limit)


@router.get("/api/config")
async def config() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "current_date": today_iso(),
        "defaults": {
            "duration_value": 7,
            "duration_unit": "nights",
            "hotel_property_type": "hotel",
            "hotel_min_stars": 3,
            "airport_buffer_minutes": 120,
            "max_results": 24,
        },
        "airport_cities": AIRPORT_CITY_NAMES,
        "airport_stations": AIRPORT_STATIONS,
    }


@router.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "current_date": today_iso(),
        "architecture": "deterministic",
        "providers": ["DB/db-vendo", "Transitous", "Flix", "trvl", "NINA/BBK", "BNetzA Mobilfunk-Monitoring", "OpenCellID"],
    }


@router.get("/api/diagnostics")
async def diagnostics() -> dict[str, Any]:
    transitous_probe_url = f"{TRANSITOUS_URL}/api/v1/geocode"
    headers = {"User-Agent": TRANSITOUS_USER_AGENT, "Accept": "application/json"}

    async def probe_db() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(f"{DB_API_URL}/health")
                return {"ok": response.is_success, "http_status": response.status_code}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def probe_transitous() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as client:
                response = await client.get(
                    transitous_probe_url,
                    params={"text": "Frankfurt(Main)Hbf", "type": "STOP", "language": "de", "numResults": 1},
                )
                return {"ok": response.is_success, "http_status": response.status_code}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    db, transitous, trvl, cache = await asyncio.gather(
        probe_db(), probe_transitous(), capability_report(), cache_health()
    )
    required = bool(
        db.get("ok")
        and transitous.get("ok")
        and cache.get("status") == "ok"
        and (trvl.get("enabled") is False or all(item.get("ok") for item in trvl.values()))
    )
    return {
        "status": "ok" if required else "degraded",
        "version": APP_VERSION,
        "db_api": db,
        "transitous": transitous,
        "trvl": trvl,
        "cache": cache,
    }
