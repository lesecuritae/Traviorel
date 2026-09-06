"""Optionaler Live-Test: PYTHONPATH=tool python tests/live/test_history_huggingface.py."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="traviorel-history-live-") as directory:
        os.environ["HISTORY_CACHE_DIR"] = str(Path(directory) / "history")
        os.environ["REISE_CACHE_DB"] = str(Path(directory) / "cache.sqlite3")
        from reisevergleich.history import build_relation_samples, query_local_history
        from reisevergleich.history_cache import DETAIL_COLUMNS, DetailSpec, ensure_detail_cache, valid_parquet

        target = await ensure_detail_cache(DetailSpec("ICE", "728", 2026, 7))
        valid, row_count = valid_parquet(target)
        if not valid or row_count <= 0:
            raise AssertionError("Gefilterter Detailcache ist leer oder ungültig")
        print("HUGGING FACE REMOTE READ: OK")
        print("DETAIL CACHE WRITE: OK")

        rows = query_local_history([target])
        if not rows or not set(DETAIL_COLUMNS).issubset(rows[0]):
            raise AssertionError("Detailcache-Schema unvollständig")
        for required in ("train_number", "eva", "id", "arrival_planned_time", "departure_planned_time"):
            if required not in rows[0]:
                raise AssertionError(f"Pflichtfeld fehlt: {required}")
        print("DETAIL CACHE READ: OK")

        by_run: dict[str, list[dict]] = {}
        from reisevergleich.history import derive_run_instance_id, normalize_eva
        for row in rows:
            run_id = derive_run_instance_id(row.get("id"))
            if run_id and normalize_eva(row.get("eva")):
                by_run.setdefault(run_id, []).append(row)
        matched = False
        for stops in by_run.values():
            ordered = sorted(stops, key=lambda row: int(row.get("train_line_station_num") or -1))
            if len(ordered) < 2:
                continue
            origin, destination = normalize_eva(ordered[0]["eva"]), normalize_eva(ordered[-1]["eva"])
            if origin and destination and build_relation_samples(rows, origin, destination):
                matched = True
                break
        if not matched:
            raise AssertionError("Keine konkrete Fahrt und Relation rekonstruierbar")
        print("HISTORY MATCH: OK")
        print(f"DETAIL CACHE SIZE: {target.stat().st_size} bytes")


if __name__ == "__main__":
    asyncio.run(main())
