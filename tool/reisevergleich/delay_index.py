"""Verspätungsstatistik als kleiner lokaler Index über die öffentlichen Bahn-Verspätungsdaten.

Quelle ist der offene Datensatz ``piebro/deutsche-bahn-data`` auf Hugging Face (Ankunfts- und Abfahrtsereignisse je Halt,
monatlich als Parquet, etwa 600 MB je Monat). Die bisherige Abfrage je Zug (``history.py``) liest dafür viele
Monatsdateien über das Netz und schafft das bei einem neuen Zug oft nicht in der Zeit. Hier wird jeder Monat einmal
im Hintergrund zusammengefasst (Anzahl, Ausfälle, Verspätungssumme, Anteil pünktlich je Zug und Halt, rund zwei Minuten
je Monat) und in einer kleinen SQLite-Datei abgelegt. Danach antwortet die Abfrage sofort. Der Index wird erst beim ersten Bedarf
aufgebaut (``request_build``); mit ``DELAY_INDEX=0`` bleibt er aus.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from .config import HISTORY_CACHE_DIR

LOG = logging.getLogger(__name__)

DATASET = "piebro/deutsche-bahn-data"
SOURCE_TEMPLATE = os.environ.get(
    "DELAY_INDEX_SOURCE", f"https://huggingface.co/datasets/{DATASET}/resolve/main/monthly_processed_data/data-{{month}}.parquet"
)
TREE_URL = f"https://huggingface.co/api/datasets/{DATASET}/tree/main/monthly_processed_data"
MONTHS = min(max(int(os.environ.get("DELAY_INDEX_MONTHS", "3")), 1), 12)
ENABLED = os.environ.get("DELAY_INDEX", "1").strip().lower() not in {"0", "false", "no", "off"}
RAIL_TYPES = ("ICE", "IC", "EC", "ECE", "RE", "RB", "S", "IRE", "NJ", "RJ", "RJX", "FLX", "TGV")
_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "current": None, "done": [], "error": None}


THRESHOLDS = (0, 2, 5, 10, 15, 20, 30, 45, 60)  # Minuten; aus den Zählungen entsteht eine grobe Verteilung der Ankunftsverspätung


def _db_path() -> Path:
    return Path(os.environ.get("DELAY_INDEX_DB", str(Path(HISTORY_CACHE_DIR) / "delay-index-v2.sqlite3")))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    columns = ", ".join(f"c{t} INTEGER NOT NULL" for t in THRESHOLDS)
    db.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS months(month TEXT PRIMARY KEY, built_at REAL NOT NULL, rows INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS stats(
            month TEXT NOT NULL, train_type TEXT NOT NULL, train_number TEXT NOT NULL, eva TEXT NOT NULL,
            n INTEGER NOT NULL, cancelled INTEGER NOT NULL, sum_delay REAL NOT NULL, {columns},
            PRIMARY KEY(month, train_type, train_number, eva)
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS stats_by_train ON stats(train_type, train_number, eva);
        """
    )
    return db


def wanted_months(today: date | None = None, count: int = MONTHS) -> list[str]:
    """Die letzten ``count`` vollständigen Monate, neuester zuerst (YYYY-MM)."""
    today = today or date.today()
    year, month = today.year, today.month
    months = []
    for _ in range(count):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        months.append(f"{year:04d}-{month:02d}")
    return months


def _available_months() -> set[str] | None:
    try:
        request = urllib.request.Request(TREE_URL, headers={"User-Agent": "Traviorel"})
        with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310 - feste https-Adresse
            entries = json.load(response)
        return {entry["path"].split("data-")[-1].removesuffix(".parquet") for entry in entries if str(entry.get("path", "")).endswith(".parquet")}
    except Exception:  # noqa: BLE001 - dann wird jeder gewünschte Monat einfach versucht
        return None


def _aggregate_month(month: str) -> list[tuple]:
    import duckdb

    source = SOURCE_TEMPLATE.format(month=month)
    types = ", ".join(f"'{value}'" for value in RAIL_TYPES)
    con = duckdb.connect()
    try:
        con.execute("SET http_timeout=300")
        con.execute("SET threads=2")
        con.execute("SET memory_limit='1GB'")
        counts = ",\n                   ".join(
            f"sum(CASE WHEN NOT coalesce(arrival_is_canceled, false) AND date_diff('minute', arrival_planned_time, arrival_change_time) <= {t} THEN 1 ELSE 0 END) AS c{t}"
            for t in THRESHOLDS
        )
        return con.execute(
            f"""
            SELECT train_type, train_number, ltrim(eva, '0') AS eva,
                   count(*) AS n,
                   sum(CASE WHEN coalesce(arrival_is_canceled, false) THEN 1 ELSE 0 END) AS cancelled,
                   sum(CASE WHEN NOT coalesce(arrival_is_canceled, false) THEN date_diff('second', arrival_planned_time, arrival_change_time) / 60.0 ELSE 0 END) AS sum_delay,
                   {counts}
            FROM read_parquet('{source}')
            WHERE train_type IN ({types}) AND arrival_planned_time IS NOT NULL AND arrival_change_time IS NOT NULL
            GROUP BY 1, 2, 3
            """
        ).fetchall()
    finally:
        con.close()


def build_month(month: str) -> int:
    rows = _aggregate_month(month)
    with _connect() as db:
        db.execute("DELETE FROM stats WHERE month = ?", (month,))
        db.executemany(f"INSERT INTO stats VALUES ({','.join('?' * (7 + len(THRESHOLDS)))})", [(month, *row) for row in rows])
        db.execute("INSERT OR REPLACE INTO months VALUES (?, ?, ?)", (month, time.time(), len(rows)))
        keep = wanted_months(count=MONTHS)
        db.execute(f"DELETE FROM stats WHERE month NOT IN ({','.join('?' * len(keep))})", keep)
        db.execute(f"DELETE FROM months WHERE month NOT IN ({','.join('?' * len(keep))})", keep)
    return len(rows)


def built_months() -> list[str]:
    with _connect() as db:
        return [row[0] for row in db.execute("SELECT month FROM months ORDER BY month DESC")]


def _run_build() -> None:
    try:
        available = _available_months()
        for month in wanted_months():
            if month in built_months():
                _state["done"].append(month)
                continue
            if available is not None and month not in available:
                continue
            _state["current"] = month
            LOG.info("Verspätungsindex: Monat %s wird aufgebaut", month)
            build_month(month)
            _state["done"].append(month)
        _state["error"] = None
    except Exception as exc:  # noqa: BLE001
        LOG.exception("Verspätungsindex konnte nicht aufgebaut werden")
        _state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _state["running"] = False
        _state["current"] = None


def request_build() -> dict[str, Any]:
    """Startet den Aufbau im Hintergrund, falls nötig, und meldet den Stand."""
    if not ENABLED:
        return {"state": "disabled"}
    with _lock:
        missing = [month for month in wanted_months() if month not in built_months()]
        if missing and not _state["running"]:
            _state.update(running=True, current=None, done=[], error=None)
            threading.Thread(target=_run_build, name="delay-index", daemon=True).start()
    return status()


def status() -> dict[str, Any]:
    if not ENABLED:
        return {"state": "disabled"}
    built = built_months()
    wanted = wanted_months()
    ready = bool(built)
    running = bool(_state["running"])
    return {
        "state": "building" if running else ("ready" if ready and not [m for m in wanted if m not in built] else ("partial" if ready else "empty")),
        "built_months": built, "wanted_months": wanted, "current_month": _state["current"], "error": _state["error"],
    }


def _stats_query(train_type: str, train_number: str, eva: str) -> dict[str, Any] | None:
    sums = ", ".join(f"sum(c{t})" for t in THRESHOLDS)
    with _connect() as db:
        row = db.execute(
            f"SELECT sum(n), sum(cancelled), sum(sum_delay), min(month), max(month), {sums} FROM stats "
            "WHERE train_type = ? AND train_number = ? AND eva = ?",
            (str(train_type).upper(), str(train_number).strip(), str(eva).strip().lstrip("0")),
        ).fetchone()
    if not row or not row[0]:
        return None
    n, cancelled, sum_delay, first, last, *counts = row
    ran = n - cancelled
    if ran <= 0:
        return {"events": n, "cancelled_percent": 100.0, "months": [first, last]}
    by_threshold = dict(zip(THRESHOLDS, counts, strict=True))
    return {
        "events": int(n), "ran": int(ran), "cancelled": int(cancelled), "cancelled_percent": round(100 * cancelled / n, 1),
        "average_delay_minutes": round(sum_delay / ran, 1),
        "on_time_5_percent": round(100 * by_threshold[5] / ran), "on_time_10_percent": round(100 * by_threshold[10] / ran),
        "within_30_percent": round(100 * by_threshold[30] / ran), "months": [first, last],
        # Anteil aller Halte (Ausfälle zählen als nicht erreicht), der höchstens so viele Minuten verspätet ankommt
        "cdf": [(t, by_threshold[t] / n) for t in THRESHOLDS],
    }


def share_within(cdf: list[tuple[int, float]], minutes: float) -> float:
    """Anteil der Halte mit höchstens ``minutes`` Verspätung (linear zwischen den gezählten Schwellen; darüber der letzte Wert)."""
    if minutes < 0 or not cdf:
        return 0.0
    previous = (-1, 0.0)
    for threshold, share in cdf:
        if minutes <= threshold:
            span = threshold - previous[0]
            return previous[1] + (share - previous[1]) * ((minutes - previous[0]) / span if span else 1.0)
        previous = (threshold, share)
    return cdf[-1][1]


async def arrival_stats(train_type: str, train_number: str, eva: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(_stats_query, train_type, train_number, eva)
