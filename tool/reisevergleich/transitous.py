from __future__ import annotations

from .ground_transport import route_matches_transport

import asyncio
import re
from datetime import datetime
from typing import Any

import httpx

from .airports import airport_identity_matches, provider_location_query
from .config import TRANSITOUS_TIMEOUT, TRANSITOUS_URL, TRANSITOUS_USER_AGENT, TZ
from .db import build_manual_db_links
from .location_resolver import location_candidates, location_match_is_safe
from .models import ReiseRequest
from .utils import as_int, normalize_text


def _terminal_numbers(value: str) -> set[int]:
    numbers: set[int] = set()
    for match in re.finditer(r"(?i)\b(?:terminal|t)\s*([0-9]+)(?:\s*[-–/]\s*([0-9]+))?", str(value or "")):
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if 0 < start <= 20 and 0 < end <= 20:
            lo, hi = sorted((start, end))
            numbers.update(range(lo, hi + 1))
    return numbers


def choose_match(matches: Any, query: str) -> dict[str, Any] | None:
    if not isinstance(matches, list):
        return None
    wanted = normalize_text(query)
    requested_terminals = _terminal_numbers(query)
    wanted_tokens = set(wanted.split())
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in matches:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        raw_name = str(item.get("name") or "")
        if not airport_identity_matches(query, raw_name):
            continue
        candidate_terminals = _terminal_numbers(raw_name)
        if requested_terminals and candidate_terminals and not (requested_terminals & candidate_terminals):
            continue
        name = normalize_text(raw_name)
        # Provider scores are useful only after hard identity checks and are
        # deliberately capped so they cannot overpower an exact name match.
        score = min(float(item.get("score") or 0), 100.0)
        score += 250 * len(wanted_tokens.intersection(name.split()))
        if name == wanted:
            score += 1000
        elif name.startswith(wanted) or wanted.startswith(name):
            score += 500
        elif wanted in name or name in wanted:
            score += 200
        if requested_terminals and candidate_terminals and requested_terminals & candidate_terminals:
            score += 150
        if item.get("type") == "STOP":
            score += 50
        scored.append((score, item))
    return max(scored, key=lambda entry: entry[0])[1] if scored else None


async def _resolve_stop(client: httpx.AsyncClient, value: str) -> tuple[dict[str, Any] | None, str, list[str]]:
    """Resolve the exact input first and use translated city spelling as fallback."""
    last_names: list[str] = []
    candidates = location_candidates(value)
    for lookup in candidates:
        response = await client.get(
            f"{TRANSITOUS_URL}/api/v1/geocode",
            params={"text": lookup, "type": "STOP", "language": "de", "numResults": 12},
        )
        response.raise_for_status()
        matches = response.json()
        last_names = [str(x.get("name") or "") for x in matches[:8] if isinstance(x, dict)] if isinstance(matches, list) else []
        selected = choose_match(matches, lookup)
        if selected and location_match_is_safe(value, str(selected.get("name") or "")):
            return selected, lookup, last_names
    return None, candidates[-1], last_names


async def search(request: ReiseRequest, *, deutschlandticket_only: bool = False) -> dict[str, Any]:
    headers = {"User-Agent": TRANSITOUS_USER_AGENT, "Accept": "application/json"}
    timeout = httpx.Timeout(TRANSITOUS_TIMEOUT, connect=min(8, TRANSITOUS_TIMEOUT))
    diagnostic: dict[str, Any] = {
        "source": "transitous",
        "ok": False,
        "origin": request.origin,
        "destination": request.destination,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            origin_query = provider_location_query(request.origin)
            destination_query = provider_location_query(request.destination)
            selected_origin = request.origin_station if request.origin_station and request.origin_station.id_for("transitous") else None
            selected_destination = request.destination_station if request.destination_station and request.destination_station.id_for("transitous") else None
            async def selected_or_resolve(selection, query):
                if selection:
                    return ({"id": selection.id_for("transitous"), "name": selection.name}, selection.name, [selection.name])
                return await _resolve_stop(client, query)
            origin_result, destination_result = await asyncio.gather(
                selected_or_resolve(selected_origin, origin_query),
                selected_or_resolve(selected_destination, destination_query),
            )
            origin, origin_lookup, origin_candidates = origin_result
            destination, destination_lookup, destination_candidates = destination_result
            diagnostic["origin_lookup_query"] = origin_lookup
            diagnostic["destination_lookup_query"] = destination_lookup
            if not origin or not destination:
                diagnostic["error"] = "Start oder Ziel wurde bei Transitous nicht sicher gefunden."
                diagnostic["origin_candidates"] = origin_candidates
                diagnostic["destination_candidates"] = destination_candidates
                return {"routes": [], "diagnostic": diagnostic}

            local_dt = datetime.fromisoformat(
                f"{request.travel_date}T{request.departure_after}:00"
            ).replace(tzinfo=TZ)
            params: dict[str, Any] = {
                "fromPlace": origin["id"],
                "toPlace": destination["id"],
                "time": local_dt.isoformat(),
                "transitModes": (
                    {"suburban": "SUBURBAN", "bus": "BUS,COACH", "local_train": "REGIONAL_RAIL,SUBURBAN"}[request.transport_mode] if request.transport_mode != "all" else
                    "REGIONAL_RAIL,SUBURBAN,SUBWAY,TRAM,BUS,FERRY,FUNICULAR"
                    if deutschlandticket_only else "TRANSIT"
                ),
                "numItineraries": request.max_results,
                "maxItineraries": request.max_results,
                "timetableView": "true",
                "detailedLegs": "false",
                "detailedTransfers": "false",
                "joinInterlinedLegs": "false",
                "language": "de",
                "timeout": 20,
            }
            if request.max_transfers is not None:
                params["maxTransfers"] = request.max_transfers

            plan_response = await client.get(f"{TRANSITOUS_URL}/api/v6/plan", params=params)
            plan_response.raise_for_status()
            data = plan_response.json()
            routes = normalize_routes(
                data.get("itineraries", []),
                request,
                origin,
                destination,
                deutschlandticket_only=deutschlandticket_only,
            )[: request.max_results]
            diagnostic.update({
                # A valid empty itinerary list is a successful provider check,
                # not a transport failure.
                "ok": True,
                "origin_match": {"id": origin.get("id"), "name": origin.get("name")},
                "destination_match": {"id": destination.get("id"), "name": destination.get("name")},
                "routes": len(routes),
                "attribution": "https://transitous.org/sources/",
            })
            return {"routes": routes, "diagnostic": diagnostic}
    except Exception as exc:
        diagnostic["error"] = f"{type(exc).__name__}: {exc}"
        return {"routes": [], "diagnostic": diagnostic}


def normalize_routes(
    itineraries: Any,
    request: ReiseRequest,
    origin_match: dict[str, Any],
    destination_match: dict[str, Any],
    *,
    deutschlandticket_only: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(itineraries, list):
        return []
    output: list[dict[str, Any]] = []
    for index, itinerary in enumerate(itineraries):
        if not isinstance(itinerary, dict):
            continue
        raw_legs = itinerary.get("legs") if isinstance(itinerary.get("legs"), list) else []
        transit_legs = [
            leg for leg in raw_legs
            if isinstance(leg, dict) and str(leg.get("mode") or "").upper() not in {"WALK", "FOOT"}
        ]
        if not transit_legs:
            continue
        if not route_matches_transport({"legs": transit_legs}, request.transport_mode):
            continue
        if deutschlandticket_only:
            forbidden_modes = {"HIGHSPEED_RAIL", "LONG_DISTANCE", "NIGHT_RAIL", "AIRPLANE", "COACH"}
            forbidden_labels = re.compile(r"^(ICE|IC|EC|ECE|TGV|RJ|RJX|NJ|FLX)\b", re.I)
            invalid = any(
                str(leg.get("mode") or "").upper() in forbidden_modes
                or forbidden_labels.search(str(
                    leg.get("displayName") or leg.get("routeShortName") or leg.get("tripShortName") or ""
                ))
                for leg in transit_legs
            )
            if invalid:
                continue

        legs = []
        for leg in transit_legs:
            from_place = leg.get("from") if isinstance(leg.get("from"), dict) else {}
            to_place = leg.get("to") if isinstance(leg.get("to"), dict) else {}
            legs.append({
                "mode": leg.get("mode"),
                "line": leg.get("displayName") or leg.get("routeShortName") or leg.get("tripShortName"),
                "provider": leg.get("agencyName") or "Transitous",
                "operator": leg.get("agencyName") or "Transitous",
                "origin": {"id": from_place.get("stopId"), "name": from_place.get("name")},
                "destination": {"id": to_place.get("stopId"), "name": to_place.get("name")},
                "departure": leg.get("scheduledStartTime") or leg.get("startTime"),
                "arrival": leg.get("scheduledEndTime") or leg.get("endTime"),
                "cancelled": bool(leg.get("cancelled")),
            })
        departure = itinerary.get("startTime") or legs[0].get("departure")
        arrival = itinerary.get("endTime") or legs[-1].get("arrival")
        links = build_manual_db_links(
            request.origin,
            request.destination,
            request.travel_date,
            request.departure_after,
            departure_iso=str(departure) if departure else None,
        )
        output.append({
            "id": f"transitous-{index}",
            "provider": "Transitous",
            "provider_code": "transitous",
            "db_source": "transitous",
            "type": "public_transport",
            "origin": origin_match.get("name") or request.origin,
            "destination": destination_match.get("name") or request.destination,
            "departure": departure,
            "arrival": arrival,
            "duration_minutes": max(0, round(as_int(itinerary.get("duration")) / 60)),
            "transfers": as_int(itinerary.get("transfers")),
            "price": None,
            "currency": None,
            "price_note": "Transitous liefert keinen belastbaren DB-Ticketpreis.",
            "deutschlandticket_coverage": (
                "nur_nahverkehr_gefiltert_nicht_tariflich_garantiert" if deutschlandticket_only else None
            ),
            "booking_url": links["normal"]["url"],
            "db_search_links": links,
            "legs": legs,
            "timezone": origin_match.get("tz") or destination_match.get("tz"),
            "attribution": "https://transitous.org/sources/",
        })
    return output
