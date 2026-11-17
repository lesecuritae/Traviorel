from __future__ import annotations

from datetime import datetime
from typing import Any

from .ground_connections import complete_connections

from .utils import as_float, as_int


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _clean(item)) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [
            cleaned
            for item in value
            if (cleaned := _clean(item)) not in (None, "", [], {})
        ]
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _minutes_between(earlier: Any, later: Any) -> int | None:
    start = _parse_datetime(earlier)
    end = _parse_datetime(later)
    if start is None or end is None:
        return None
    try:
        seconds = (end - start).total_seconds()
    except TypeError:
        seconds = (end.replace(tzinfo=None) - start.replace(tzinfo=None)).total_seconds()
    if seconds < 0:
        return None
    return int(seconds // 60)


def _segment(segment: dict[str, Any]) -> dict[str, Any]:
    legs = []
    modes = []
    for leg in (segment.get("legs") or [])[:8]:
        if not isinstance(leg, dict):
            continue
        mode = leg.get("mode") or leg.get("type")
        if mode and mode not in modes:
            modes.append(mode)
        legs.append(_clean({
            "mode": mode,
            "line": leg.get("line"),
            "provider": leg.get("provider"),
            "origin": leg.get("origin"),
            "destination": leg.get("destination"),
            "departure": leg.get("departure"),
            "arrival": leg.get("arrival"),
        }))

    return _clean({
        "provider": segment.get("provider"),
        "type": segment.get("type") or segment.get("mode"),
        "modes": modes,
        "line": segment.get("line"),
        "origin": segment.get("origin"),
        "destination": segment.get("destination"),
        "departure": segment.get("departure"),
        "arrival": segment.get("arrival"),
        "duration_minutes": as_int(segment.get("duration_minutes")) or None,
        "price": as_float(segment.get("price")) if segment.get("price") is not None else None,
        "currency": segment.get("currency"),
        "ticket": segment.get("ticket"),
        "legs": legs,
        "offer_url": segment.get("booking_url"),
    })


def _feeder_option(option: Any) -> dict[str, Any] | None:
    if not isinstance(option, dict):
        return None
    price_known = option.get("price_known") is True
    requires_deutschlandticket = option.get("requires_deutschlandticket") is True
    total_price = as_float(option.get("total_price")) if price_known else None
    segments = [
        _segment(item)
        for item in (option.get("segments") or [])[:4]
        if isinstance(item, dict)
    ]
    modes: list[str] = []
    for segment in segments:
        for mode in segment.get("modes") or []:
            if mode not in modes:
                modes.append(mode)
        if not segment.get("modes") and segment.get("type") and segment["type"] not in modes:
            modes.append(segment["type"])

    selected_destination = None
    if segments:
        selected_destination = segments[-1].get("destination")
        if not selected_destination:
            legs = segments[-1].get("legs") or []
            if legs:
                selected_destination = legs[-1].get("destination")

    return _clean({
        "type": option.get("type"),
        "label": option.get("label"),
        "transport_modes": modes,
        "selected_destination": selected_destination,
        "departure": option.get("departure"),
        "arrival": option.get("arrival"),
        "duration_minutes": as_int(option.get("duration_minutes")) or None,
        "additional_ticket_cost": total_price,
        "price_known": price_known,
        "currency": "EUR" if price_known else None,
        "ticket_coverage": (
            "deutschlandticket_candidate_mode_filtered"
            if requires_deutschlandticket and option.get("deutschlandticket_tariff_guaranteed") is False
            else "existing_deutschlandticket"
            if requires_deutschlandticket
            else "separate_paid_ticket"
            if price_known
            else None
        ),
        "requires_deutschlandticket": requires_deutschlandticket,
        "deutschlandticket_tariff_guaranteed": option.get("deutschlandticket_tariff_guaranteed"),
        "price_note": option.get("price_note"),
        "self_managed_transfers": as_int(option.get("self_managed_transfers")),
        "split_station": option.get("split_station"),
        "segments": segments,
        "offer_url": option.get("booking_url"),
    })


def _feeder(component: Any) -> dict[str, Any] | None:
    if not isinstance(component, dict):
        return None

    selected_raw = component.get("selected_option") if isinstance(component.get("selected_option"), dict) else None
    if selected_raw is None:
        return _clean({
            "status": component.get("status"),
            "direction": component.get("direction"),
            "target_airport_station": component.get("airport_station"),
            "required_arrival_before": component.get("required_arrival_before"),
            "earliest_departure": component.get("earliest_departure"),
            "manual_required": True,
            "manual_db_links": component.get("manual_db_links"),
        })

    raw_options = [selected_raw]
    raw_options.extend(
        item for item in (component.get("alternatives") or [])
        if isinstance(item, dict)
    )

    # Deduplizieren, aber unterschiedliche Kombinationen bewusst erhalten.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for option in raw_options:
        key = (
            option.get("type"), option.get("split_station"), option.get("departure"),
            option.get("arrival"), as_float(option.get("total_price")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(option)

    def known_price(item: dict[str, Any]) -> float:
        return as_float(item.get("total_price")) if item.get("price_known") is True else 1_000_000.0

    def duration(item: dict[str, Any]) -> int:
        return as_int(item.get("duration_minutes")) or 1_000_000

    dticket_options = [
        item for item in unique
        if item.get("requires_deutschlandticket") is True or "deutschlandticket" in str(item.get("type") or "").casefold()
    ]
    paid_options = [item for item in unique if item not in dticket_options]
    dticket = min(dticket_options, key=lambda item: (known_price(item), duration(item)), default=None)
    dticket_view = _feeder_option(dticket)
    if dticket_view is None and component.get("deutschlandticket_considered") is True:
        dticket_view = {
            "status": "unavailable",
            "requires_deutschlandticket": True,
            "manual_required": True,
            "reason": "Keine D-Ticket-fähige Zubringerverbindung automatisch bestätigt.",
        }
    # Wenn ein Deutschlandticket-Pfad existiert, ist der zweite Reiter bewusst
    # die günstigste andere Variante. Sonst würde 0 EUR denselben Pfad doppelt zeigen
    # und z. B. FlixTrain/ICE-Mischungen unsichtbar machen.
    cheapest_pool = paid_options if dticket is not None and paid_options else unique
    cheapest = min(cheapest_pool, key=lambda item: (known_price(item), duration(item)), default=None)
    fastest = min(unique, key=lambda item: (duration(item), known_price(item)), default=None)

    return _clean({
        "status": component.get("status"),
        "direction": component.get("direction"),
        "target_airport_station": component.get("airport_station"),
        "required_arrival_before": component.get("required_arrival_before"),
        "earliest_departure": component.get("earliest_departure"),
        "manual_required": False,
        "selected": _feeder_option(selected_raw),
        "views": {
            "deutschlandticket": dticket_view,
            "cheapest": _feeder_option(cheapest),
            "fastest": _feeder_option(fastest),
        },
        "alternatives": [_feeder_option(item) for item in unique[:5]],
    })

def _flight_side(option: dict[str, Any], side: str) -> dict[str, Any] | None:
    section = option.get(side) if isinstance(option.get(side), dict) else None
    if section is None:
        return None

    legs = []
    for leg in (section.get("legs") or [])[:4]:
        if not isinstance(leg, dict):
            continue
        legs.append(_clean({
            "from": leg.get("departure_airport"),
            "to": leg.get("arrival_airport"),
            "departure": leg.get("departure"),
            "arrival": leg.get("arrival"),
            "airline": leg.get("airline"),
            "flight_number": leg.get("flight_number"),
        }))

    return _clean({
        "from": section.get("departure_airport"),
        "to": section.get("arrival_airport"),
        "departure": section.get("departure"),
        "arrival": section.get("arrival"),
        "stops": as_int(section.get("stops")),
        "legs": legs,
    })


def _flight(option: Any, *, manual_url: str | None = None) -> dict[str, Any] | None:
    if not isinstance(option, dict):
        return None

    price = as_float(option.get("price"))
    offer_url = option.get("booking_url")
    return _clean({
        "provider": option.get("provider"),
        "booking_status": "not_booked",
        "price": {
            "value": price,
            "currency": option.get("currency") or "EUR",
            "scope": "round_trip",
        } if price > 0 else None,
        "offer_url": offer_url,
        "manual_url": manual_url if not offer_url else None,
        "outbound": _flight_side(option, "outbound"),
        "return": _flight_side(option, "return"),
        "warnings": option.get("warnings"),
    })


def _transfer(component: Any, *, label: str) -> dict[str, Any] | None:
    if not isinstance(component, dict):
        return None

    options = []
    for item in (component.get("options") or [])[:3]:
        if not isinstance(item, dict):
            continue
        departure = item.get("departure") if isinstance(item.get("departure"), dict) else {}
        arrival = item.get("arrival") if isinstance(item.get("arrival"), dict) else {}
        departure_time = departure.get("time") if departure else item.get("departure")
        arrival_time = arrival.get("time") if arrival else item.get("arrival")
        departure_station = departure.get("station") or departure.get("city") or item.get("origin")
        arrival_station = arrival.get("station") or arrival.get("city") or item.get("destination")
        raw_price = as_float(item.get("price"))
        price_known = raw_price > 0
        options.append(_clean({
            "provider": item.get("provider"),
            "type": item.get("type"),
            "departure_station": departure_station,
            "price_note": item.get("price_note") if not price_known else None,
            "departure": departure_time,
            "arrival_station": arrival_station,
            "arrival": arrival_time,
            "duration_minutes": as_int(item.get("duration_minutes")) or None,
            "price": raw_price if price_known else None,
            "currency": item.get("currency") if price_known else None,
            "offer_url": item.get("booking_url"),
        }))

    manual_required = not bool(options)
    return _clean({
        "route": label,
        "availability": "manual_required" if manual_required else "options_found",
        "manual_required": manual_required,
        "date": component.get("date"),
        "depart_after": component.get("depart_after"),
        "arrive_before": component.get("arrive_before"),
        "options": options,
        "provider_statuses": component.get("provider_statuses"),
        "manual_url": component.get("manual_url") if manual_required else None,
    })


def _hotel(component: Any, checkin: str | None, checkout: str | None) -> dict[str, Any] | None:
    if not isinstance(component, dict):
        return None

    raw_options = [item for item in (component.get("hotel_options") or []) if isinstance(item, dict)]
    verified_options = [item for item in raw_options if as_float(item.get("verified_total_price")) > 0]
    selected_options = verified_options[:3] if verified_options else raw_options[:3]

    options = []
    for item in selected_options:
        verified = as_float(item.get("verified_total_price"))
        nightly = as_float(item.get("nightly_price"))
        headline = as_float(item.get("headline_price"))
        price = None
        if verified > 0:
            price = {
                "value": verified,
                "currency": item.get("currency") or "EUR",
                "basis": "verified_total_stay",
            }
        elif nightly > 0:
            price = {
                "value": nightly,
                "currency": item.get("currency") or "EUR",
                "basis": "nightly_unverified",
            }
        elif headline > 0:
            price = {
                "value": headline,
                "currency": item.get("currency") or "EUR",
                "basis": item.get("price_basis") or "provider_headline_unverified",
            }

        options.append(_clean({
            "name": item.get("name"),
            "rating": as_float(item.get("rating")) or None,
            "stars": as_int(item.get("stars")) or None,
            "property_type": item.get("property_type"),
            "address": item.get("address"),
            "provider": item.get("provider"),
            "provider_min_stars": as_int(item.get("provider_min_stars")) or None,
            "price": price,
            "booking_status": "not_booked",
            "offer_url": item.get("booking_url"),
        }))

    return _clean({
        "status": component.get("status"),
        "booking_status": "not_booked",
        "checkin": checkin,
        "checkout": checkout,
        "options": options,
        "provider_statuses": component.get("provider_statuses"),
        "verified_total_count": component.get("verified_total_count"),
        "manual_url": component.get("manual_booking_url") if not options else None,
    })


def _cost_summary(summary: Any) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None

    known = as_float(summary.get("known_total_price"))
    complete = summary.get("total_price_complete") is True
    priced = summary.get("priced_components") if isinstance(summary.get("priced_components"), dict) else {}
    missing = summary.get("missing_price_components") if isinstance(summary.get("missing_price_components"), list) else []

    return _clean({
        "currency": summary.get("currency") or "EUR",
        "complete": complete,
        "verified_total": round(known, 2) if complete and known > 0 else None,
        "known_subtotal": round(known, 2) if not complete and known > 0 else None,
        "priced_components": priced,
        "missing_components": missing,
    })


def _buffer_check(
    feeder: Any,
    flight_side: Any,
    *,
    required_minutes: Any,
    mode: str,
) -> dict[str, Any] | None:
    if not isinstance(feeder, dict) or not isinstance(flight_side, dict):
        return None
    selected = feeder.get("selected_option") if isinstance(feeder.get("selected_option"), dict) else None
    if selected is None:
        return None

    required = as_int(required_minutes)
    if mode == "before_flight":
        feeder_time = selected.get("arrival")
        flight_time = flight_side.get("departure")
        actual = _minutes_between(feeder_time, flight_time)
        latest = feeder.get("required_arrival_before")
        return _clean({
            "required_minutes": required or None,
            "actual_minutes": actual,
            "satisfied": actual >= required if actual is not None and required > 0 else None,
            "feeder_arrival": feeder_time,
            "flight_departure": flight_time,
            "latest_acceptable_arrival": latest,
        })

    feeder_time = selected.get("departure")
    flight_time = flight_side.get("arrival")
    actual = _minutes_between(flight_time, feeder_time)
    earliest = feeder.get("earliest_departure")
    return _clean({
        "required_minutes": required or None,
        "actual_minutes": actual,
        "satisfied": actual >= required if actual is not None and required > 0 else None,
        "flight_arrival": flight_time,
        "feeder_departure": feeder_time,
        "earliest_acceptable_departure": earliest,
    })


def _trip_context(result: dict[str, Any]) -> dict[str, Any]:
    candidates = [item for item in (result.get("airport_candidates") or []) if isinstance(item, dict)]
    selected = next(
        (item for item in candidates if item.get("origin_airport") == result.get("recommended_origin_airport")),
        candidates[0] if candidates else {},
    )

    direct_query = selected.get("feeder_direct_query") if isinstance(selected.get("feeder_direct_query"), dict) else {}
    origin = direct_query.get("origin") or result.get("recommended_origin_airport")
    destination = result.get("destination_city") or result.get("destination_iata")
    destination_iata = result.get("destination_iata")
    selected_flight = selected.get("selected_flight") if isinstance(selected.get("selected_flight"), dict) else {}
    needs_return = "return_flight" in (result.get("trip_chain") or [])

    def _route_complete(option: Any) -> bool:
        if not isinstance(option, dict):
            return False
        outbound = option.get("outbound") if isinstance(option.get("outbound"), dict) else {}
        inbound = option.get("return") if isinstance(option.get("return"), dict) else {}
        expected_origin = result.get("recommended_origin_airport") or selected.get("origin_airport")
        outbound_matches = (
            outbound.get("departure_airport") == expected_origin
            and outbound.get("arrival_airport") == destination_iata
        )
        if not needs_return:
            return bool(outbound_matches)
        return bool(
            outbound_matches
            and inbound.get("departure_airport") == destination_iata
            and inbound.get("arrival_airport") == expected_origin
        )

    # flight_options sind bereits vom Provider-Layer richtungsgeprüft. Falls
    # selected_flight im Paket-Slot fehlt, darf die öffentliche Ausgabe trotzdem
    # nicht zu einem leeren Flug werden. Nur einen vollständig passenden
    # Hin-/Rückflug als Fallback übernehmen.
    if not _route_complete(selected_flight):
        selected_flight = next(
            (item for item in (selected.get("flight_options") or []) if _route_complete(item)),
            {},
        )

    outbound_flight = selected_flight.get("outbound") if isinstance(selected_flight.get("outbound"), dict) else {}
    return_flight = selected_flight.get("return") if isinstance(selected_flight.get("return"), dict) else {}

    notices = [
        "Das Backend hat keine Buchung oder Reservierung vorgenommen.",
        "Angebots- und Suchlinks sind nur Verweise; Verfügbarkeit und Endpreis müssen beim Anbieter bestätigt werden.",
        "Ein vorhandenes Deutschlandticket kann die zusätzlichen Ticketkosten auf 0 EUR senken, wenn dies ausdrücklich ausgewiesen ist. Es garantiert keinen Anschluss an einen getrennt gebuchten Flug oder eine andere Leistung.",
    ]

    return _clean({
        "route": {
            "origin": origin,
            "destination": destination,
            "outbound_date": result.get("departure_date"),
            "return_date": result.get("return_date"),
            "stay_nights": result.get("requested_stay_nights"),
            "journey_type": result.get("journey_type") or ("round_trip" if needs_return else "one_way"),
            "origin_airport": result.get("recommended_origin_airport"),
            "destination_airport": destination_iata,
        },
        "booking": {
            "performed": False,
            "status": "not_booked",
        },
        "outbound_feeder": _feeder(selected.get("outbound_feeder")),
        "airport_buffer_check": _buffer_check(
            selected.get("outbound_feeder"),
            outbound_flight,
            required_minutes=result.get("airport_buffer_minutes"),
            mode="before_flight",
        ),
        "flight": _flight(selected_flight, manual_url=selected.get("flight_manual_url")),
        "outbound_transfer": _transfer(
            selected.get("outbound_destination_transfer"),
            label=f"{destination_iata} Airport -> {destination}" if destination_iata and destination else "airport_to_destination",
        ),
        "hotel": _hotel(
            selected.get("hotel"),
            selected.get("hotel_checkin_date"),
            selected.get("hotel_checkout_date"),
        ),
        "return_transfer": _transfer(
            selected.get("return_destination_transfer"),
            label=f"{destination} -> {destination_iata} Airport" if destination_iata and destination else "destination_to_airport",
        ),
        "return_feeder": _feeder(selected.get("return_feeder")),
        "post_flight_buffer_check": _buffer_check(
            selected.get("return_feeder"),
            return_flight,
            required_minutes=result.get("return_airport_buffer_minutes"),
            mode="after_flight",
        ),
        "cost_summary": _cost_summary(selected.get("cost_summary")),
        "provider_health": {
            "flight": selected.get("flight_provider_statuses"),
            "flight_summary": selected.get("flight_provider_summary"),
            "hotel": (selected.get("hotel") or {}).get("provider_statuses") if isinstance(selected.get("hotel"), dict) else None,
            "outbound_transfer": (selected.get("outbound_destination_transfer") or {}).get("provider_statuses") if isinstance(selected.get("outbound_destination_transfer"), dict) else None,
            "return_transfer": (selected.get("return_destination_transfer") or {}).get("provider_statuses") if isinstance(selected.get("return_destination_transfer"), dict) else None,
            "outbound_feeder_db": (selected.get("outbound_feeder") or {}).get("db_attempts") if isinstance(selected.get("outbound_feeder"), dict) else None,
            "return_feeder_db": (selected.get("return_feeder") or {}).get("db_attempts") if isinstance(selected.get("return_feeder"), dict) else None,
        },
        "notices": notices,
    })


def _public_links(value: Any) -> Any:
    """Rename provider URLs so a link cannot be mistaken for a completed booking."""
    if isinstance(value, list):
        return [_public_links(item) for item in value]
    if not isinstance(value, dict):
        return value
    output = {}
    for key, item in value.items():
        if key == "booking_url":
            output["offer_url"] = _public_links(item)
        elif key == "manual_booking_url":
            output["manual_url"] = _public_links(item)
        else:
            output[key] = _public_links(item)
    return output


def _ground_direction(component: Any, dticket_routes: Any = None) -> dict[str, Any] | None:
    if not isinstance(component, dict):
        return None
    recommendation = component.get("recommendation") if isinstance(component.get("recommendation"), dict) else {}
    return _clean({
        "status": component.get("status"),
        "query": component.get("query"),
        "best_live_price": _public_links(recommendation.get("cheapest_with_live_price")),
        "fastest": _public_links(recommendation.get("fastest")),
        "db_options": _public_links((component.get("db_options") or [])[:3]),
        "flix_options": _public_links((component.get("flix_options") or [])[:3]),
        "visible_options": _public_links(component.get("visible_options") or []),
        "mixed_ticket_options": _public_links((component.get("mixed_ticket_options") or [])[:8]),
        "split_ticket": _public_links(component.get("split_ticket")),
        "manual_db_links": _public_links(component.get("manual_db_links")),
        "warnings": component.get("warnings"),
        "provider_statuses": component.get("provider_statuses"),
        "connections": _public_links(complete_connections(
            component,
            dticket_routes if isinstance(dticket_routes, list) else [],
        )),
    })


def _ground_trip_context(result: dict[str, Any]) -> dict[str, Any]:
    dticket = result.get("deutschlandticket") if isinstance(result.get("deutschlandticket"), dict) else {}
    return _clean({
        "route": result.get("route"),
        "booking": {"performed": False, "status": "not_booked"},
        "outbound": _ground_direction(result.get("outbound"), dticket.get("outbound")),
        "return": _ground_direction(result.get("return"), dticket.get("return")),
        "deutschlandticket": _public_links(result.get("deutschlandticket")),
        "price_summary": result.get("price_summary"),
        "hotel_required": False,
    })


def public_result(result: dict[str, Any]) -> dict[str, Any]:
    """Gib nur geprüfte, UI-taugliche Daten aus."""
    base = {
        "status": result.get("status"),
        "search_mode": result.get("search_mode"),
        "current_date": result.get("current_date"),
        "journey_id": result.get("journey_id"),
        "cache": result.get("cache"),
    }

    if result.get("search_mode") == "ground_trip":
        base["response_context"] = _ground_trip_context(result)
        if result.get("error"):
            base["error"] = result.get("error")
        return _clean(base)

    if result.get("search_mode") == "trip_plan":
        base["response_context"] = _trip_context(result)
        if result.get("clarification"):
            base["clarification"] = result.get("clarification")
        if result.get("error"):
            base["error"] = result.get("error")
        return _clean(base)

    context: dict[str, Any] = {
        "booking": {"performed": False, "status": "not_booked"},
    }
    for key, value in result.items():
        if key in {
            "router", "cache", "journey_id",
            "status", "search_mode", "current_date",
        }:
            continue
        public_key = (
            "offer_url" if key == "booking_url"
            else "manual_url" if key == "manual_booking_url"
            else key
        )
        context.setdefault(public_key, _public_links(value))
    base["response_context"] = context
    return _clean(base)
