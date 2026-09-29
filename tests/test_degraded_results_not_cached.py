"""Degraded provider and trip results stay out of the shared caches (audit 2026-09-28, lead 12)."""
from reisevergleich import cache, service
from reisevergleich.models import TripRequest

BASE = dict(origin="Startort", destination="Zielort", departure_date="2030-09-15", include_hotel=False)


def test_failed_db_search_tuple_is_not_cacheable():
    assert cache._cacheable_component(({"status": "failed", "journeys": []}, {"attempts": 3})) is False
    assert cache._cacheable_component(({"status": "ok", "journeys": [1]}, {"attempts": 1})) is True
    assert cache._cacheable_component(()) is True


async def test_failed_producer_result_is_recomputed_not_served(tmp_path, monkeypatch):
    monkeypatch.setenv("REISE_CACHE_DB", str(tmp_path / "cache.sqlite3"))
    calls = []

    async def producer():
        calls.append(1)
        return ({"status": "failed", "journeys": []}, {"attempts": 3})

    for _ in range(3):
        await cache.cached_call("db.search", {"route": "A-B"}, 600, producer)
    assert len(calls) == 3, "a failed tuple result must not be served from the component cache"


async def _run_search(monkeypatch, tmp_path, status):
    monkeypatch.setenv("REISE_CACHE_DB", str(tmp_path / "cache.sqlite3"))
    computed = []

    async def fake_compute(request):
        computed.append(1)
        return {"status": status, "search_mode": "trip_plan"}

    monkeypatch.setattr(service, "_compute", fake_compute)
    monkeypatch.setattr(service, "public_result", lambda result: result)
    monkeypatch.setattr(service.price_history, "record_search", lambda result: None)
    request = TripRequest(**BASE)
    first = await service.search(request)
    second = await service.search(request)
    return first, second, computed


async def test_partial_trip_result_is_not_stored_for_other_clients(monkeypatch, tmp_path):
    first, second, computed = await _run_search(monkeypatch, tmp_path, "partial")
    assert "journey_id" not in first
    assert second["cache"]["journey_hit"] is False
    assert len(computed) == 2


async def test_ok_trip_result_is_still_cached(monkeypatch, tmp_path):
    first, second, computed = await _run_search(monkeypatch, tmp_path, "ok")
    assert first.get("journey_id")
    assert second["cache"]["journey_hit"] is True
    assert len(computed) == 1


async def test_degraded_rows_written_before_the_fix_are_not_served(monkeypatch, tmp_path):
    monkeypatch.setenv("REISE_CACHE_DB", str(tmp_path / "cache.sqlite3"))
    request = TripRequest(**BASE)
    await cache.save_journey(request, {"status": "manual_required"})
    assert await cache.get_cached_journey(request) is None
