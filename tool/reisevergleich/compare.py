from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

from .config import HISTORY_ENRICH_TIMEOUT, SEARCH_DEPARTURE_TOLERANCE_MINUTES, today_iso
from .db import build_manual_db_links, compact_attempts, compact_route, rank_routes
from .ground_connections import connection_signature
from .ground_transport import GroundTransportMode, route_matches_transport
from .flix_connections import complete_flix_routes
from .models import DeutschlandticketRequest, ReiseRequest
from .provider_cache import db_search_with_retry, db_split_analysis, flix_search, transitous_search
from .ground_mixed import ground_mixed_options
from .history import enrich_routes_history
from .utils import annotate_departure_tolerance, as_float, as_int, departure_search_floor, parse_datetime, route_departure_in_window
from .progress import update as progress

_route_departure_in_window = route_departure_in_window


def _provider_state(provider: str, *, ok: bool, result_count: int = 0, skipped: bool = False) -> dict[str, Any]:
    """Expose one unambiguous, UI-safe outcome without leaking diagnostics."""
    if ok or skipped:
        outcome = "connection_found" if result_count else "no_connection"
        message = "Verbindung gefunden" if result_count else "Keine Verbindung verfügbar"
    else:
        outcome = "technical_error"
        message = "Flix konnte nicht geprüft werden" if provider == "Flix" else "Technischer Abruffehler"
    return {"provider": provider, "outcome": outcome, "message": message, "result_count": result_count}


def _route_transport_kinds(route: dict[str, Any]) -> set[str]:
    values = [route.get("type"), route.get("mode"), route.get("flix_kind"), route.get("product")]
    for collection in (route.get("legs"), route.get("segments")):
        for item in collection or []:
            if isinstance(item, dict):
                values.extend((item.get("type"), item.get("mode"), item.get("product"), item.get("line")))
    text = " ".join(str(value or "").casefold() for value in values)
    kinds: set[str] = set()
    if any(token in text for token in ("bus", "coach", "fernlinienbus")): kinds.add("bus")
    if any(token in text for token in ("train", "rail", "zug", "ice", "intercity", "regional", "tram", "s-bahn", "suburban")): kinds.add("train")
    return kinds


def _transport_selected(route: dict[str, Any], request: ReiseRequest) -> bool:
    if request.transport_mode != "all":
        return route_matches_transport(route, request.transport_mode)
    kinds = _route_transport_kinds(route)
    if not kinds:
        return True
    enabled = ({"train"} if request.include_train else set()) | ({"bus"} if request.include_bus else set())
    return bool(kinds & enabled)


def _attach_coverage_endpoints(routes: list[dict[str, Any]], request: ReiseRequest) -> list[dict[str, Any]]:
    def endpoint(selection: Any, fallback: str) -> dict[str, Any]:
        return {
            "name": fallback,
            "latitude": getattr(selection, "latitude", None),
            "longitude": getattr(selection, "longitude", None),
        }

    origin = endpoint(request.origin_station, request.origin)
    destination = endpoint(request.destination_station, request.destination)
    return [{**route, "coverage_origin": origin, "coverage_destination": destination} for route in routes]


async def _enrich_history_bounded(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep optional history I/O outside the critical search latency path."""
    try:
        return await asyncio.wait_for(
            enrich_routes_history(routes), timeout=HISTORY_ENRICH_TIMEOUT,
        )
    except (Exception, asyncio.TimeoutError):
        return routes

def select_visible_ground_options(
    db_routes: list[dict[str, Any]],
    flix_routes: list[dict[str, Any]],
    request: ReiseRequest,
) -> list[dict[str, Any]]:
    """Reserve pure Flix diversity, then prefer DB and use Flix as backfill."""
    ranked_db = rank_routes(db_routes, request.preference)
    ranked_flix = rank_routes(flix_routes, request.preference)
    if request.max_results == 1:
        return rank_routes(ranked_db + ranked_flix, request.preference)[:1]

    def chronological(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(routes, key=lambda route: str((route.get("departure") or {}).get("time") if isinstance(route.get("departure"), dict) else route.get("departure") or ""))

    train = [route for route in ranked_flix if route.get("flix_kind") == "train"]
    bus = [route for route in ranked_flix if route.get("flix_kind") == "bus"]
    selected: list[dict[str, Any]] = []
    reserved_groups = (
        train[:2] if request.include_flixtrain else [],
        bus[:2] if request.include_flixbus else [],
    )
    for index in range(2):
        for group in reserved_groups:
            if index < len(group) and len(selected) < request.max_results:
                selected.append(group[index])

    known = {connection_signature(route) for route in selected}
    if len(selected) >= request.max_results:
        return chronological(selected)
    for pool in (ranked_db, ranked_flix):
        for route in pool:
            signature = connection_signature(route)
            if signature in known:
                continue
            selected.append(route)
            known.add(signature)
            if len(selected) >= request.max_results:
                return chronological(selected)
    return chronological(selected)


def _compact_split(split: dict[str, Any]) -> dict[str, Any]:
    output = {
        "status": split.get("status"),
        "method": split.get("method"),
        "original_price": split.get("original_price"),
        "currency": split.get("currency"),
        "checked_split_points": split.get("checked_split_points"),
        "reason": split.get("reason"),
        "error": split.get("error"),
    }
    options = split.get("split_options")
    if isinstance(options, list) and options:
        output["split_options"] = options[:3]
    return {key: value for key, value in output.items() if value not in (None, "", [])}


async def compare_trip(request: ReiseRequest) -> dict[str, Any]:
    for step in ("db", "transitous", "gtfs", "flixbus", "flixtrain"):
        progress(step, "loading")
    provider_date, provider_time = departure_search_floor(request.travel_date, request.departure_after)
    provider_request = request.model_copy(update={"travel_date": provider_date, "departure_after": provider_time})
    db_origin_id = request.origin_station.id_for("db") if request.origin_station else None
    db_destination_id = request.destination_station.id_for("db") if request.destination_station else None
    selections_active = bool(request.origin_station and request.destination_station)
    async def unsupported_db():
        return ({"status": "empty", "journeys": []}, [{"source":"db-api", "ok":False, "skipped":True, "error":"Ausgewählte Station besitzt keine DB-ID."}])
    db_task = asyncio.create_task((db_search_with_retry(
        origin=request.origin,
        destination=request.destination,
        travel_date=provider_date,
        departure_after=provider_time,
        mode="all",
        transport_mode=request.transport_mode,
        max_transfers=request.max_transfers,
        results=request.max_results,
        origin_id=db_origin_id,
        destination_id=db_destination_id,
    ) if not selections_active or (db_origin_id and db_destination_id) else unsupported_db()))
    # Fallbacks parallel starten: Wenn DBnav/DB langsam ist, ist Transitous bereits
    # unterwegs. Flix läuft ebenfalls unabhängig und kann die Bahn nie blockieren.
    transitous_supported = not selections_active or (
        request.origin_station.id_for("transitous") and request.destination_station.id_for("transitous")
    )
    flix_supported = request.transport_mode not in {"suburban", "local_train"} and (not selections_active or (
        request.origin_station.id_for("flix") and request.destination_station.id_for("flix")
    ))
    async def unsupported_transitous():
        return {"routes": [], "diagnostic": {"ok":False, "skipped":True, "error":"Ausgewählte Station besitzt keine Transitous-ID."}}
    async def unsupported_flix():
        return {"routes": [], "candidate_routes": [], "provider_status": {"ok":False, "skipped":True, "error":"Ausgewählte Station besitzt keine Flix-ID."}}
    transitous_task = asyncio.create_task(transitous_search(provider_request) if transitous_supported else unsupported_transitous())
    flix_task = asyncio.create_task(flix_search(provider_request) if flix_supported else unsupported_flix())

    def provider_finished(step: str, task, routes_key: str, *, tuple_result: bool = False) -> None:
        try:
            value = task.result()
            if tuple_result: value = value[0]
            progress(step, "completed" if (value.get(routes_key) or []) else "empty")
        except BaseException as exc:
            progress(step, "failed", f"{type(exc).__name__}: {exc}")

    db_task.add_done_callback(lambda task: provider_finished("db", task, "journeys", tuple_result=True))
    transitous_task.add_done_callback(lambda task: provider_finished("transitous", task, "routes"))

    def flix_finished(task) -> None:
        try:
            value = task.result(); counts = value.get("candidate_counts") or {}
            ok = (value.get("provider_status") or {}).get("ok") is True
            detail = None if counts.get("bus") or counts.get("train") else ("Keine Flix-Verbindung verfügbar" if ok else "Flix konnte nicht geprüft werden")
            progress("gtfs", "completed" if ok else "failed", detail)
            progress("flixbus", "completed" if counts.get("bus") else ("empty" if ok else "failed"), detail)
            progress("flixtrain", "completed" if counts.get("train") else ("empty" if ok else "failed"), detail)
        except BaseException as exc:
            progress("gtfs", "failed", f"{type(exc).__name__}: {exc}")
            progress("flixbus", "failed"); progress("flixtrain", "failed")
    flix_task.add_done_callback(flix_finished)

    try:
        db_result, db_attempts = await db_task
        progress("db", "completed" if db_result.get("journeys") else "empty")
    except Exception as exc:
        progress("db", "failed", f"{type(exc).__name__}: {exc}")
        db_result = {"status": "failed", "journeys": []}
        db_attempts = [{"source": "db-api", "attempt": 1, "ok": False, "error": f"{type(exc).__name__}: {exc}"}]

    try:
        transitous_result = await transitous_task
        progress("transitous", "completed" if transitous_result.get("routes") else "empty")
    except Exception as exc:
        progress("transitous", "failed", f"{type(exc).__name__}: {exc}")
        transitous_result = {"routes": [], "diagnostic": {"ok": False, "error": f"{type(exc).__name__}: {exc}"}}

    try:
        flix_result = await flix_task
        counts = flix_result.get("candidate_counts") or {}
        provider_ok = (flix_result.get("provider_status") or {}).get("ok") is True
        detail = None if counts.get("bus") or counts.get("train") else ("Keine Flix-Verbindung verfügbar" if provider_ok else "Flix konnte nicht geprüft werden")
        progress("gtfs", "completed" if provider_ok else "failed", detail)
        progress("flixbus", "completed" if counts.get("bus") else ("empty" if provider_ok else "failed"), detail)
        progress("flixtrain", "completed" if counts.get("train") else ("empty" if provider_ok else "failed"), detail)
    except Exception as exc:
        progress("gtfs", "failed", f"{type(exc).__name__}: {exc}")
        progress("flixbus", "failed"); progress("flixtrain", "failed")
        flix_result = {"routes": [], "provider_status": {"provider": "flixbus", "ok": False, "error": f"{type(exc).__name__}: {exc}"}}

    progress("merge", "processing")
    db_routes = db_result.get("journeys") or []
    db_source = db_result.get("source")
    transitous_diagnostic = transitous_result.get("diagnostic")
    transitous_routes = transitous_result.get("routes") or []
    if transitous_routes:
        db_source = f"{db_source}+transitous" if db_routes and db_source else "transitous"
    def selected_scheduled(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
        annotate_departure_tolerance(route, request.travel_date, request.departure_after)
        for route in routes
        if _transport_selected(route, request) and route_departure_in_window(
            route, request.travel_date, request.departure_after,
            tolerance_minutes=SEARCH_DEPARTURE_TOLERANCE_MINUTES,
        )
        ]

    selected_db_routes = selected_scheduled(db_routes)
    selected_transitous_routes = selected_scheduled(transitous_routes)
    db_routes = [*selected_db_routes, *selected_transitous_routes]

    flix_routes = flix_result.get("candidate_routes") or flix_result.get("routes") or []
    flix_routes = [
        annotate_departure_tolerance(route, request.travel_date, request.departure_after)
        for route in flix_routes
        if _transport_selected(route, request) and route_departure_in_window(
            route, request.travel_date, request.departure_after,
            tolerance_minutes=SEARCH_DEPARTURE_TOLERANCE_MINUTES,
        )
    ]
    flix_routes = await complete_flix_routes(flix_routes, request)
    flix_routes = [route for route in flix_routes if _transport_selected(route, request)]
    db_routes = _attach_coverage_endpoints(db_routes, request)
    flix_routes = _attach_coverage_endpoints(flix_routes, request)

    # Historie ist eine unabhängige, rein additive Schicht. Fehler oder Timeouts
    # dürfen Providerresultate, Preise und Ranking niemals verändern.
    db_routes = await _enrich_history_bounded(db_routes)

    selected_db = rank_routes(db_routes, request.preference)[0] if db_routes else None
    split: dict[str, Any] = {
        "status": "skipped",
        "method": "db-vendo-direct-two-ticket",
        "reason": "Keine DB- oder DBnav-Verbindung mit Live-Preis und Analyse-Token verfügbar.",
    }
    if (
        request.split_ticket_check and selected_db
        and selected_db.get("db_source") in {"db", "dbnav"}
        and selected_db.get("analysis_token")
        and as_float(selected_db.get("price")) > 0
    ):
        try:
            split = await db_split_analysis(str(selected_db["analysis_token"]))
        except Exception as exc:
            split = {
                "status": "failed",
                "method": "db-vendo-direct-two-ticket",
                "error": f"{type(exc).__name__}: {exc}",
                "split_options": [],
            }

    if split.get("status") == "success" and selected_db:
        options = split.get("split_options") or []
        if options:
            selected_db = dict(selected_db)
            selected_db["best_split_price"] = options[0].get("total_price")
            selected_db["split_savings"] = options[0].get("savings")
            db_routes = [
                selected_db if route.get("id") == selected_db.get("id") else route
                for route in db_routes
            ]

    ranked_db = rank_routes(db_routes, request.preference)
    combined = rank_routes(db_routes + flix_routes, request.preference)
    visible_options = select_visible_ground_options(db_routes, flix_routes, request)
    priced = [
        route for route in combined
        if as_float(route.get("price")) > 0 and route.get("price_complete") is not False
    ]
    fastest = min(combined, key=lambda route: as_int(route.get("duration_minutes")) or 1_000_000, default=None)

    has_db_price = any(
        route.get("db_source") in {"db", "dbnav"} and as_float(route.get("price")) > 0
        for route in db_routes
    )
    manual_links = build_manual_db_links(
        request.origin, request.destination, request.travel_date, request.departure_after,
        departure_iso=(str(ranked_db[0].get("departure")) if ranked_db else None),
    )

    warnings: list[str] = []
    if not has_db_price:
        warnings.append("Kein belastbarer DB-Livepreis verfügbar; vorhandene Bahnzeiten können aus dem Fallback stammen.")
    if flix_routes:
        warnings.append("Flix-Preise vor der Buchung auf der Anbieterseite bestätigen.")
    if split.get("status") == "no_saving_found":
        warnings.append("Die interne Zwei-Ticket-Prüfung wurde durchgeführt und fand keine Ersparnis.")
    elif split.get("status") in {"failed", "unavailable", "skipped"}:
        warnings.append("Die interne Zwei-Ticket-Prüfung konnte für diese Verbindung nicht vollständig ausgewertet werden.")

    result = {
        "status": "ok" if combined else "manual_required",
        "search_mode": "price_compare",
        "current_date": today_iso(),
        "query": request.model_dump(),
        "db_source": db_source,
        "source_status": {
            "db_live_price_available": has_db_price,
            "db_attempts": compact_attempts(db_attempts),
            "transitous": transitous_diagnostic,
            "flix": flix_result.get("provider_status"),
        },
        "provider_statuses": [
            _provider_state(
                "DB", ok=any(item.get("ok") for item in db_attempts),
                skipped=all(item.get("skipped") for item in db_attempts), result_count=len(selected_db_routes),
            ),
            _provider_state(
                "Transitous", ok=bool((transitous_diagnostic or {}).get("ok")),
                skipped=bool((transitous_diagnostic or {}).get("skipped")), result_count=len(selected_transitous_routes),
            ),
            _provider_state(
                "Flix", ok=bool((flix_result.get("provider_status") or {}).get("ok")),
                skipped=bool((flix_result.get("provider_status") or {}).get("skipped")), result_count=len(flix_routes),
            ),
        ],
        "db_options": [compact_route(route) for route in ranked_db[:3]],
        "flix_options": (flix_result.get("routes") or [])[:3],
        "visible_options": [
            compact_route(route) if route.get("db_source") else route
            for route in visible_options
        ],
        "recommendation": {
            "fastest": compact_route(fastest) if isinstance(fastest, dict) and fastest.get("db_source") else fastest,
            "cheapest_with_live_price": compact_route(priced[0]) if priced and priced[0].get("db_source") else (priced[0] if priced else None),
        },
        "manual_db_links": manual_links,
        "split_ticket": _compact_split(split),
        "warnings": warnings,
    }
    progress("merge", "completed" if combined else "empty")
    return result


async def deutschlandticket(request: DeutschlandticketRequest) -> dict[str, Any]:
    db_task = asyncio.create_task(db_search_with_retry(
        origin=request.origin,
        destination=request.destination,
        travel_date=request.travel_date,
        departure_after=request.departure_after,
        mode="deutschlandticket",
        transport_mode=request.transport_mode,
        max_transfers=request.max_transfers,
        results=request.max_results,
    ))
    fallback = ReiseRequest(
        transport_mode=request.transport_mode,
        origin=request.origin,
        destination=request.destination,
        travel_date=request.travel_date,
        departure_after=request.departure_after,
        preference="fastest",
        include_flixtrain=False,
        include_flixbus=False,
        max_transfers=request.max_transfers,
        max_results=request.max_results,
        split_ticket_check=False,
    )
    transitous_task = asyncio.create_task(transitous_search(fallback, deutschlandticket_only=True))

    try:
        result, attempts = await db_task
    except Exception as exc:
        result = {"status": "failed", "journeys": []}
        attempts = [{"source": "db-api", "attempt": 1, "ok": False, "error": f"{type(exc).__name__}: {exc}"}]
    try:
        transitous_result = await transitous_task
    except Exception as exc:
        transitous_result = {"routes": [], "diagnostic": {"ok": False, "error": f"{type(exc).__name__}: {exc}"}}

    routes = result.get("journeys") or []
    routes = [route for route in routes if route_matches_transport(route, request.transport_mode)]
    source = result.get("source")
    if not routes:
        routes = transitous_result.get("routes") or []
        routes = [route for route in routes if route_matches_transport(route, request.transport_mode)]
        if routes:
            source = "transitous"

    try:
        routes = await enrich_routes_history(routes)
    except Exception:
        pass

    return {
        "status": "ok" if routes else "empty",
        "search_mode": "deutschlandticket",
        "current_date": today_iso(),
        "query": request.model_dump(),
        "source": source,
        "attempts": compact_attempts(attempts),
        "routes": [compact_route(route) for route in routes[: request.max_results]],
        "transitous_diagnostic": transitous_result.get("diagnostic"),
        "warning": (
            "Die Suche filtert Fernverkehr heraus, ist aber keine Tarifgarantie. Sonderzüge, einzelne Fähren "
            "und grenzüberschreitende Abschnitte müssen separat geprüft werden."
        ),
    }

def _route_arrival_date(result: dict[str, Any]) -> str | None:
    recommendation = result.get("recommendation") if isinstance(result.get("recommendation"), dict) else {}
    route = recommendation.get("cheapest_with_live_price") or recommendation.get("fastest")
    if not isinstance(route, dict):
        return None
    value = route.get("arrival")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None



def _recommended_live_price(result: dict[str, Any]) -> float:
    recommendation = result.get("recommendation") if isinstance(result.get("recommendation"), dict) else {}
    route = recommendation.get("cheapest_with_live_price")
    if not isinstance(route, dict):
        return 0.0
    return as_float(route.get("price"))

def _direction_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "query": result.get("query"),
        "db_source": result.get("db_source"),
        "db_options": result.get("db_options") or [],
        "flix_options": result.get("flix_options") or [],
        "visible_options": result.get("visible_options") or [],
        "recommendation": result.get("recommendation"),
        "split_ticket": result.get("split_ticket"),
        "manual_db_links": result.get("manual_db_links"),
        "warnings": result.get("warnings") or [],
        "provider_statuses": result.get("provider_statuses") or [],
    }


async def compare_ground_round_trip(
    *,
    origin: str,
    destination: str,
    outbound_date: str,
    return_date: str | None,
    stay_nights: int | None,
    departure_after: str,
    preference: str,
    include_flixtrain: bool,
    include_flixbus: bool,
    max_transfers: int | None,
    max_results: int,
    split_ticket_check: bool,
    deutschlandticket_mode: str | None = None,
    split_candidates: list[str] | None = None,
    one_way: bool = False,
    flix_origin_stop_id: str | None = None,
    flix_destination_stop_id: str | None = None,
    origin_station=None,
    destination_station=None,
    include_train: bool = True,
    include_bus: bool = True,
    transport_mode: GroundTransportMode = "all",
) -> dict[str, Any]:
    if deutschlandticket_mode == "only":
        outward_dt = await deutschlandticket(DeutschlandticketRequest(
            origin=origin, destination=destination, travel_date=outbound_date,
            departure_after=departure_after, max_transfers=max_transfers, max_results=max_results, transport_mode=transport_mode,
        ))
        outward_routes = outward_dt.get("routes") or []
        if one_way:
            complete = bool(outward_routes)
            return {
                "status": "ok" if complete else "partial",
                "search_mode": "ground_trip",
                "current_date": today_iso(),
                "route": {
                    "origin": origin, "destination": destination, "outbound_date": outbound_date,
                    "return_date": None, "return_date_source": None, "stay_nights": 0,
                },
                "trip_chain": ["outbound_ground"],
                "outbound": {"status": outward_dt.get("status"), "db_options": [], "flix_options": [], "mixed_ticket_options": [], "recommendation": None, "split_ticket": {"status": "not_applicable"}, "warnings": []},
                "return": None,
                "deutschlandticket": {"outbound": outward_routes, "return": [], "warning": outward_dt.get("warning"), "only": True},
                "price_summary": {
                    "outbound_live_price": 0.0 if complete else None,
                    "return_live_price": None,
                    "round_trip_live_price": None,
                    "known_total_price": 0.0 if complete else None,
                    "currency": "EUR" if complete else None,
                    "complete": complete,
                    "price_semantics": "deutschlandticket_covered_additional_cost",
                },
                "hotel": None,
            }
        return_source = "explicit" if return_date else None
        if not return_date and stay_nights:
            first_outward = (outward_dt.get("routes") or [None])[0]
            arrival_date = _route_arrival_date({"recommendation": {"fastest": first_outward}}) if first_outward else None
            return_date = (date.fromisoformat(arrival_date or outbound_date) + timedelta(days=stay_nights)).isoformat()
            return_source = "stay_duration"
        if not return_date:
            return {
                "status": "missing_fields", "search_mode": "ground_trip", "current_date": today_iso(),
                "missing_fields": ["return_date_or_stay_duration"],
            }
        return_dt = await deutschlandticket(DeutschlandticketRequest(
            origin=destination, destination=origin, travel_date=return_date,
            departure_after=departure_after, max_transfers=max_transfers, max_results=max_results, transport_mode=transport_mode,
        ))
        outward_routes = outward_dt.get("routes") or []
        return_routes = return_dt.get("routes") or []
        complete = bool(outward_routes and return_routes)
        return {
            "status": "ok" if complete else "partial",
            "search_mode": "ground_trip",
            "current_date": today_iso(),
            "route": {
                "origin": origin, "destination": destination, "outbound_date": outbound_date,
                "return_date": return_date, "return_date_source": return_source, "stay_nights": stay_nights,
            },
            "trip_chain": ["outbound_ground", "return_ground"],
            "outbound": {"status": outward_dt.get("status"), "db_options": [], "flix_options": [], "mixed_ticket_options": [], "recommendation": None, "split_ticket": {"status": "not_applicable"}, "warnings": []},
            "return": {"status": return_dt.get("status"), "db_options": [], "flix_options": [], "mixed_ticket_options": [], "recommendation": None, "split_ticket": {"status": "not_applicable"}, "warnings": []},
            "deutschlandticket": {
                "outbound": outward_routes, "return": return_routes,
                "warning": outward_dt.get("warning") or return_dt.get("warning"),
                "only": True,
            },
            "price_summary": {
                "outbound_live_price": 0.0 if outward_routes else None,
                "return_live_price": 0.0 if return_routes else None,
                "round_trip_live_price": 0.0 if complete else None,
                "currency": "EUR" if complete else None,
                "complete": complete,
                "price_semantics": "deutschlandticket_covered_additional_cost",
            },
            "hotel": None,
        }

    outbound_request = ReiseRequest(
        origin=origin,
        destination=destination,
        travel_date=outbound_date,
        departure_after=departure_after,
        preference=preference,
        include_train=include_train,
        include_bus=include_bus,
        transport_mode=transport_mode,
        include_flixtrain=include_flixtrain,
        include_flixbus=include_flixbus,
        max_transfers=max_transfers,
        max_results=max_results,
        split_ticket_check=split_ticket_check,
        deutschlandticket=deutschlandticket_mode == "include",
        flix_origin_stop_id=flix_origin_stop_id,
        flix_destination_stop_id=flix_destination_stop_id,
        origin_station=origin_station,
        destination_station=destination_station,
    )
    outbound = await compare_trip(outbound_request)

    if one_way:
        outbound_mixed: list[dict[str, Any]] = []
        if deutschlandticket_mode == "include" and split_candidates:
            outbound_mixed = await ground_mixed_options(
                origin, destination, outbound_date, departure_after, split_candidates,
                include_flixbus=include_flixbus, include_flixtrain=include_flixtrain,
            )
        dticket: dict[str, Any] | None = None
        if deutschlandticket_mode == "include":
            outward_dt = await deutschlandticket(DeutschlandticketRequest(
                origin=origin, destination=destination, travel_date=outbound_date,
                departure_after=departure_after, max_transfers=max_transfers, max_results=max_results, transport_mode=transport_mode,
            ))
            dticket = {
                "outbound": outward_dt.get("routes") or [],
                "return": [],
                "warning": outward_dt.get("warning"),
            }
        outbound_price = _recommended_live_price(outbound)
        return {
            "status": "ok" if outbound.get("status") in {"ok", "manual_required"} else "partial",
            "search_mode": "ground_trip",
            "current_date": today_iso(),
            "route": {
                "origin": origin, "destination": destination, "outbound_date": outbound_date,
                "return_date": None, "return_date_source": None, "stay_nights": 0,
            },
            "trip_chain": ["outbound_ground"],
            "outbound": {**_direction_summary(outbound), "mixed_ticket_options": outbound_mixed},
            "return": None,
            "deutschlandticket": dticket,
            "price_summary": {
                "outbound_live_price": round(outbound_price, 2) if outbound_price > 0 else None,
                "return_live_price": None,
                "round_trip_live_price": None,
                "known_total_price": round(outbound_price, 2) if outbound_price > 0 else None,
                "currency": "EUR" if outbound_price > 0 else None,
                "complete": bool(outbound_price > 0),
            },
            "hotel": None,
        }

    return_source = "explicit" if return_date else None
    if not return_date and stay_nights:
        anchor = _route_arrival_date(outbound) or outbound_date
        return_date = (date.fromisoformat(anchor) + timedelta(days=stay_nights)).isoformat()
        return_source = "stay_duration"

    if not return_date:
        return {
            "status": "missing_fields",
            "search_mode": "ground_trip",
            "current_date": today_iso(),
            "missing_fields": ["return_date_or_stay_duration"],
            "outbound": _direction_summary(outbound),
        }

    return_request = ReiseRequest(
        origin=destination,
        destination=origin,
        travel_date=return_date,
        departure_after=departure_after,
        preference=preference,
        include_train=include_train,
        include_bus=include_bus,
        transport_mode=transport_mode,
        include_flixtrain=include_flixtrain,
        include_flixbus=include_flixbus,
        max_transfers=max_transfers,
        max_results=max_results,
        split_ticket_check=split_ticket_check,
        deutschlandticket=deutschlandticket_mode == "include",
        flix_origin_stop_id=flix_destination_stop_id,
        flix_destination_stop_id=flix_origin_stop_id,
        origin_station=destination_station,
        destination_station=origin_station,
    )
    return_result = await compare_trip(return_request)
    outbound_mixed: list[dict[str, Any]] = []
    return_mixed: list[dict[str, Any]] = []
    if deutschlandticket_mode == "include" and split_candidates:
        outbound_mixed, return_mixed = await asyncio.gather(
            ground_mixed_options(origin, destination, outbound_date, departure_after, split_candidates, include_flixbus=include_flixbus, include_flixtrain=include_flixtrain),
            ground_mixed_options(destination, origin, return_date, departure_after, list(reversed(split_candidates)), include_flixbus=include_flixbus, include_flixtrain=include_flixtrain),
        )

    dticket: dict[str, Any] | None = None
    if deutschlandticket_mode == "include":
        outward_dt, return_dt = await asyncio.gather(
            deutschlandticket(DeutschlandticketRequest(
                origin=origin, destination=destination, travel_date=outbound_date,
                departure_after=departure_after, max_transfers=max_transfers, max_results=max_results, transport_mode=transport_mode,
            )),
            deutschlandticket(DeutschlandticketRequest(
                origin=destination, destination=origin, travel_date=return_date,
                departure_after=departure_after, max_transfers=max_transfers, max_results=max_results, transport_mode=transport_mode,
            )),
        )
        dticket = {
            "outbound": outward_dt.get("routes") or [],
            "return": return_dt.get("routes") or [],
            "warning": outward_dt.get("warning") or return_dt.get("warning"),
        }

    status = (
        "ok"
        if outbound.get("status") in {"ok", "manual_required"}
        and return_result.get("status") in {"ok", "manual_required"}
        else "partial"
    )
    outbound_price = _recommended_live_price(outbound)
    return_price = _recommended_live_price(return_result)
    price_summary = {
        "outbound_live_price": round(outbound_price, 2) if outbound_price > 0 else None,
        "return_live_price": round(return_price, 2) if return_price > 0 else None,
        "round_trip_live_price": round(outbound_price + return_price, 2) if outbound_price > 0 and return_price > 0 else None,
        "currency": "EUR" if outbound_price > 0 or return_price > 0 else None,
        "complete": bool(outbound_price > 0 and return_price > 0),
    }
    return {
        "status": status,
        "search_mode": "ground_trip",
        "current_date": today_iso(),
        "route": {
            "origin": origin,
            "destination": destination,
            "outbound_date": outbound_date,
            "return_date": return_date,
            "return_date_source": return_source,
            "stay_nights": stay_nights,
        },
        "trip_chain": ["outbound_ground", "return_ground"],
        "outbound": {**_direction_summary(outbound), "mixed_ticket_options": outbound_mixed},
        "return": {**_direction_summary(return_result), "mixed_ticket_options": return_mixed},
        "deutschlandticket": dticket,
        "price_summary": price_summary,
        "hotel": None,
    }
