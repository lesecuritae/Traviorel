"""Regressionen aus dem Ultrareview von compare.py/presentation.py/mcp_server.py/planner.py."""
from __future__ import annotations

import asyncio

import pytest

from reisevergleich import compare, mcp_server, planner, presentation
from reisevergleich.models import ReiseRequest, StationSelection, TripRequest

DATE = "2030-10-27"


def _route(identifier: str, time: str, *, source: str, price: float | None = None, transfers: int = 0) -> dict:
    route = {
        "id": identifier, "provider": source, "provider_code": source, "db_source": source,
        "type": "train", "origin": "Leipzig Hbf", "destination": "Frankfurt(Main) Hbf",
        "departure": f"{DATE}T{time}:00+01:00", "arrival": f"{DATE}T09:00:00+01:00",
        "duration_minutes": 180, "transfers": transfers,
        "legs": [{"mode": "REGIONAL_RAIL", "line": "RE 1", "origin": {"name": "Leipzig Hbf"}, "destination": {"name": "Frankfurt(Main) Hbf"}}],
    }
    if price is not None:
        route["price"] = price
    return route


def _flix_route(identifier: str, time: str, *, kind: str = "bus") -> dict:
    return {
        "id": identifier, "flix_kind": kind, "type": kind,
        "departure": {"station": "Leipzig Hbf", "time": f"{DATE}T{time}:00+01:00"},
        "arrival": {"station": "Frankfurt(Main) Hbf", "time": f"{DATE}T09:00:00+01:00"},
        "duration_minutes": 180, "price": 9.99, "currency": "EUR", "legs": [],
    }


def _station(name: str, provider_id: str) -> StationSelection:
    return StationSelection(name=name, provider="db", provider_id=provider_id,
                             provider_ids={"db": provider_id, "transitous": f"{provider_id}-transitous"})


def test_flix_options_respect_the_same_filters_as_every_other_route_list(monkeypatch):
    # Issue: flix_options was built from the raw, unfiltered flix_result["routes"], skipping the
    # transport-mode/include_bus/departure-window filters every other list already applies.
    async def fake_db(**kwargs):
        return ({"journeys": [], "source": "dbnav"}, [])

    async def fake_transitous(request):
        return {"routes": [], "diagnostic": {"ok": True}}

    in_window = _flix_route("in-window", "03:00")
    out_of_window = _flix_route("too-early", "00:10")

    async def fake_flix(request):
        return {"routes": [in_window, out_of_window], "candidate_routes": [in_window, out_of_window], "provider_status": {"ok": True}}

    async def identity(routes):
        return routes

    async def complete_passthrough(routes, request):
        return routes

    monkeypatch.setattr(compare, "db_search_with_retry", fake_db)
    monkeypatch.setattr(compare, "transitous_search", fake_transitous)
    monkeypatch.setattr(compare, "flix_search", fake_flix)
    monkeypatch.setattr(compare, "_enrich_history_bounded", identity)
    monkeypatch.setattr(compare, "complete_flix_routes", complete_passthrough)
    request = ReiseRequest(origin="Leipzig Hbf", destination="Frankfurt(Main) Hbf", travel_date=DATE,
                            departure_after="03:00", split_ticket_check=False)
    result = asyncio.run(compare.compare_trip(request))
    flix_ids = [item["id"] for item in result["flix_options"]]
    assert flix_ids == ["in-window"], result["flix_options"]


def test_cheapest_with_live_price_is_actually_the_cheapest_priced_route(monkeypatch):
    # Issue: cheapest_with_live_price was priced[0] (top of the preference-ranked list), not the
    # minimum-price route - with preference="fastest" a pricier-but-faster route was labelled cheapest.
    fast_expensive = _route("fast", "03:00", source="dbnav", price=120.0)
    slow_cheap = _route("slow", "03:30", source="dbnav", price=19.99)
    slow_cheap["duration_minutes"] = 400

    async def fake_db(**kwargs):
        return ({"journeys": [fast_expensive, slow_cheap], "source": "dbnav"}, [])

    async def fake_transitous(request):
        return {"routes": [], "diagnostic": {"ok": True}}

    async def fake_flix(request):
        return {"routes": [], "candidate_routes": [], "provider_status": {"ok": True}}

    async def identity(routes):
        return routes

    monkeypatch.setattr(compare, "db_search_with_retry", fake_db)
    monkeypatch.setattr(compare, "transitous_search", fake_transitous)
    monkeypatch.setattr(compare, "flix_search", fake_flix)
    monkeypatch.setattr(compare, "_enrich_history_bounded", identity)
    request = ReiseRequest(origin="Leipzig Hbf", destination="Frankfurt(Main) Hbf", travel_date=DATE,
                            departure_after="03:00", preference="fastest", split_ticket_check=False)
    result = asyncio.run(compare.compare_trip(request))
    assert result["recommendation"]["fastest"]["id"] == "fast"
    assert result["recommendation"]["cheapest_with_live_price"]["id"] == "slow", result["recommendation"]


def test_optional_flix_and_mixed_ticket_enrichment_failures_do_not_fail_the_search(monkeypatch):
    # Issue: complete_flix_routes/ground_mixed_options were awaited with no exception isolation;
    # a failure there used to take down the whole comparison instead of just losing that extra.
    flix_route = _flix_route("flix-1", "03:00")

    async def fake_db(**kwargs):
        return ({"journeys": [], "source": "dbnav"}, [])

    async def fake_transitous(request):
        return {"routes": [], "diagnostic": {"ok": True}}

    async def fake_flix(request):
        return {"routes": [flix_route], "candidate_routes": [flix_route], "provider_status": {"ok": True}}

    async def boom(routes, request):
        raise RuntimeError("flix enrichment backend unavailable")

    async def identity(routes):
        return routes

    monkeypatch.setattr(compare, "db_search_with_retry", fake_db)
    monkeypatch.setattr(compare, "transitous_search", fake_transitous)
    monkeypatch.setattr(compare, "flix_search", fake_flix)
    monkeypatch.setattr(compare, "_enrich_history_bounded", identity)
    monkeypatch.setattr(compare, "complete_flix_routes", boom)
    request = ReiseRequest(origin="Leipzig Hbf", destination="Frankfurt(Main) Hbf", travel_date=DATE,
                            departure_after="03:00", split_ticket_check=False)
    result = asyncio.run(compare.compare_trip(request))
    assert result["flix_options"], "flix routes should survive a failed enrichment step"


def test_trip_context_keeps_the_priced_flight_when_no_option_matches_the_airport_exactly():
    # Issue: _route_complete required an exact airport match; when the provider reports a
    # different-but-compatible airport (e.g. a metro/city code), the public flight became {}
    # while cost_summary still counted its price - public output said "no flight", total said otherwise.
    mismatched_flight = {
        "price": 199.0, "currency": "EUR", "provider": "test",
        "outbound": {"departure_airport": "LON", "arrival_airport": "FCO", "departure": "2030-09-15T13:45", "arrival": "2030-09-15T16:05", "stops": 0},
        "return": {"departure_airport": "FCO", "arrival_airport": "LON", "departure": "2030-09-22T18:00", "arrival": "2030-09-22T20:20", "stops": 0},
    }
    raw = {
        "status": "ok", "search_mode": "trip_plan", "recommended_origin_airport": "LHR", "destination_iata": "ROM",
        "destination_city": "Rom", "airport_candidates": [{
            "origin_airport": "LHR", "selected_flight": mismatched_flight, "flight_options": [mismatched_flight],
        }],
    }
    ctx = presentation._trip_context(raw)
    assert ctx["flight"]["price"]["value"] == 199.0, ctx["flight"]
    assert ctx["flight"]["outbound"]["from"] == "LON", ctx["flight"]


def test_public_result_passes_missing_fields_through_for_ground_trip_and_trip_plan():
    # Issue: public_result dropped missing_fields for both branches, so a client could see
    # status="missing_fields" with no way to tell which field needs to be supplied.
    ground = presentation.public_result({
        "status": "missing_fields", "search_mode": "ground_trip",
        "missing_fields": ["return_date_or_stay_duration"],
    })
    assert ground["missing_fields"] == ["return_date_or_stay_duration"], ground
    trip = presentation.public_result({
        "status": "missing_fields", "search_mode": "trip_plan",
        "missing_fields": ["destination_airport"],
    })
    assert trip["missing_fields"] == ["destination_airport"], trip


def test_flight_price_currency_is_not_silently_defaulted_to_eur():
    # Issue: _flight replaced a missing currency with "EUR", contradicting
    # planner._candidate_totals, which already marks a missing currency as flight_currency_unknown.
    assert presentation._flight({"price": 100, "currency": "USD", "booking_url": "x"})["price"]["currency"] == "USD"
    assert "currency" not in presentation._flight({"price": 100, "booking_url": "x"})["price"]


def test_plan_flight_trip_shows_the_real_currency_and_does_not_mislabel_a_nightly_price(monkeypatch):
    # Issue: plan_flight_trip always wrote "€" (ignoring price.currency) and always labelled the
    # hotel price "gesamt" (whole stay) even for a basis of "nightly_unverified".
    async def fake_search(request):
        return {
            "response_context": {
                "route": {"origin": "Leipzig", "destination": "Rom", "outbound_date": "2030-09-15", "origin_airport": "LEJ", "destination_airport": "FCO"},
                "flight": {"price": {"value": 149.5, "currency": "USD"}, "provider": "test", "outbound": {"departure": "2030-09-15T06:00", "arrival": "2030-09-15T08:00", "stops": 0}, "offer_url": "x"},
                "hotel": {"options": [{"name": "Nightly Hotel", "stars": 3, "rating": 8.0, "price": {"value": 89.0, "currency": "EUR", "basis": "nightly_unverified"}}]},
                "cost_summary": {"complete": False, "missing_components": ["flight_currency_conversion"]},
            },
        }
    monkeypatch.setattr(mcp_server, "search", fake_search)
    reply = asyncio.run(mcp_server.plan_flight_trip("Leipzig", "Rom", "2030-09-15"))
    text = reply.content[0].text
    assert "149.50 USD" in text, text
    assert "€" not in text.split("Flug:")[1].split("\n")[0]
    hotel_line = next(line for line in text.splitlines() if line.startswith("Hotel:"))
    assert "gesamt" not in hotel_line, hotel_line
    assert "pro Nacht" in hotel_line, hotel_line


def test_live_delays_distinguishes_missing_realtime_data_from_actually_on_time(monkeypatch):
    # Issue: fmt(None) returned "pünktlich" (on time), the same as fmt(0) - a leg with real-time
    # data for only one end (departure delay known, arrival delay missing, or vice versa) was
    # reported as arriving on time with no real-time arrival data at all.
    connection = {
        "provider": "Deutsche Bahn", "origin": "Leipzig Hbf", "destination": "Frankfurt(Main) Hbf",
        "departure": "2030-09-15T06:00:00+01:00",
        "legs": [{
            "mode": "train", "line_name": "RE 1", "origin": "Leipzig Hbf", "destination": "Frankfurt(Main) Hbf",
            "departure": "2030-09-15T06:00:00+01:00", "arrival": "2030-09-15T09:00:00+01:00",
            "departure_delay_minutes": 3, "arrival_delay_minutes": None, "train_number": "RE1",
        }],
    }

    async def fake_search(request):
        return {"connections": [connection]}
    monkeypatch.setattr(mcp_server, "search", fake_search)
    ref = mcp_server._remember([connection])[0]
    reply = asyncio.run(mcp_server.live_delays(ref))
    text = reply.content[0].text
    arrival_line = next(line for line in text.splitlines() if "Ankunft" in line)
    assert "keine Angabe" in arrival_line, arrival_line
    assert "pünktlich" not in arrival_line.split("Ankunft")[1], arrival_line


def test_deutschlandticket_honours_an_explicit_station_selection(monkeypatch):
    # Issue: deutschlandticket() always searched DB/Transitous by free-text name, ignoring an
    # explicit find_station selection the caller supplied - unlike compare_trip, which searches
    # by the selected stop's own id and skips a provider that has none for it.
    seen = {}

    async def fake_db(**kwargs):
        seen["db_ids"] = (kwargs.get("origin_id"), kwargs.get("destination_id"))
        return ({"journeys": [], "source": "dbnav"}, [])

    async def fake_transitous(request, **kwargs):
        seen["transitous_station"] = (request.origin_station, request.destination_station)
        return {"routes": [], "diagnostic": {"ok": True}}

    monkeypatch.setattr(compare, "db_search_with_retry", fake_db)
    monkeypatch.setattr(compare, "transitous_search", fake_transitous)
    origin_station = _station("Leipzig Hbf", "8010205")
    destination_station = _station("Frankfurt(Main) Hbf", "8000105")
    from reisevergleich.models import DeutschlandticketRequest
    request = DeutschlandticketRequest(
        origin="Leipzig Hbf", destination="Frankfurt(Main) Hbf", travel_date=DATE,
        origin_station=origin_station, destination_station=destination_station,
    )
    asyncio.run(compare.deutschlandticket(request))
    assert seen["db_ids"] == ("8010205", "8000105"), seen
    assert seen["transitous_station"] == (origin_station, destination_station), seen


def test_deutschlandticket_skips_db_when_the_selected_station_has_no_db_id(monkeypatch):
    called = {"db": False}

    async def fake_db(**kwargs):
        called["db"] = True
        return ({"journeys": [], "source": "dbnav"}, [])

    async def fake_transitous(request, **kwargs):
        return {"routes": [], "diagnostic": {"ok": True}}

    monkeypatch.setattr(compare, "db_search_with_retry", fake_db)
    monkeypatch.setattr(compare, "transitous_search", fake_transitous)
    bus_only_station = StationSelection(name="Leipzig, Bushalt", provider="flix", provider_id="leipzig-flix")
    from reisevergleich.models import DeutschlandticketRequest
    request = DeutschlandticketRequest(
        origin="Leipzig, Bushalt", destination="Frankfurt(Main) Hbf", travel_date=DATE,
        origin_station=bus_only_station, destination_station=_station("Frankfurt(Main) Hbf", "8000105"),
    )
    result = asyncio.run(compare.deutschlandticket(request))
    assert called["db"] is False, "DB must be skipped, not searched by the free-text name, when the selection has no DB id"
    assert result["status"] == "empty"


def test_complete_trip_survives_a_failed_optional_hotel_search(monkeypatch):
    # Issue: complete_trip awaited the hotel/transfer tasks with no per-component exception
    # isolation; one optional provider raising aborted the whole trip plan instead of marking
    # just that component as missing.
    flight = {
        "price": 149.5, "currency": "EUR", "provider": "regression-test",
        "outbound": {"departure_airport": "FRA", "arrival_airport": "CDG", "departure": "2030-09-15T13:45:00", "arrival": "2030-09-15T16:05:00", "stops": 0},
        "return": {"departure_airport": "CDG", "arrival_airport": "FRA", "departure": "2030-09-22T18:00:00", "arrival": "2030-09-22T20:20:00", "stops": 0},
    }

    async def fake_flight_result_for_stay(*args, **kwargs):
        return {"status": "ok", "flight_options": [flight], "result_count": 1,
                "provider_statuses": [{"provider": "regression-test", "status": "ok", "results": 1}], "provider_summary": {"ok": 1}}

    async def fake_hotel_search(request):
        raise RuntimeError("hotel provider unavailable")

    original_flight = planner._flight_result_for_stay
    original_hotel = planner.hotel_search
    try:
        planner._flight_result_for_stay = fake_flight_result_for_stay
        planner.hotel_search = fake_hotel_search
        request = TripRequest(
            travel_mode="flight", origin="Frankfurt (Main) Hbf", destination="Paris", departure_date="2030-09-15",
            return_mode="duration", duration_value=7, duration_unit="nights", deutschlandticket=True,
            include_feeder=False, origin_airports=["FRA"], destination_airport="CDG", include_hotel=True,
            hotel_property_type="hotel", hotel_min_stars=3, include_destination_transfer=False,
            max_results=10, refresh_cache=True,
        )
        result = asyncio.run(planner.complete_trip(request))
    finally:
        planner._flight_result_for_stay = original_flight
        planner.hotel_search = original_hotel

    assert result["status"] == "ok", result
    candidate = result["airport_candidates"][0]
    assert candidate["hotel"] is None, candidate
    assert candidate["selected_flight"]["outbound"]["arrival_airport"] == "CDG", candidate


def test_search_ground_forwards_prefer_and_limit_into_the_backend_selection(monkeypatch):
    # Issue: prefer/limit only re-sorted and truncated whatever the backend's own defaults
    # returned; a cheaper/fewer-transfer connection the default ranking had already cut never
    # reached this tool at all.
    captured = {}

    async def fake_search(request):
        captured["feeder_preference"] = request.feeder_preference
        captured["max_results"] = request.max_results
        return {"status": "empty", "journeys": []}
    monkeypatch.setattr(mcp_server, "search", fake_search)
    asyncio.run(mcp_server.search_ground("Leipzig Hbf", "Frankfurt(Main) Hbf", date="2030-09-15", departure_after="06:00", prefer="fewest_transfers", limit=15))
    assert captured == {"feeder_preference": "fewest_transfers", "max_results": 15}, captured
