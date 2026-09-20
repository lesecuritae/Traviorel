import asyncio

import os

# Diese Tests prüfen den trvl-Weg (und den Rückfall darauf); die eigenen Direktwege sind in test_flix_api.py und
# test_skiplagged_fx.py abgedeckt und dürfen hier nicht ins Netz gehen.
for _name in ("FLIX_NATIVE", "FLIGHT_NATIVE", "HOTEL_NATIVE"):
    os.environ[_name] = "0"

from reisevergleich.models import TripRequest
import reisevergleich.planner as planner

flight = {
    "price": 149.50,
    "currency": "USD",
    "provider": "regression-test",
    "outbound": {
        "departure_airport": "FRA",
        "arrival_airport": "CDG",
        "departure": "2030-09-15T13:45:00",
        "arrival": "2030-09-15T16:05:00",
        "stops": 0,
    },
    "return": {
        "departure_airport": "CDG",
        "arrival_airport": "FRA",
        "departure": "2030-09-22T18:00:00",
        "arrival": "2030-09-22T20:20:00",
        "stops": 0,
    },
}

hotel_requests = []

async def fake_flight_result_for_stay(*args, **kwargs):
    return {
        "status": "ok",
        "flight_options": [flight],
        "result_count": 1,
        "provider_statuses": [{"provider": "regression-test", "status": "ok", "results": 1}],
        "provider_summary": {"ok": 1},
    }

async def fake_hotel_search(request):
    hotel_requests.append(request)
    return {
        "status": "ok",
        "hotel_options": [{
            "name": "Regression Hotel",
            "stars": 3,
            "verified_total_price": 700.0,
            "currency": "EUR",
        }],
        "result_count": 1,
    }

async def main():
    original_flight = planner._flight_result_for_stay
    original_hotel = planner.hotel_search
    try:
        planner._flight_result_for_stay = fake_flight_result_for_stay
        planner.hotel_search = fake_hotel_search

        results = []
        for max_results in (10, 24, 48):
            request = TripRequest(
                travel_mode="flight",
                origin="Frankfurt (Main) Hbf",
                destination="Paris",
                departure_date="2030-09-15",
                return_mode="duration",
                duration_value=7,
                duration_unit="nights",
                deutschlandticket=True,
                include_feeder=False,
                origin_airports=["FRA"],
                destination_airport="CDG",
                include_hotel=True,
                hotel_property_type="hotel",
                hotel_min_stars=3,
                include_destination_transfer=False,
                max_results=max_results,
                refresh_cache=True,
            )
            results.append(await planner.complete_trip(request))
    finally:
        planner._flight_result_for_stay = original_flight
        planner.hotel_search = original_hotel

    assert [request.max_results for request in hotel_requests] == [10, 10, 10]
    result = results[-1]
    assert all(item["status"] == "ok" for item in results), results
    assert result["recommended_origin_airport"] == "FRA", result
    candidate = result["airport_candidates"][0]
    summary = candidate["cost_summary"]
    assert "flight" not in summary["priced_components"], summary
    assert "flight_currency_conversion" in summary["missing_price_components"], summary
    assert summary["known_total_price"] == 700.0, summary
    assert candidate["hotel_checkin_date"] == "2030-09-15", candidate
    assert candidate["hotel_checkout_date"] == "2030-09-22", candidate
    assert hotel_requests, "hotel_search wurde nicht erreicht"
    assert hotel_requests[0].checkin_date == "2030-09-15"
    assert hotel_requests[0].checkout_date == "2030-09-22"
    assert candidate["selected_flight"]["outbound"]["arrival_airport"] == "CDG"
    assert candidate["selected_flight"]["return"]["arrival_airport"] == "FRA"

asyncio.run(main())
print("Kompletter Flugpaket-/Hotel-Enrichment-Pfad: OK")
