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
