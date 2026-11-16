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
    now = datetime(2026, 11, 17, 22, 0, tzinfo=BERLIN)
    assert mcp_server._live_window("2026-11-18T00:10+01:00", now) == ("2026-11-17", "23:50")
    assert mcp_server._live_window("2026-11-18T18:51+01:00", now) == ("2026-11-18", "18:31")
    assert mcp_server._live_window("2026-11-17T23:30:00", now) == ("2026-11-17", "23:10")


def test_live_window_starts_today_for_a_departure_just_after_midnight():
    now = datetime(2026, 11, 18, 0, 5, tzinfo=BERLIN)
    assert mcp_server._live_window("2026-11-18T00:10+01:00", now) == ("2026-11-18", "00:00")


def test_live_window_converts_other_offsets_and_survives_garbage():
    now = datetime(2026, 11, 17, 12, 0, tzinfo=BERLIN)
    assert mcp_server._live_window("2026-11-17T23:05+00:00", now) == ("2026-11-17", "23:45")
    assert mcp_server._live_window("kein Datum", now) == ("kein Datum", "00:00")


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
