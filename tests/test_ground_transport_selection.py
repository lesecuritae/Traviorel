import asyncio

import pytest

from reisevergleich import compare, models, service, transitous
from reisevergleich.ground_transport import route_matches_transport


def route(*products):
    return {"legs": [{"product": product, "mode": "train", "line": "fixture"} for product in products]}


@pytest.mark.parametrize("mode,products,expected", [
    ("suburban", ("suburban",), True), ("suburban", ("regionalExpress",), False),
    ("suburban", ("suburban", "bus"), False), ("suburban", ("tram",), False),
    ("bus", ("bus",), True), ("bus", ("bus", "regionalExpress"), False),
    ("bus", ("regionalExpress",), False),
    ("local_train", ("suburban", "regionalExpress", "regional"), True),
    ("local_train", ("nationalExpress",), False), ("local_train", ("national",), False),
    ("local_train", ("suburban", "bus"), False), ("all", ("suburban", "bus"), True),
])
def test_explicit_mode_filters_every_ridden_leg(mode, products, expected):
    assert route_matches_transport(route(*products), mode) is expected


def test_walking_is_not_a_conflicting_transport_and_unknown_rides_fail_closed():
    assert route_matches_transport({"legs": [{"mode": "WALK"}, {"mode": "SUBURBAN"}]}, "suburban")
    assert not route_matches_transport({"legs": [{"mode": "WALK"}]}, "suburban")
    assert not route_matches_transport({"legs": [{"line": "S1", "mode": "train"}]}, "suburban")


@pytest.mark.parametrize("spoken,canonical", [("S-Bahn", "suburban"), ("sbahn", "suburban"),
    ("Bus", "bus"), ("RE", "local_train"), ("RB", "local_train"), ("Zug", "local_train")])
def test_transport_aliases_are_shared_by_api_models(spoken, canonical):
    req = models.ReiseRequest(origin="Start", destination="Ziel", travel_date="2030-09-15", transport_mode=spoken)
    assert req.transport_mode == canonical


@pytest.mark.parametrize("mode", ["all", "bus", "suburban", "local_train"])
def test_same_selection_is_used_by_api_models_and_comparison(mode):
    req = models.ReiseRequest(origin="Start", destination="Ziel", travel_date="2030-09-15", transport_mode=mode)
    trip = models.TripRequest(origin="Start", destination="Ziel", departure_date="2030-09-15", transport_mode=mode,
                             travel_mode="ground", journey_type="one_way")
    assert req.transport_mode == trip.transport_mode == mode
    assert req.include_train == trip.include_train
    assert req.include_bus == trip.include_bus
    if mode == "bus":
        assert not req.include_train and req.include_bus and not req.include_flixtrain
        assert not compare._transport_selected(route("regionalExpress"), req)
    elif mode in {"suburban", "local_train"}:
        assert req.include_train and not req.include_bus
        assert not req.include_flixtrain and not req.include_flixbus


def test_transport_selection_reaches_existing_service_pipeline(monkeypatch):
    async def compare_ground_round_trip(**kwargs):
        assert kwargs["transport_mode"] == "bus"
        assert kwargs["include_train"] is False
        assert kwargs["include_bus"] is True
        return {"status": "empty"}
    monkeypatch.setattr(service, "compare_ground_round_trip", compare_ground_round_trip)
    req = models.TripRequest(origin="Start", destination="Ziel", departure_date="2030-09-15", transport_mode="bus",
                             travel_mode="ground", journey_type="one_way")
    assert asyncio.run(service._compute(req))["status"] == "empty"


@pytest.mark.parametrize("mode,accepted", [("bus", "BUS"), ("suburban", "SUBURBAN"), ("local_train", "REGIONAL_RAIL")])
def test_transitous_normalization_cannot_reintroduce_a_different_mode(mode, accepted):
    request = models.ReiseRequest(origin="Start", destination="Ziel", travel_date="2030-09-15", transport_mode=mode)
    def itinerary(leg_mode):
        return {"startTime": "2030-09-15T13:00:00+02:00", "endTime": "2030-09-15T13:30:00+02:00", "duration": 1800,
                "legs": [{"mode": leg_mode, "from": {"name": "Start"}, "to": {"name": "Ziel"}}]}
    result = transitous.normalize_routes([itinerary(accepted), itinerary("LONG_DISTANCE")], request, {"name": "Start"}, {"name": "Ziel"})
    assert len(result) == 1
    assert result[0]["legs"][0]["mode"] == accepted
