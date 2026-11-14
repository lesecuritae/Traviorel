import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from reisevergleich import mcp_server as mcp
from reisevergleich.models import StationSelection

START = StationSelection(name="Start Bahnhof", provider="db", provider_id="8000001")
END = StationSelection(name="Ziel Bahnhof", provider="db", provider_id="8000002")


def clock(monkeypatch, stamp="2030-09-15T13:00:00+02:00"):
    now = datetime.fromisoformat(stamp).astimezone(ZoneInfo("Europe/Berlin"))
    monkeypatch.setattr(mcp, "_server_now", lambda: now)


def row(direction):
    return {"line": "S1", "direction": direction, "product": "suburban", "cancelled": False,
            "planned_departure": "2030-09-15T11:10:00Z", "expected_departure": "2030-09-15T11:11:00Z"}


def test_board_without_destination_keeps_both_directions_and_local_times(monkeypatch):
    clock(monkeypatch)
    async def fetch(payload):
        assert "destination" not in payload
        assert payload["transport_mode"] == "all"
        assert payload["station_id"] == START.provider_id
        return {"status": "ok", "departures": [row("East"), row("West")]}
    monkeypatch.setattr(mcp, "db_departures", fetch)
    result = asyncio.run(mcp.next_departures(START.name, origin_station=START))
    assert not result.is_error
    assert len(result.structured_content["departures"]) == 2
    assert "East" in result.content[0].text and "West" in result.content[0].text
    assert "13:10" in result.content[0].text and "13:11" in result.content[0].text


@pytest.mark.parametrize("stamp,expected", [
    ("2030-09-15T23:30:00+02:00", "2030-09-16T00:30:00+02:00"),
    ("2030-10-27T02:30:00+02:00", "2030-10-27T02:30:00+01:00"),
    ("2030-03-31T01:30:00+01:00", "2030-03-31T03:30:00+02:00"),
])
def test_relative_board_time_preserves_midnight_and_dst_offsets(monkeypatch, stamp, expected):
    clock(monkeypatch, stamp)
    async def fetch(payload):
        assert payload["when"] == expected
        return {"status": "empty", "departures": []}
    monkeypatch.setattr(mcp, "db_departures", fetch)
    result = asyncio.run(mcp.next_departures(START.name, origin_station=START, departure_in_minutes=60))
    assert result.structured_content["requested_from"] == expected


@pytest.mark.parametrize("mode", ["bus", "suburban", "local_train"])
def test_optional_destination_is_generic_and_mode_is_not_global(monkeypatch, mode):
    clock(monkeypatch)
    seen = []
    async def fetch(payload):
        seen.append(payload)
        return {"status": "empty", "departures": []}
    monkeypatch.setattr(mcp, "db_departures", fetch)
    asyncio.run(mcp.next_departures(START.name, END.name, transport_mode=mode,
        origin_station=START, destination_station=END, deutschlandticket_only=True))
    asyncio.run(mcp.next_departures(START.name, origin_station=START))
    assert seen[0]["destination_id"] == END.provider_id
    assert seen[0]["transport_mode"] == mode
    assert seen[0]["deutschlandticket_only"] is True
    assert seen[1]["transport_mode"] == "all"
    assert "destination_id" not in seen[1]


@pytest.mark.parametrize("extra", [{"departure_in_minutes": -1}, {"departure_in_minutes": 10081},
    {"limit": 0}, {"limit": 21}, {"duration_minutes": 0}, {"duration_minutes": 361},
    {"origin": "Wrong station", "origin_station": START}])
def test_bad_input_never_reaches_lookup_or_provider(monkeypatch, extra):
    clock(monkeypatch)
    async def forbidden(*args, **kwargs):
        raise AssertionError("Invalid input reached provider")
    monkeypatch.setattr(mcp, "db_departures", forbidden)
    monkeypatch.setattr(mcp, "_departure_station", forbidden)
    result = asyncio.run(mcp.next_departures(**({"origin": START.name} | extra)))
    assert result.is_error
    assert result.structured_content["error"]["code"] == "INVALID_ARGUMENTS"


def test_source_failure_is_not_an_empty_timetable(monkeypatch):
    clock(monkeypatch)
    async def failed(payload):
        raise RuntimeError("synthetic source failure")
    monkeypatch.setattr(mcp, "db_departures", failed)
    result = asyncio.run(mcp.next_departures(START.name, origin_station=START))
    assert result.is_error
    assert result.structured_content["error"]["code"] == "SOURCE_UNAVAILABLE"


def test_ambiguous_station_requests_selection_without_querying_board(monkeypatch):
    clock(monkeypatch)
    async def stations(*args, **kwargs):
        return {"requires_selection": True, "stations": [
            {"name": "Start North", "provider": "db", "provider_id": "8000001"},
            {"name": "Start South", "provider": "db", "provider_id": "8000002"}]}
    async def forbidden(payload):
        raise AssertionError("Ambiguous station reached board")
    monkeypatch.setattr(mcp, "search_stations", stations)
    monkeypatch.setattr(mcp, "db_departures", forbidden)
    result = asyncio.run(mcp.next_departures("Start"))
    assert result.structured_content["status"] == "needs_selection"
    assert len(result.structured_content["stations"]) == 2


def test_unspecified_mode_reads_close_bus_and_train_stops_separately(monkeypatch):
    clock(monkeypatch)
    rail = {"name": "Start Bahnhof", "provider": "db", "provider_id": "8000001",
            "modes": ["SUBURBAN"], "latitude": 51.0, "longitude": 12.0}
    bus = {"name": "Start Bahnhof (Bus)", "provider": "db", "provider_id": "8000002",
           "modes": ["BUS"], "latitude": 51.0001, "longitude": 12.0}
    async def stations(*args, **kwargs):
        return {"query": "Start Bahnhof", "auto_selection": rail, "stations": [rail, bus], "transport_mode": "all"}
    calls = []
    async def fetch(payload):
        calls.append(payload)
        departure = row("East")
        departure.update(product="bus" if payload["transport_mode"] == "bus" else "suburban")
        return {"status": "ok", "departures": [departure]}
    monkeypatch.setattr(mcp, "search_stations", stations)
    monkeypatch.setattr(mcp, "db_departures", fetch)
    result = asyncio.run(mcp.next_departures("Start Bahnhof"))
    assert not result.is_error
    assert {p["transport_mode"] for p in calls} == {"bus", "local_train"}
    assert len(calls) == 2
    assert set(result.structured_content["by_transport"]) == {"bus", "local_train"}


def test_far_apart_or_explicit_mode_is_never_an_automatic_double_query():
    from reisevergleich.station_catalog import nearby_modal_options
    rail = {"name": "Start Bahnhof", "provider_id": "rail", "modes": ["SUBURBAN"], "latitude": 51.0, "longitude": 12.0}
    bus = {"name": "Start Bahnhof (Bus)", "provider_id": "bus", "modes": ["BUS"], "latitude": 52.0, "longitude": 12.0}
    base = {"query": "Start Bahnhof", "auto_selection": rail, "stations": [rail, bus]}
    assert nearby_modal_options(base) == []
    assert nearby_modal_options(base | {"transport_mode": "bus"}) == []


@pytest.mark.parametrize("place", ["Leipzig Engelsdorf", "Berlin Karow", "Dresden Niedersedlitz", "Hamburg Bergedorf"])
def test_bus_at_exact_rail_station_name_resolves_bus_stop_without_model_ids(monkeypatch, place):
    clock(monkeypatch)
    rail = {"name": place, "provider": "db", "provider_id": "rail-id", "modes": ["SUBURBAN"]}
    bus = {"name": "S-Bahnhof " + place, "provider": "db", "provider_id": "bus-id", "modes": ["BUS"]}
    calls = []
    async def stations(query, limit, transport_mode="all"):
        calls.append((query, transport_mode))
        if transport_mode == "all":
            return {"query": query, "stations": [rail, bus], "auto_selection": None, "requires_selection": True}
        if query == place + " Bahnhof":
            return {"query": query, "stations": [bus], "auto_selection": bus}
        return {"query": query, "stations": [bus], "auto_selection": None, "requires_selection": True}
    async def fetch(payload):
        assert payload["station_id"] == "bus-id"
        assert payload["transport_mode"] == "bus"
        return {"status": "ok", "departures": [{**row("West"), "product": "bus"}]}
    monkeypatch.setattr(mcp, "search_stations", stations)
    monkeypatch.setattr(mcp, "db_departures", fetch)
    result = asyncio.run(mcp.next_departures(place, transport_mode="bus"))
    assert not result.is_error
    assert result.structured_content["departures"][0]["product"] == "bus"
    assert calls == [(place, "bus"), (place, "all"), (place + " Bahnhof", "bus")]


def test_duplicate_region_label_is_a_safe_exact_station_alias(monkeypatch):
    clock(monkeypatch)
    rail = {"name": "Musterstadt West", "provider": "db", "provider_id": "rail-id", "modes": ["SUBURBAN"]}
    async def stations(*args, **kwargs):
        return {"stations": [rail], "auto_selection": None, "requires_selection": True}
    async def fetch(payload):
        assert payload["station_id"] == "rail-id"
        return {"status": "empty", "departures": []}
    monkeypatch.setattr(mcp, "search_stations", stations)
    monkeypatch.setattr(mcp, "db_departures", fetch)
    result = asyncio.run(mcp.next_departures("Musterstadt West, Musterstadt", transport_mode="suburban"))
    assert result.structured_content["status"] == "empty"


def test_same_named_distant_stations_still_require_selection(monkeypatch):
    clock(monkeypatch)
    rows = [{"name": "Musterstadt West", "provider": "db", "provider_id": code, "modes": ["SUBURBAN"]}
            for code in ["north", "south"]]
    async def stations(*args, **kwargs):
        return {"stations": rows, "auto_selection": None, "requires_selection": True}
    async def forbidden(payload):
        raise AssertionError("Ambiguous alias reached departures")
    monkeypatch.setattr(mcp, "search_stations", stations)
    monkeypatch.setattr(mcp, "db_departures", forbidden)
    result = asyncio.run(mcp.next_departures("Musterstadt West", transport_mode="suburban"))
    assert result.structured_content["status"] == "needs_selection"


def test_public_departure_tool_keeps_provider_id_choice_server_side():
    tools = asyncio.run(mcp.mcp.list_tools())
    board = next(tool for tool in tools if tool.name == "next_departures")
    properties = board.input_schema["properties"]
    assert "transport_mode" in properties
    assert "origin_station" not in properties
    assert "destination_station" not in properties


def test_overlap_uses_consistent_provider_coordinates_not_one_stale_source():
    from reisevergleich.station_catalog import nearby_modal_options
    rail = {"name": "Musterstadt West", "provider_id": "rail", "modes": ["SUBURBAN"],
            "latitude": 51.0, "longitude": 12.0,
            "provider_locations": {"db": {"latitude": 51.0, "longitude": 12.0},
                                   "transitous": {"latitude": 51.0, "longitude": 12.01}}}
    bus = {"name": "S-Bahnhof West, Musterstadt", "provider_id": "bus", "modes": ["BUS"],
           "latitude": 51.0, "longitude": 12.011,
           "provider_locations": {"db": {"latitude": 51.0, "longitude": 12.011},
                                  "transitous": {"latitude": 51.0, "longitude": 12.011}}}
    result = {"query": "Musterstadt West", "stations": [rail, bus]}
    assert [mode for mode, _ in nearby_modal_options(result)] == ["bus", "local_train"]


def test_unrelated_provider_coordinates_cannot_establish_an_overlap():
    from reisevergleich.station_catalog import nearby_modal_options
    rail = {"name": "Musterstadt West", "provider_id": "rail", "modes": ["SUBURBAN"],
            "latitude": 51.0, "longitude": 12.0,
            "provider_locations": {"db": {"latitude": 51.0, "longitude": 12.0}}}
    bus = {"name": "S-Bahnhof West, Musterstadt", "provider_id": "bus", "modes": ["BUS"],
           "latitude": 52.0, "longitude": 12.0,
           "provider_locations": {"transitous": {"latitude": 51.0, "longitude": 12.0001}}}
    assert nearby_modal_options({"query": "Musterstadt West", "stations": [rail, bus]}) == []
