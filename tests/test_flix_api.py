import asyncio

from reisevergleich import flix_api

ORIGIN = {"id": "c1", "name": "Berlin", "legacy_id": 88}
DEST = {"id": "c2", "name": "Hamburg", "legacy_id": 118}


def _point(day, station, city):
    return {"date": day, "station_id": station, "city_id": city}


def _result(kind, price, dep, arr, status="available", legs=None):
    leg = lambda mode: {"means_of_transport": mode, "departure": _point(dep, "s1", "c1"), "arrival": _point(arr, "s2", "c2")}  # noqa: E731
    return {
        "status": status, "transfer_type_key": kind, "provider": "flixbus", "duration": {"hours": 3, "minutes": 15},
        "price": {"total": price}, "departure": _point(dep, "s1", "c1"), "arrival": _point(arr, "s2", "c2"),
        "legs": legs if legs is not None else [leg("train" if kind == "train" else "bus")],
    }


RESPONSE = {
    "trips": [{"results": {
        "a": _result("direct", 9.49, "2026-09-29T04:00:00+02:00", "2026-09-29T07:15:00+02:00"),
        "b": _result("train", 24.99, "2026-09-29T09:55:00+02:00", "2026-09-29T12:21:00+02:00"),
        "c": _result("direct", 12.0, "2026-09-29T10:00:00+02:00", "2026-09-29T13:00:00+02:00", status="sold_out"),
        "d": _result("direct", 0, "2026-09-29T11:00:00+02:00", "2026-09-29T14:00:00+02:00"),
        "e": _result("direct", 15.0, "2026-09-29T12:00:00+02:00", "2026-09-29T15:00:00+02:00", legs=[]),
    }}],
    "stations": {"s1": {"name": "Berlin ZOB"}, "s2": {"name": "Hamburg ZOB"}},
    "cities": [{"id": "c1", "name": "Berlin"}, {"id": "c2", "name": "Hamburg"}],
}

routes = flix_api.routes_from_response(RESPONSE, ORIGIN, DEST, "2026-09-29")
assert [(r["type"], r["price"]) for r in routes] == [("bus", 9.49), ("train", 24.99)], routes
first = routes[0]
assert first["departure"] == {"city": "Berlin", "station": "Berlin ZOB", "station_id": "s1", "time": "2026-09-29T04:00:00+02:00"}
assert first["duration_minutes"] == 195 and first["transfers"] == 0 and first["currency"] == "EUR"
assert "departureCity=88" in first["booking_url"] and "rideDate=29.09.2026" in first["booking_url"]

assert flix_api.pick_city([{"id": "1", "name": "Berlin Schönefeld"}, {"id": "2", "name": "Berlin"}], "berlin")["id"] == "2"
assert flix_api.pick_city([{"id": "1", "name": "Leipzig Süd"}], "Leipzig Hbf")["id"] == "1"
assert flix_api.pick_city([], "x") is None and flix_api.pick_city("kaputt", "x") is None

calls = []


def fake_get_json(path, params):
    calls.append(path)
    if "autocomplete" in path:
        return [ORIGIN] if params["q"] == "Berlin" else [DEST]
    return RESPONSE


flix_api._get_json = fake_get_json
flix_api._city_cache.clear()
result = asyncio.run(flix_api.search("Berlin", "Hamburg", "2026-09-29"))
assert result["ok"] and len(result["data"]["routes"]) == 2
asyncio.run(flix_api.search("Berlin", "Hamburg", "2026-09-29"))
assert calls.count("/search/autocomplete/cities") == 2, "Orte werden zwischengespeichert"


def boom(path, params):
    raise OSError("Netz weg")


flix_api._get_json = boom
flix_api._city_cache.clear()
failed = asyncio.run(flix_api.search("Berlin", "Hamburg", "2026-09-29"))
assert failed["ok"] is False and "Netz weg" in failed["error"]
print("Flix-API: Routen, Ortswahl, Cache und Fehlerfall: OK")
