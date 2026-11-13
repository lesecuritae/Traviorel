"""Invalid MCP search inputs must explain the correction without querying providers."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from reisevergleich import mcp_server, models


def freeze(monkeypatch, stamp="2030-09-15T13:00:00+02:00"):
    now = datetime.fromisoformat(stamp).astimezone(ZoneInfo("Europe/Berlin"))
    monkeypatch.setattr(models, "today_iso", lambda: now.date().isoformat())
    monkeypatch.setattr(mcp_server, "_server_now", lambda: now, raising=False)


@pytest.mark.parametrize("change,field,fragment", [
    ({"date": "2030-09-14"}, "date", "Vergangenheit"),
    ({"destination": "Leipzig Hbf"}, "request", "verschieden"),
    ({"departure_after": "25:99"}, "departure_after", ""),
])
def test_invalid_search_is_actionable_and_does_not_call_providers(monkeypatch, change, field, fragment):
    freeze(monkeypatch)
    async def forbidden(request):
        raise AssertionError("Invalid input reached a provider")
    monkeypatch.setattr(mcp_server, "search", forbidden)
    args = dict(origin="Leipzig Hbf", destination="Berlin Hbf", date="2030-09-15")
    result = asyncio.run(mcp_server.search_ground(**(args | change)))
    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "INVALID_ARGUMENTS"
    assert result.structured_content["current_date"] == "2030-09-15"
    errors = result.structured_content["error"]["fields"]
    assert any(e["field"] == field and fragment in e["message"] for e in errors)
    assert "2030-09-15" in result.content[0].text
    assert "Error executing tool" not in result.content[0].text


def test_valid_search_keeps_connections_and_prices(monkeypatch):
    freeze(monkeypatch)
    async def search(request):
        assert request.departure_date == "2030-09-15"
        assert request.departure_after == "13:00"
        return {"connections": [{"provider": "Deutsche Bahn", "price": 22.99,
            "currency": "EUR", "departure": "2030-09-15T13:12+02:00",
            "arrival": "2030-09-15T14:29+02:00"}]}
    monkeypatch.setattr(mcp_server, "search", search)
    result = asyncio.run(mcp_server.search_ground("Leipzig Hbf", "Berlin Hbf", "2030-09-15", departure_after="13:00"))
    assert not result.is_error
    assert result.structured_content["connections"][0]["price"] == 22.99
    assert "13:12" in result.content[0].text


@pytest.mark.parametrize("stamp,args,expected_date,expected_time", [
    ("2030-09-15T13:00:00+02:00", {}, "2030-09-15", "13:00"),
    ("2030-09-15T13:00:00+02:00", {"date": " heute "}, "2030-09-15", "13:00"),
    ("2030-09-15T13:00:00+02:00", {"date": "TODAY"}, "2030-09-15", "13:00"),
    ("2030-09-15T13:00:00+02:00", {"date": "2030-09-16"}, "2030-09-16", "06:00"),
    ("2030-09-15T13:00:00+02:00", {"departure_in_minutes": 60}, "2030-09-15", "14:00"),
    ("2030-09-15T23:30:00+02:00", {"departure_in_minutes": 60}, "2030-09-16", "00:30"),
    ("2030-03-31T01:30:00+01:00", {"departure_in_minutes": 60}, "2030-03-31", "03:30"),
])
def test_server_resolves_search_window(monkeypatch, stamp, args, expected_date, expected_time):
    freeze(monkeypatch, stamp)
    async def search(request):
        assert request.departure_date == expected_date
        assert request.departure_after == expected_time
        return {"connections": []}
    monkeypatch.setattr(mcp_server, "search", search)
    result = asyncio.run(mcp_server.search_ground("Start", "Ziel", **args))
    assert not result.is_error
    assert result.structured_content["search"] == {
        "date": expected_date, "departure_after": expected_time, "timezone": "Europe/Berlin"}


def test_current_time_comes_from_server_clock(monkeypatch):
    freeze(monkeypatch)
    result = mcp_server.current_time()
    assert result.structured_content["datetime"] == "2030-09-15T13:00:00+02:00"
    assert result.structured_content["date"] == "2030-09-15"
    assert result.structured_content["source"] == "server_clock"
    assert result.structured_content["timezone"] == "Europe/Berlin"


@pytest.mark.parametrize("args", [
    {"departure_in_minutes": -1}, {"departure_in_minutes": 10081},
    {"departure_in_minutes": True}, {"departure_in_minutes": 60, "departure_after": "12:00"},
    {"departure_in_minutes": 60, "date": "2030-09-14"},
])
def test_conflicting_or_unbounded_relative_times_never_reach_provider(monkeypatch, args):
    freeze(monkeypatch)
    async def forbidden(request):
        raise AssertionError("Invalid time reached provider")
    monkeypatch.setattr(mcp_server, "search", forbidden)
    result = asyncio.run(mcp_server.search_ground("Start", "Ziel", **args))
    assert result.is_error
    assert result.structured_content["error"]["code"] == "INVALID_ARGUMENTS"


def test_second_autumn_hour_is_not_silently_shifted_back(monkeypatch):
    freeze(monkeypatch, "2030-10-27T02:30:00+02:00")
    async def forbidden(request):
        raise AssertionError("Ambiguous second hour reached provider")
    monkeypatch.setattr(mcp_server, "search", forbidden)
    result = asyncio.run(mcp_server.search_ground("Start", "Ziel", departure_in_minutes=60))
    assert result.is_error
    assert "nicht eindeutig" in result.content[0].text


def test_selected_station_id_is_preserved(monkeypatch):
    freeze(monkeypatch)
    async def search(request):
        assert request.origin_station.id_for("db") == "8013070"
        assert request.origin == "Leipzig Allee-Center"
        return {"connections": []}
    monkeypatch.setattr(mcp_server, "search", search)
    station = models.StationSelection(name="Leipzig Allee-Center", provider="db", provider_id="8013070")
    destination = models.StationSelection(name="Leipzig Hbf", provider="db", provider_id="8010205")
    result = asyncio.run(mcp_server.search_ground(station.name, destination.name, date="heute", origin_station=station, destination_station=destination))
    assert not result.is_error


def test_station_discovery_supplies_reusable_selection(monkeypatch):
    async def stations(query, limit, transport_mode="all"):
        return {"requires_selection": True, "stations": [{"name": "Leipzig Allee-Center", "provider": "db",
            "provider_id": "8013070", "provider_ids": {"db": "8013070"}, "region": "Leipzig"}]}
    monkeypatch.setattr(mcp_server, "search_stations", stations)
    result = asyncio.run(mcp_server.find_station("Allee-Center Leipzig"))
    row = result.structured_content["stations"][0]
    assert row["selection"]["provider_id"] == "8013070"
    assert row["ids"] == {"db": "8013070"}
    assert result.structured_content["requires_selection"] is True
