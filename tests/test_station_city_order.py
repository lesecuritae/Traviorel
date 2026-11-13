"""A city suffix must not hide a railway stop behind a nearby tram stop."""
import asyncio

from reisevergleich import station_catalog


def test_city_suffix_finds_canonical_station_without_merging_nearby_tram(monkeypatch):
    calls = []
    rail = {"provider": "db", "provider_id": "8013070", "name": "Leipzig Allee-Center",
            "latitude": 51.32, "longitude": 12.29, "is_station": True}
    tram = {"provider": "db", "provider_id": "206601", "name": "Allee-Center Bahnhof Lützner Str., Leipzig",
            "latitude": 51.321, "longitude": 12.291, "is_station": True}
    async def db(query):
        calls.append(query)
        return [rail, tram] if query == "Leipzig Allee-Center" else [tram]
    async def transitous(query):
        item = rail if query == "Leipzig Allee-Center" else tram
        return [{**item, "provider": "transitous", "provider_id": "transit:" + item["provider_id"], "region": "Leipzig"}]
    async def empty(query):
        return []
    monkeypatch.setattr(station_catalog, "_db_locations", db)
    monkeypatch.setattr(station_catalog, "_transitous_locations", transitous)
    monkeypatch.setattr(station_catalog, "_flix_locations", empty)
    result = asyncio.run(station_catalog._search_uncached("Allee-Center Leipzig", 8))
    assert calls == ["Allee-Center Leipzig", "Leipzig Allee-Center"]
    assert result["stations"][0]["name"] == rail["name"]
    assert len(result["stations"]) == 2
    assert result["stations"][0]["provider_ids"] == {"db": "8013070", "transitous": "transit:8013070"}
    assert result["stations"][0]["provider_alias_ids"]["db"] == ["8013070"]
    assert result["stations"][1]["provider_ids"]["db"] == "206601"


def test_word_order_does_not_outrank_full_identity_with_similar_stop():
    score = station_catalog._score
    assert score("Allee-Center Leipzig", "Leipzig Allee-Center", "db") > score(
        "Allee-Center Leipzig", "Allee-Center Bahnhof Lützner Str., Leipzig", "db")


def test_city_reordering_uses_only_matching_provider_region_and_one_extra_query():
    helper = station_catalog._city_first_queries
    assert helper("Allee-Center Leipzig", [{"region": "Hamm"}], []) == []
    assert helper("Allee-Center Leipzig", [{"region": "Leipzig"}] * 10, []) == ["Leipzig Allee-Center"]
    assert helper("Leipzig Allee-Center", [{"region": "Leipzig"}], []) == []
    assert helper("Allee-Center Leipzig", [{"region": "Leipzig"}], ["Leipzig Allee-Center"]) == []


def test_central_station_prominence_cannot_replace_a_requested_district_stop(monkeypatch):
    async def db(query):
        return [{"provider": "db", "provider_id": "main", "name": "Leipzig Hbf", "is_station": True, "modes": ["BUS"]},
                {"provider": "db", "provider_id": "district-bus", "name": "S-Bahnhof Engelsdorf, Leipzig", "is_station": True, "modes": ["BUS"]}]
    async def empty(query): return []
    monkeypatch.setattr(station_catalog, "_db_locations", db)
    monkeypatch.setattr(station_catalog, "_transitous_locations", empty)
    monkeypatch.setattr(station_catalog, "_flix_locations", empty)
    result = asyncio.run(station_catalog._search_uncached("Engelsdorf Bahnhof Leipzig", 8, "bus"))
    assert [s["name"] for s in result["stations"]] == ["S-Bahnhof Engelsdorf, Leipzig"]
    assert result["auto_selection"]["provider_id"] == "district-bus"


def test_relevance_is_generic_and_can_use_provider_region():
    helper = station_catalog._query_matches_station
    assert helper("Haltestelle West Musterstadt", {"name": "Haltestelle West", "region": "Musterstadt"})
    assert not helper("Haltestelle West Musterstadt", {"name": "Musterstadt Hbf"})


def test_source_outage_is_failed_and_must_not_be_cached_as_no_stations(monkeypatch):
    async def failed(query):
        raise RuntimeError("synthetic upstream outage")
    for name in ["_db_locations", "_transitous_locations", "_flix_locations"]:
        monkeypatch.setattr(station_catalog, name, failed)
    result = asyncio.run(station_catalog._search_uncached("Musterstadt Bahnhof", 8))
    assert result["stations"] == []
    assert result["status"] == "failed"
    assert set(result["provider_status"].values()) == {"failed"}


def test_nearby_cross_provider_stops_require_matching_names_not_only_distance(monkeypatch):
    async def db(query):
        return [
            {"provider": "db", "provider_id": "rail", "name": "Musterstadt West", "latitude": 51.0, "longitude": 12.0},
            {"provider": "db", "provider_id": "bus", "name": "S-Bahnhof West, Musterstadt", "latitude": 51.0001, "longitude": 12.0},
            {"provider": "db", "provider_id": "street", "name": "West Beispielstr., Musterstadt", "latitude": 51.0002, "longitude": 12.0},
        ]
    async def transitous(query):
        return [
            {"provider": "transitous", "provider_id": "t-rail", "name": "Musterstadt-West, Bahnhof", "modes": ["SUBURBAN"], "latitude": 51.0, "longitude": 12.0},
            {"provider": "transitous", "provider_id": "t-bus", "name": "Musterstadt, S-Bahnhof West", "modes": ["BUS"], "latitude": 51.0001, "longitude": 12.0},
        ]
    async def empty(query): return []
    monkeypatch.setattr(station_catalog, "_db_locations", db)
    monkeypatch.setattr(station_catalog, "_transitous_locations", transitous)
    monkeypatch.setattr(station_catalog, "_flix_locations", empty)
    result = asyncio.run(station_catalog._search_uncached("West Bahnhof Musterstadt", 8))
    pairs = {item["provider_ids"].get("transitous"): item["provider_ids"].get("db") for item in result["stations"]}
    assert pairs["t-rail"] == "rail"
    assert pairs["t-bus"] == "bus"
    assert any(item["provider_ids"] == {"db": "street"} for item in result["stations"])
