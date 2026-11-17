"""Regressionen: Beobachtungsliste mit Umlauten, Echtzeitfenster um Mitternacht, Eingabefehler der MCP-Werkzeuge."""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("DB_CFFI_TOKEN", "0" * 64)

from reisevergleich import api, mcp_server, price_history as ph  # noqa: E402
from reisevergleich.utils import route_departure_in_window  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture()
def watch_db(monkeypatch):
    with tempfile.TemporaryDirectory() as folder:
        monkeypatch.setenv("PRICE_HISTORY_DB", os.path.join(folder, "prices.sqlite3"))
        yield


@pytest.mark.parametrize("origin", ["Gießen", "Österreich Wien", "Übersee", "Leipzig Hbf"])
def test_watch_is_not_duplicated_for_non_ascii_names(watch_db, origin):
    first = ph.add_watch(origin, "Hamburg Hbf")
    again = ph.add_watch(f"  {origin.upper()} ", "hamburg  hbf")
    assert again["id"] == first["id"]
    assert len(ph.list_watches()) == 1


def test_known_watch_is_returned_when_the_list_is_full(watch_db):
    first = ph.add_watch("Gießen", "Hamburg Hbf")
    for index in range(ph.MAX_WATCHES - 1):
        ph.add_watch(f"Ort {index}", "Ziel")
    assert ph.add_watch("gießen", "Hamburg Hbf")["id"] == first["id"]
    with pytest.raises(ValueError):
        ph.add_watch("Einer zu viel", "Ziel")


def test_live_window_keeps_the_day_of_the_window_start():
    assert mcp_server._live_window("2026-11-18T18:51+01:00") == ("2026-11-18", "18:31")
    assert mcp_server._live_window("2026-11-17T23:30:00") == ("2026-11-17", "23:10")


def test_live_window_clamps_to_midnight_instead_of_the_previous_day():
    # route_departure_in_window (utils.py) only accepts a departure up to midnight *after*
    # travel_date; a window dated the previous day would exclude the very departure being
    # searched for. That silent end-to-end break is what test_live_window_result_matches_its_own_departure
    # below guards against directly.
    assert mcp_server._live_window("2026-11-18T00:10+01:00") == ("2026-11-18", "00:00")
    assert mcp_server._live_window("2026-11-18T00:19+01:00") == ("2026-11-18", "00:00")


def test_live_window_converts_other_offsets_and_survives_garbage():
    # 23:05 UTC is 00:05 CET the next day - the same midnight case as above, just via timezone conversion.
    assert mcp_server._live_window("2026-11-17T23:05+00:00") == ("2026-11-18", "00:00")
    assert mcp_server._live_window("2026-11-17T20:05+00:00") == ("2026-11-17", "20:45")
    assert mcp_server._live_window("kein Datum") == ("kein Datum", "00:00")


@pytest.mark.parametrize("departure", [
    "2026-11-18T00:10+01:00", "2026-11-18T00:19+01:00", "2026-11-17T23:30:00+01:00", "2026-11-18T18:51+01:00",
])
def test_live_window_result_matches_its_own_departure(departure):
    """The (date, time) _live_window returns must let route_departure_in_window accept the exact
    departure it was computed from - otherwise live_delays searches a window that excludes the
    connection it is looking for, even though _live_window's own unit tests look correct in isolation."""
    window_date, window_start = mcp_server._live_window(departure)
    assert route_departure_in_window({"departure": departure}, window_date, window_start, tolerance_minutes=0)


def test_live_delays_reports_a_past_trip_instead_of_raising(monkeypatch):
    async def must_not_search(_request):
        raise AssertionError("eine vergangene Fahrt darf keine Suche auslösen")

    monkeypatch.setattr(mcp_server, "search", must_not_search)
    yesterday = (datetime.now(BERLIN) - timedelta(days=1)).replace(hour=12, minute=0).isoformat(timespec="minutes")
    ref = mcp_server._remember([{"provider": "Deutsche Bahn", "origin": "Leipzig Hbf", "destination": "Berlin Hbf", "departure": yesterday, "legs": []}])[0]
    reply = asyncio.run(mcp_server.live_delays(ref))
    assert "nicht mehr bei der Bahn" in reply.content[0].text


def test_flight_and_calendar_tools_answer_invalid_input_as_tool_errors(monkeypatch):
    async def must_not_search(_request):
        raise AssertionError("ungültige Eingaben dürfen keine Suche auslösen")

    monkeypatch.setattr(mcp_server, "search", must_not_search)
    monkeypatch.setattr(mcp_server, "price_calendar", must_not_search)
    flight = asyncio.run(mcp_server.plan_flight_trip("Leipzig", "Rom", "2020-01-01"))
    calendar = asyncio.run(mcp_server.price_calendar_search("Leipzig Hbf", "Hamburg Hbf", "2020-01-01"))
    same = asyncio.run(mcp_server.price_calendar_search("Leipzig Hbf", "leipzig hbf", datetime.now(BERLIN).date().isoformat()))
    for reply in (flight, calendar, same):
        assert reply.is_error is True
        assert reply.structured_content["error"]["code"] == "INVALID_ARGUMENTS"
    assert calendar.structured_content["error"]["fields"][0]["field"] == "date"


def test_refs_of_two_searches_never_share_a_tag(monkeypatch):
    mcp_server._refs.clear()
    tags = iter(["aaaa", "aaaa", "aaaa", "bbbb"])
    monkeypatch.setattr(mcp_server.secrets, "token_hex", lambda _n: next(tags))
    first = mcp_server._remember([{"n": 1}])
    second = mcp_server._remember([{"n": 2}])
    assert first == ["aaaa-1"] and second == ["bbbb-1"]
    assert mcp_server._connection("aaaa-1") == {"n": 1}
    mcp_server._refs.clear()


def test_diagnostics_does_not_publish_exception_messages(monkeypatch):
    class Broken:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("http://db-api:3001 interner Hostname")

    async def stub():
        return {"enabled": False, "status": "ok"}

    monkeypatch.setattr(api.httpx, "AsyncClient", Broken)
    monkeypatch.setattr(api, "capability_report", stub)
    monkeypatch.setattr(api, "cache_health", stub)
    result = asyncio.run(api.diagnostics())
    assert result["status"] == "degraded"
    assert result["db_api"] == {"ok": False, "error": "RuntimeError"}
    assert result["transitous"] == {"ok": False, "error": "RuntimeError"}
    assert "db-api" not in str(result)


def test_diagnostics_redacts_cache_path_and_trvl_stderr(monkeypatch):
    class Broken:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("boom")

    async def cache_with_path():
        return {"status": "ok", "path": "/data/reisevergleich/cache.sqlite3", "schema": 7}

    async def trvl_with_stderr():
        return {"flights": {"ok": False, "error": "/etc/trvl/secret-config.toml: Permission denied"}}

    monkeypatch.setattr(api.httpx, "AsyncClient", Broken)
    monkeypatch.setattr(api, "capability_report", trvl_with_stderr)
    monkeypatch.setattr(api, "cache_health", cache_with_path)
    result = asyncio.run(api.diagnostics())
    assert result["cache"] == {"status": "ok"}
    assert result["trvl"] == {"flights": {"ok": False}}
    assert "cache.sqlite3" not in str(result)
    assert "secret-config" not in str(result)
