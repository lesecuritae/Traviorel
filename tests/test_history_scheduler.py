from __future__ import annotations

import asyncio
import time

import reisevergleich.history_scheduler as scheduler
from reisevergleich import cache


def test_history_stats_are_read_from_real_cache_schema(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REISE_CACHE_DB", str(tmp_path / "cache.sqlite3"))
    cache._set_component_sync("history.stats", {"train": "ICE 618"}, {"status": "ok", "sample_count": 12}, 60)
    cache._set_component_sync("other.namespace", {"train": "ICE 618"}, {"status": "ok"}, 60)
    entries = cache.history_stats_snapshot_entries()
    assert len(entries) == 1
    assert entries[0]["payload"] == {"status": "ok", "sample_count": 12}
    assert entries[0]["updated_at"] <= time.time()


async def test_snapshot_cycle_archives_cached_stats(monkeypatch) -> None:
    entries = [
        {"cache_key": "abc123", "payload": {"status": "ok", "sample_count": 12}, "updated_at": 1.0},
        {"cache_key": "def456", "payload": {"status": "insufficient_data", "sample_count": 2}, "updated_at": 2.0},
    ]
    saved = []
    monkeypatch.setattr(scheduler.cache, "history_stats_snapshot_entries", lambda: entries)
    monkeypatch.setattr(scheduler, "save_daily_snapshot", lambda snapshot_id, payload: saved.append((snapshot_id, payload)))
    monkeypatch.setattr(scheduler, "prune_daily_snapshots", lambda: 3)
    result = await scheduler.run_snapshot_cycle()
    assert result == {"candidates": 2, "saved": 2, "failed": 0, "removed": 3}
    assert [item[0] for item in saved] == ["history-stats:abc123", "history-stats:def456"]


async def test_scheduler_starts_and_runs_on_schedule(monkeypatch) -> None:
    triggered = asyncio.Event()

    async def cycle():
        triggered.set()
        return {"saved": 0}

    monkeypatch.setattr(scheduler, "HISTORY_ENABLED", True)
    monkeypatch.setattr(scheduler, "HISTORY_SNAPSHOT_SCHEDULER_ENABLED", True)
    monkeypatch.setattr(scheduler, "HISTORY_SNAPSHOT_INITIAL_DELAY_SECONDS", 0)
    monkeypatch.setattr(scheduler, "HISTORY_SNAPSHOT_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(scheduler, "run_snapshot_cycle", cycle)
    handle = scheduler.start_scheduler()
    assert handle is not None and handle[0].get_name() == "traviorel-history-snapshots"
    await asyncio.wait_for(triggered.wait(), timeout=1)
    await scheduler.stop_scheduler(handle)
    assert handle[0].done()


async def test_scheduler_failure_never_blocks_search(monkeypatch) -> None:
    attempts = 0
    recovered = asyncio.Event()

    async def broken_then_recover():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("snapshot failure")
        recovered.set()
        return {"saved": 0}

    async def independent_search():
        await asyncio.sleep(0)
        return {"status": "ok"}

    monkeypatch.setattr(scheduler, "HISTORY_ENABLED", True)
    monkeypatch.setattr(scheduler, "HISTORY_SNAPSHOT_SCHEDULER_ENABLED", True)
    monkeypatch.setattr(scheduler, "HISTORY_SNAPSHOT_INITIAL_DELAY_SECONDS", 0)
    monkeypatch.setattr(scheduler, "HISTORY_SNAPSHOT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(scheduler, "run_snapshot_cycle", broken_then_recover)
    handle = scheduler.start_scheduler()
    assert await asyncio.wait_for(independent_search(), timeout=0.1) == {"status": "ok"}
    await asyncio.wait_for(recovered.wait(), timeout=1)
    assert handle is not None and not handle[0].done()
    await scheduler.stop_scheduler(handle)


async def test_scheduler_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setattr(scheduler, "HISTORY_ENABLED", True)
    monkeypatch.setattr(scheduler, "HISTORY_SNAPSHOT_SCHEDULER_ENABLED", False)
    assert scheduler.start_scheduler() is None
    await scheduler.stop_scheduler(None)
