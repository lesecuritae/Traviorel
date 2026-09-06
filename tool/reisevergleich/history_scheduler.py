from __future__ import annotations

import asyncio
import logging

from . import cache
from .config import (
    HISTORY_ENABLED, HISTORY_SNAPSHOT_INITIAL_DELAY_SECONDS,
    HISTORY_SNAPSHOT_INTERVAL_SECONDS, HISTORY_SNAPSHOT_SCHEDULER_ENABLED,
)
from .history_cache import prune_daily_snapshots, save_daily_snapshot

LOG = logging.getLogger(__name__)


async def run_snapshot_cycle() -> dict[str, int]:
    """Archive already-computed history stats without fetching providers."""
    entries = await asyncio.to_thread(cache.history_stats_snapshot_entries)
    saved = 0
    failed = 0
    for entry in entries:
        try:
            await asyncio.to_thread(
                save_daily_snapshot,
                f"history-stats:{entry['cache_key']}",
                entry["payload"],
            )
            saved += 1
        except Exception:
            failed += 1
            LOG.exception("History-Snapshot konnte nicht geschrieben werden")
    try:
        removed = await asyncio.to_thread(prune_daily_snapshots)
    except Exception:
        removed = 0
        failed += 1
        LOG.exception("History-Snapshot-Retention fehlgeschlagen")
    return {"candidates": len(entries), "saved": saved, "failed": failed, "removed": removed}


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    if HISTORY_SNAPSHOT_INITIAL_DELAY_SECONDS:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HISTORY_SNAPSHOT_INITIAL_DELAY_SECONDS)
            return
        except TimeoutError:
            pass
    while not stop_event.is_set():
        try:
            await run_snapshot_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("History-Snapshot-Zyklus fehlgeschlagen; Scheduler läuft weiter")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HISTORY_SNAPSHOT_INTERVAL_SECONDS)
        except TimeoutError:
            continue


def start_scheduler() -> tuple[asyncio.Task[None], asyncio.Event] | None:
    if not HISTORY_ENABLED or not HISTORY_SNAPSHOT_SCHEDULER_ENABLED:
        return None
    stop_event = asyncio.Event()
    task = asyncio.create_task(scheduler_loop(stop_event), name="traviorel-history-snapshots")
    return task, stop_event


async def stop_scheduler(handle: tuple[asyncio.Task[None], asyncio.Event] | None) -> None:
    if handle is None:
        return
    task, stop_event = handle
    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=5)
    except TimeoutError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
