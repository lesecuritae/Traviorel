from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any

from .config import MAX_FLIGHT_PACKAGES, MAX_RETURN_DATE_PROBES, TZ, today_iso
from .feeder import (
    _best_price, _feeder_price_known, _flight_probe_score, _flight_times,
    _probe_outbound_feeder, _probe_return_feeder, _selected_price, feeder_outbound, feeder_return,
)
from .airports import (
    AIRPORT_CITY_NAMES, flight_local_value, local_cutoff, resolve_destination_airport,
    resolve_feeder_airport_station, resolve_origin_airports, return_departure_date, stay_return_date,
)
from .models import FlightRequest, HotelRequest, TripRequest
from .provider_cache import airport_transfer_search, flight_search, hotel_search, return_transfer_search
from .utils import as_float

logger = logging.getLogger("reisevergleich.planner")


def _feeder_probe_options(
    options: list[dict[str, Any]],
    *,
    travel_date: str,
    departure_after: str,
    buffer_minutes: int,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Remove flights whose airport cutoff precedes the hard home-departure floor."""
    lower_bound = datetime.fromisoformat(
        f"{travel_date}T{departure_after}:00"
    ).replace(tzinfo=TZ)
    compatible: list[dict[str, Any]] = []
    for option in options:
        outbound_departure, _, _, _ = _flight_times(option)
        if outbound_departure is None:
            continue
        airport_cutoff = outbound_departure.astimezone(TZ) - timedelta(minutes=buffer_minutes)
        if airport_cutoff < lower_bound:
            continue
        compatible.append(option)
        if len(compatible) >= max(1, limit):
            break
    return compatible


def _component_price(component: dict[str, Any] | None, options_key: str = "routes") -> float:
    if not isinstance(component, dict):
        return 0.0
    selected = _selected_price(component)
    if selected > 0:
        return selected
    options = component.get(options_key)
    if not isinstance(options, list):
        return 0.0
    values = []
    for item in options:
        if not isinstance(item, dict):
            continue
        values.extend([
            as_float(item.get("total_price")),
            as_float(item.get("price")),
        ])
    values = [value for value in values if value > 0]
    return min(values) if values else 0.0


def _candidate_totals(
    candidate: dict[str, Any],
    hotel: dict[str, Any] | None,
    outbound_transfer: dict[str, Any] | None,
    return_transfer: dict[str, Any] | None,
) -> dict[str, Any]:
    components: dict[str, float] = {}
    missing: list[str] = []

    selected_flight = candidate.get("selected_flight") if isinstance(candidate.get("selected_flight"), dict) else None
    flight_options = candidate.get("flight_options") or []
    flight_price = as_float(selected_flight.get("price")) if selected_flight else _best_price(flight_options, "price")
    if flight_price > 0:
        flight_currency = str((selected_flight or {}).get("currency") or "").upper()
        if flight_currency == "EUR":
            components["flight"] = flight_price
        elif flight_currency:
            missing.append("flight_currency_conversion")
        else:
            missing.append("flight_currency_unknown")
    else:
        missing.append("flight")

    outbound_feeder = candidate.get("outbound_feeder")
    outbound_db = _component_price(outbound_feeder)
    if outbound_feeder:
        if outbound_db > 0 or _feeder_price_known(outbound_feeder):
            components["outbound_db"] = outbound_db
        else:
            missing.append("outbound_db")

    return_feeder = candidate.get("return_feeder")
    return_db = _component_price(return_feeder)
    if return_feeder:
        if return_db > 0 or _feeder_price_known(return_feeder):
            components["return_db"] = return_db
        else:
            missing.append("return_db")

    transfer_out = _component_price(outbound_transfer, "options")
    if outbound_transfer:
        if transfer_out > 0:
            components["outbound_transfer"] = transfer_out
        else:
            missing.append("outbound_transfer")

    transfer_back = _component_price(return_transfer, "options")
    if return_transfer:
        if transfer_back > 0:
            components["return_transfer"] = transfer_back
        else:
            missing.append("return_transfer")

    hotel_total = 0.0
    if hotel:
        verified_hotel_totals = [
            as_float(option.get("verified_total_price"))
            for option in hotel.get("hotel_options") or []
            if isinstance(option, dict) and as_float(option.get("verified_total_price")) > 0
        ]
        hotel_total = min(verified_hotel_totals) if verified_hotel_totals else 0.0
        if hotel_total > 0:
            components["hotel"] = hotel_total
        else:
            missing.append("hotel_verified_total")

    known_total = round(sum(components.values()), 2)
    return {
        "known_total_price": known_total,
        "currency": "EUR",
        "total_price_complete": not missing,
        "missing_price_components": missing,
        "priced_components": components,
    }


async def _flight_result_for_stay(
    code: str,
    destination_iata: str,
    departure_date: str,
    request: TripRequest,
    *,
    explicit_return_date: str | None,
    stay_nights: int | None,
) -> dict[str, Any]:
    async def search(return_date: str | None) -> dict[str, Any]:
        return await flight_search(FlightRequest(
            origin_iata=code,
            destination_iata=destination_iata,
            departure_date=departure_date,
            return_date=return_date,
            adults=request.adults,
            cabin=request.cabin,
            stops=request.stops,
            max_price=request.flight_max_price,
            max_results=10,
        ))

    if explicit_return_date or not stay_nights:
        return await search(explicit_return_date)

    # Für eine Dauer wie "eine Woche" zählt das lokale Ankunftsdatum am Ziel.
    # Deshalb zuerst den Hinflug ohne erfundenes Rückflugdatum suchen. Ein Nachtflug
    # kann sonst dazu führen, dass die eigentlich richtige Rückflug-Suche nie startet.
    outbound_only = await search(None)
    desired_dates: list[str] = []
    for option in (outbound_only.get("flight_options") or [])[:4]:
        desired = stay_return_date(option, stay_nights)
        if desired and desired not in desired_dates:
            desired_dates.append(desired)

    provisional_return = (date.fromisoformat(departure_date) + timedelta(days=stay_nights)).isoformat()
    if not desired_dates:
        desired_dates.append(provisional_return)

    searched_return_dates = desired_dates[:MAX_RETURN_DATE_PROBES]
    roundtrip_results = await asyncio.gather(*(search(value) for value in searched_return_dates))

    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for result in roundtrip_results:
        for option in result.get("flight_options") or []:
            desired = stay_return_date(option, stay_nights)
            actual_return = return_departure_date(option)
            if desired and actual_return and desired != actual_return:
                continue
            key = (
                ((option.get("outbound") or {}).get("departure") if isinstance(option.get("outbound"), dict) else None),
                ((option.get("return") or {}).get("departure") if isinstance(option.get("return"), dict) else None),
                as_float(option.get("price")),
                option.get("provider"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(option)

    merged.sort(key=lambda item: as_float(item.get("price")) or 1_000_000)
    base = roundtrip_results[0] if roundtrip_results else outbound_only
    return {
        **base,
        "status": "ok" if merged else base.get("status"),
        "flight_options": merged[:10],
        "result_count": len(merged[:10]),
        "stay_nights": stay_nights,
        "duration_derived_return": True,
        "outbound_discovery_count": len(outbound_only.get("flight_options") or []),
        "searched_return_dates": searched_return_dates,
    }


async def complete_trip(request: TripRequest) -> dict[str, Any]:
    departure_date = request.departure_date
    needs_return = request.journey_type == "round_trip"
    stay_nights = request.stay_nights if needs_return else 0
    explicit_return_date = request.return_date if needs_return and request.return_mode == "date" else None
    duration_derived_return = needs_return and request.return_mode == "duration"

    origin_codes, origin_airport_source = resolve_origin_airports(request.origin, request.origin_airports)
    destination_iata, destination_airport_source = resolve_destination_airport(
        request.destination, request.destination_airport
    )
    airport_resolution_source = {
        "origin": origin_airport_source,
        "destination": destination_airport_source,
    }

    wants_hotel = request.include_hotel and needs_return
    hotel_min_stars = request.hotel_min_stars
    hotel_property_type = request.hotel_property_type
    destination_city = request.destination or (AIRPORT_CITY_NAMES.get(destination_iata or "") if destination_iata else None)
    hotel_location = destination_city

    wants_feeder = request.include_feeder
    feeder_origin = request.origin if wants_feeder else None
    trusted_split_candidates = list(request.feeder_split_candidates)
    feeder_dticket_mode = "include" if request.deutschlandticket else "exclude"
    feeder_dticket_mode_source = "structured_input"
    feeder_deutschlandticket_available = request.deutschlandticket

    missing: list[str] = []
    if not origin_codes:
        missing.append("origin_airports")
    if not destination_iata:
        missing.append("destination_airport")
    if wants_hotel and not hotel_location:
        missing.append("hotel_location")
    if wants_feeder and not feeder_origin:
        missing.append("origin")
    if missing:
        return {
            "status": "missing_fields",
            "search_mode": "trip_plan",
            "current_date": today_iso(),
            "missing_fields": missing,
            "error": (
                "Die Flughäfen konnten nicht vollständig deterministisch aufgelöst werden. "
                "Bitte in den erweiterten Optionen einen IATA-Code eintragen."
            ),
        }

    logger.info("trip stage=flight_search airports=%d", len(origin_codes))
    flight_tasks = {
        code: asyncio.create_task(_flight_result_for_stay(
            code,
            destination_iata,
            departure_date,
            request,
            explicit_return_date=explicit_return_date,
            stay_nights=stay_nights if duration_derived_return else None,
        ))
        for code in origin_codes
    }
    flight_results = {code: await task for code, task in flight_tasks.items()}

    candidates: list[dict[str, Any]] = []
    for code, result in flight_results.items():
        all_options = result.get("flight_options") or []
        options = (_feeder_probe_options(all_options, travel_date=departure_date, departure_after=request.departure_after, buffer_minutes=request.airport_buffer_minutes, limit=6) if feeder_origin else all_options[: max(3, min(request.max_results, 4))])
        airport_station, airport_station_source = resolve_feeder_airport_station(
            code,
            request.feeder_airport_station,
        )

        async def probe_option(option: dict[str, Any]) -> dict[str, Any]:
            outbound_departure, _, _, return_arrival = _flight_times(option)
            candidate_return_date = return_departure_date(option) or explicit_return_date
            outbound_probe = {"compatible": True, "price": 0.0}
            return_probe = {"compatible": True, "price": 0.0}
            if feeder_origin:
                tasks = [asyncio.create_task(_probe_outbound_feeder(
                    feeder_origin,
                    airport_station,
                    departure_date,
                    request.departure_after,
                    outbound_departure,
                    request.airport_buffer_minutes,
                    deutschlandticket_available=feeder_deutschlandticket_available,
                ))]
                if needs_return and candidate_return_date:
                    tasks.append(asyncio.create_task(_probe_return_feeder(
                        airport_station,
                        feeder_origin,
                        candidate_return_date,
                        return_arrival,
                        request.return_airport_buffer_minutes,
                        deutschlandticket_available=feeder_deutschlandticket_available,
                    )))
                values = await asyncio.gather(*tasks)
                outbound_probe = values[0]
                if len(values) > 1:
                    return_probe = values[1]
            return {
                "flight": option,
                "return_date": candidate_return_date,
                "outbound_probe": outbound_probe,
                "return_probe": return_probe,
                "timing": {
                    "outbound_departure": outbound_departure.isoformat() if outbound_departure else None,
                    "outbound_arrival_provider_local": ((option.get("outbound") or {}).get("arrival") if isinstance(option.get("outbound"), dict) else None),
                    "return_departure_provider_local": ((option.get("return") or {}).get("departure") if isinstance(option.get("return"), dict) else None),
                    "return_arrival": return_arrival.isoformat() if return_arrival else None,
                },
            }

        logger.info("trip stage=feeder_probe airport=%s options=%d", code, len(options))
        probes = list(await asyncio.gather(*(probe_option(option) for option in options))) if options else []
        probes.sort(key=lambda item: _flight_probe_score(item, needs_return))
        full_candidates = probes[:MAX_FLIGHT_PACKAGES]

        async def optimize_probe(probe: dict[str, Any]) -> dict[str, Any]:
            option = probe["flight"]
            candidate_return_date = probe.get("return_date")
            outbound_departure, _, _, return_arrival = _flight_times(option)
            outbound_feeder = None
            return_feeder = None
            tasks: list[asyncio.Task] = []
            labels: list[str] = []
            if feeder_origin:
                tasks.append(asyncio.create_task(feeder_outbound(
                    feeder_origin, code, airport_station, departure_date, request.departure_after,
                    outbound_departure, request.airport_buffer_minutes,
                    preference=request.effective_feeder_preference,
                    deutschlandticket_available=feeder_deutschlandticket_available,
                    split_candidates=trusted_split_candidates,
                    transfer_minutes=request.feeder_transfer_minutes,
                    split_ticket_check=request.split_ticket_check,
                    include_flixbus=request.include_flixbus,
                    include_flixtrain=request.include_flixtrain,
                )))
                labels.append("out")
            if feeder_origin and needs_return and candidate_return_date:
                tasks.append(asyncio.create_task(feeder_return(
                    code, airport_station, feeder_origin, candidate_return_date, return_arrival,
                    request.return_airport_buffer_minutes,
                    preference=request.effective_feeder_preference,
                    deutschlandticket_available=feeder_deutschlandticket_available,
                    split_candidates=trusted_split_candidates,
                    transfer_minutes=request.feeder_transfer_minutes,
                    split_ticket_check=request.split_ticket_check,
                    include_flixbus=request.include_flixbus,
                    include_flixtrain=request.include_flixtrain,
                )))
                labels.append("back")
            if tasks:
                values = await asyncio.gather(*tasks)
                for label, value in zip(labels, values):
                    if label == "out": outbound_feeder = value
                    else: return_feeder = value
            flight_price = as_float(option.get("price"))
            out_price = _selected_price(outbound_feeder)
            back_price = _selected_price(return_feeder)
            missing_feeders = int(bool(feeder_origin and not _feeder_price_known(outbound_feeder)))
            if feeder_origin and needs_return and not _feeder_price_known(return_feeder):
                missing_feeders += 1
            return {
                "flight": option,
                "return_date": candidate_return_date,
                "outbound_feeder": outbound_feeder,
                "return_feeder": return_feeder,
                "timing": probe["timing"],
                "known_transport_price": round(flight_price + out_price + back_price, 2),
                "missing_feeder_components": missing_feeders,
            }

        logger.info("trip stage=feeder_optimize airport=%s candidates=%d", code, len(full_candidates))
        optimized_packages = list(await asyncio.gather(*(optimize_probe(item) for item in full_candidates))) if full_candidates else []

        optimized_packages.sort(key=lambda item: (
            item.get("missing_feeder_components", 0),
            as_float(item.get("known_transport_price")) or 1_000_000,
        ))

        async def enrich_package(package: dict[str, Any]) -> dict[str, Any]:
            option = package.get("flight") or {}
            target_arrival_local = flight_local_value(option, "outbound", "arrival")
            target_return_departure_local = flight_local_value(option, "return", "departure")
            candidate_return_date = package.get("return_date") or explicit_return_date

            candidate_checkin = (
                target_arrival_local.date().isoformat() if target_arrival_local else departure_date
            )
            if duration_derived_return and stay_nights:
                candidate_checkout = (date.fromisoformat(candidate_checkin) + timedelta(days=stay_nights)).isoformat()
            elif target_return_departure_local:
                candidate_checkout = target_return_departure_local.date().isoformat()
            else:
                candidate_checkout = candidate_return_date

            tasks: dict[str, asyncio.Task] = {}
            if request.include_destination_transfer and destination_city and target_arrival_local:
                tasks["outbound_transfer"] = asyncio.create_task(airport_transfer_search(
                    destination_iata,
                    destination_city,
                    target_arrival_local.date().isoformat(),
                    arrival_after=target_arrival_local.strftime("%H:%M"),
                    max_results=request.max_results,
                ))
            if request.include_destination_transfer and destination_city and target_return_departure_local:
                transfer_date, arrive_before = local_cutoff(
                    target_return_departure_local,
                    request.destination_airport_buffer_minutes,
                )
                if transfer_date and arrive_before:
                    tasks["return_transfer"] = asyncio.create_task(return_transfer_search(
                        destination_city,
                        destination_iata,
                        transfer_date,
                        arrive_before=arrive_before,
                        max_results=request.max_results,
                    ))
            if wants_hotel and hotel_location and candidate_checkin and candidate_checkout:
                tasks["hotel"] = asyncio.create_task(hotel_search(HotelRequest(
                    location=hotel_location,
                    checkin_date=candidate_checkin,
                    checkout_date=candidate_checkout,
                    adults=request.adults,
                    min_rating=request.hotel_min_rating,
                    max_nightly_price=request.hotel_max_nightly_price,
                    min_stars=hotel_min_stars,
                    property_type=hotel_property_type,
                    # TripRequest erlaubt bis zu 48 Reiseoptionen, HotelRequest nur 10.
                    # Ohne Begrenzung wirft pydantic hier einen ValidationError und die
                    # gesamte Suche endet in HTTP 500 (die UI sendet fest max_results=24).
                    max_results=min(request.max_results, 10),
                )))

            resolved: dict[str, Any] = {}
            if tasks:
                values = await asyncio.gather(*tasks.values())
                resolved = dict(zip(tasks.keys(), values))

            enriched = {
                **package,
                "hotel_checkin_date": candidate_checkin if wants_hotel else None,
                "hotel_checkout_date": candidate_checkout if wants_hotel else None,
                "outbound_destination_transfer": resolved.get("outbound_transfer"),
                "return_destination_transfer": resolved.get("return_transfer"),
                "hotel": resolved.get("hotel"),
            }
            total_candidate = {
                "selected_flight": option,
                "flight_options": [option],
                "outbound_feeder": package.get("outbound_feeder"),
                "return_feeder": package.get("return_feeder"),
            }
            enriched["cost_summary"] = _candidate_totals(
                total_candidate,
                enriched.get("hotel"),
                enriched.get("outbound_destination_transfer"),
                enriched.get("return_destination_transfer"),
            )
            return enriched

        packages_to_enrich = optimized_packages[:MAX_FLIGHT_PACKAGES] if optimized_packages else []
        logger.info("trip stage=destination_components airport=%s packages=%d", code, len(packages_to_enrich))
        enriched_packages = await asyncio.gather(*(enrich_package(item) for item in packages_to_enrich)) if packages_to_enrich else []
        enriched_packages.sort(key=lambda item: (
            item.get("missing_feeder_components", 0),
            0 if (item.get("cost_summary") or {}).get("total_price_complete") else 1,
            as_float((item.get("cost_summary") or {}).get("known_total_price")) or 1_000_000,
        ))

        selected_package = enriched_packages[0] if enriched_packages else (optimized_packages[0] if optimized_packages else None)
        selected_flight = selected_package.get("flight") if selected_package else (options[0] if options else {})
        selected_return_date = (selected_package.get("return_date") if selected_package else None) or explicit_return_date
        selected_outbound = selected_package.get("outbound_feeder") if selected_package else None
        selected_return = selected_package.get("return_feeder") if selected_package else None
        outbound_transfer = selected_package.get("outbound_destination_transfer") if selected_package else None
        return_transfer = selected_package.get("return_destination_transfer") if selected_package else None
        hotel_result = selected_package.get("hotel") if selected_package else None
        candidate_checkin = selected_package.get("hotel_checkin_date") if selected_package else None
        candidate_checkout = selected_package.get("hotel_checkout_date") if selected_package else None

        candidate = {
            "origin_airport": code,
            "feeder_airport_station": airport_station,
            "feeder_airport_station_source": airport_station_source,
            "feeder_direct_query": {
                "origin": feeder_origin,
                "destination": airport_station,
                "forced_intermediate": None,
            } if feeder_origin else None,
            "flight_status": result.get("status"),
            "flight_options": options[:4],
            "selected_flight": selected_flight,
            "flight_manual_url": result.get("manual_booking_url"),
            "flight_provider_statuses": result.get("provider_statuses") or [],
            "flight_provider_summary": result.get("provider_summary") or {},
            "outbound_feeder": selected_outbound,
            "return_feeder": selected_return,
            "flight_packages": [
                {
                    "flight_price": as_float((package.get("flight") or {}).get("price")) or None,
                    "return_date": package.get("return_date"),
                    "known_total_price": (package.get("cost_summary") or {}).get("known_total_price"),
                    "total_price_complete": (package.get("cost_summary") or {}).get("total_price_complete"),
                    "missing_price_components": (package.get("cost_summary") or {}).get("missing_price_components"),
                    "hotel_checkin_date": package.get("hotel_checkin_date"),
                    "hotel_checkout_date": package.get("hotel_checkout_date"),
                }
                for package in enriched_packages[:MAX_FLIGHT_PACKAGES]
            ],
            "selected_flight_timing": selected_package.get("timing") if selected_package else {},
            "return_date": selected_return_date,
            "hotel_checkin_date": candidate_checkin if wants_hotel else None,
            "hotel_checkout_date": candidate_checkout if wants_hotel else None,
            "outbound_destination_transfer": outbound_transfer,
            "return_destination_transfer": return_transfer,
            "hotel": hotel_result,
        }
        candidate["cost_summary"] = (
            selected_package.get("cost_summary")
            if selected_package and isinstance(selected_package.get("cost_summary"), dict)
            else _candidate_totals(candidate, hotel_result, outbound_transfer, return_transfer)
        )
        candidates.append(candidate)

    def candidate_score(item: dict[str, Any]) -> tuple[int, int, float]:
        status_penalty = 0 if item.get("flight_options") else 1
        complete_penalty = 0 if item.get("cost_summary", {}).get("total_price_complete") else 1
        total = as_float(item.get("cost_summary", {}).get("known_total_price")) or 1_000_000
        return status_penalty, complete_penalty, total

    candidates.sort(key=candidate_score)
    recommended_candidate = candidates[0] if candidates else {}
    recommended = recommended_candidate.get("origin_airport")
    resolved_return_date = recommended_candidate.get("return_date") or explicit_return_date

    warnings = [
        "Zubringer, Flug, Transfers und Hotel sind getrennte Buchungen; Anschlussrisiken bleiben beim Reisenden.",
        "Flugzeiten von trvl werden in der lokalen Uhrzeit des jeweiligen Flughafens beibehalten und nicht pauschal nach Europe/Berlin umgerechnet.",
        "Der Hintransfer wird gegen die lokale Ankunftszeit des ausgewählten Fluges gesucht; der Rücktransfer muss vor Rückflug minus destination_airport_buffer_minutes am Flughafen ankommen.",
        "Hotel-Check-in wird ohne explizite Vorgabe aus dem lokalen Ankunftsdatum des ausgewählten Hinfluges abgeleitet.",
        "Bei einer Aufenthaltsdauer ohne explizites Rückreisedatum wird das Rückflugdatum aus lokalem Ankunftsdatum plus stay_nights abgeleitet; Nachtflüge verkürzen den Aufenthalt dadurch nicht.",
        f"Unterkünfte werden als {hotel_property_type} mit mindestens {hotel_min_stars} Sternen gesucht." if hotel_property_type == "hotel" else f"Gewählte Unterkunftsart: {hotel_property_type}.",
        "Hotelpreise werden nur dann in die Gesamtsumme eingerechnet, wenn trvl einen vollständigen Aufenthaltspreis liefert.",
        "Fehlende Teilpreise werden nicht geschätzt; known_total_price ist dann nur die Summe der tatsächlich bepreisten Komponenten.",
        "Bei feeder_deutschlandticket_mode=include bedeuten 0 EUR beim Deutschlandticket 0 EUR zusätzliche Ticketkosten bei bereits vorhandenem Deutschlandticket.",
    ]
    if explicit_return_date and stay_nights:
        warnings.append("Ein explizites Rückreisedatum hat Vorrang vor der zusätzlich genannten Aufenthaltsdauer.")

    return {
        "status": "ok" if any(candidate.get("flight_options") for candidate in candidates) else "partial",
        "search_mode": "trip_plan",
        "current_date": today_iso(),
        "trip_chain": (
            (["outbound_db_feeder"] if feeder_origin else [])
            + ["outbound_flight"]
            + (["outbound_destination_transfer"] if request.include_destination_transfer and destination_city else [])
            + (["hotel"] if wants_hotel else [])
            + (
                ((["return_destination_transfer"] if request.include_destination_transfer and destination_city else [])
                 + ["return_flight"]
                 + (["return_db_feeder"] if feeder_origin else []))
                if needs_return else []
            )
        ),
        "departure_date": departure_date,
        "return_date": resolved_return_date,
        "return_date_source": "explicit" if explicit_return_date else "stay_duration" if duration_derived_return and needs_return else None,
        "return_date_input_source": "structured_input" if explicit_return_date else None,
        "requested_stay_nights": stay_nights,
        "journey_type": request.journey_type,
        "stay_duration_source": "structured_input" if stay_nights else None,
        "destination_iata": destination_iata,
        "destination_city": destination_city,
        "airport_resolution_source": airport_resolution_source,
        "recommended_origin_airport": recommended,
        "airport_buffer_minutes": request.airport_buffer_minutes,
        "destination_airport_buffer_minutes": request.destination_airport_buffer_minutes,
        "return_airport_buffer_minutes": request.return_airport_buffer_minutes,
        "feeder_deutschlandticket_mode": feeder_dticket_mode,
        "feeder_deutschlandticket_mode_source": feeder_dticket_mode_source,
        "feeder_cost_basis": (
            "additional_ticket_cost_with_existing_deutschlandticket"
            if feeder_dticket_mode == "include" else "paid_ticket_cost"
        ),
        "airport_candidates": candidates,
        "outbound_destination_transfer": recommended_candidate.get("outbound_destination_transfer"),
        "hotel": recommended_candidate.get("hotel"),
        "return_destination_transfer": recommended_candidate.get("return_destination_transfer"),
        "warnings": warnings,
    }


