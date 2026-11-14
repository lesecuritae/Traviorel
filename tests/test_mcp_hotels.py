"""Hotel MCP uses the existing adapter; prices below are synthetic fixtures."""
import asyncio
from datetime import timedelta

from reisevergleich import mcp_server as mcp


def dates():
    checkin = mcp._server_now().date() + timedelta(days=7)
    return checkin.isoformat(), (checkin + timedelta(days=3)).isoformat()


def test_standalone_hotel_tool_is_registered():
    tools = asyncio.run(mcp.mcp.list_tools())
    assert "search_hotels" in {tool.name for tool in tools}


def test_hotel_stay_total_is_distinct_from_nightly_price(monkeypatch):
    async def provider(request):
        assert request.location == "Musterstadt"
        assert request.adults == 2 and request.min_stars == 3
        return {"status": "ok", "hotel_options": [
            {"name": "Fixture Stay", "verified_total_price": 240, "nightly_price": 80, "currency": "EUR"},
            {"name": "Fixture Night", "nightly_price": 90, "currency": "EUR"},
        ], "provider_statuses": [{"provider": "fixture", "ok": True}]}
    monkeypatch.setattr(mcp, "search_hotel_provider", provider, raising=False)
    result = asyncio.run(mcp.search_hotels("Musterstadt", *dates(), adults=2))
    assert not result.is_error
    assert result.structured_content["hotel_options"][0]["verified_total_price"] == 240
    assert "240,00" in result.content[0].text and "gesamt" in result.content[0].text
    assert "90,00" in result.content[0].text and "pro Nacht" in result.content[0].text
    assert "Gesamtpreis nicht bestätigt" in result.content[0].text


def test_bad_dates_do_not_call_provider(monkeypatch):
    async def forbidden(request):
        raise AssertionError("Invalid hotel request reached provider")
    monkeypatch.setattr(mcp, "search_hotel_provider", forbidden, raising=False)
    checkin, _ = dates()
    result = asyncio.run(mcp.search_hotels("Musterstadt", checkin, checkin))
    assert result.is_error
    assert result.structured_content["error"]["code"] == "INVALID_ARGUMENTS"


def test_no_provider_result_is_not_claimed_as_no_available_hotels(monkeypatch):
    async def provider(request):
        return {"status": "manual_required", "hotel_options": [],
                "provider_statuses": [{"provider": "fixture", "ok": False, "timed_out": True}]}
    monkeypatch.setattr(mcp, "search_hotel_provider", provider, raising=False)
    result = asyncio.run(mcp.search_hotels("Musterstadt", *dates()))
    assert result.is_error
    assert result.structured_content["status"] == "manual_required"
    assert result.structured_content["provider_statuses"][0]["timed_out"] is True


def test_provider_exception_is_explicit_source_failure(monkeypatch):
    async def failed(request):
        raise RuntimeError("fixture outage")
    monkeypatch.setattr(mcp, "search_hotel_provider", failed, raising=False)
    result = asyncio.run(mcp.search_hotels("Musterstadt", *dates()))
    assert result.is_error
    assert result.structured_content["error"]["code"] == "SOURCE_UNAVAILABLE"
