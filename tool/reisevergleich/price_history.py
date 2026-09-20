"""Preisverlauf: Traviorel merkt sich die Preise, die es bei Suchen sieht (nur lokal, SQLite im Datenordner).

Die Quellen liefern nur den aktuellen Preis. Damit man sehen kann, wie sich der Preis einer Fahrt entwickelt, je näher der
Reisetag rückt, und ob ein Preis gut ist, schreibt Traviorel bei jeder frischen Suche den günstigsten Preis je Anbieter,
Strecke, Reisetag und Abrufdatum mit. Eine kleine Beobachtungsliste fragt ein paar Strecken einmal am Tag von selbst ab,
damit die Kurve auch ohne eigene Suchen wächst. Der Verlauf beginnt leer; rückwirkend geht nichts.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import statistics
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import HISTORY_CACHE_DIR

LOG = logging.getLogger(__name__)

ENABLED = os.environ.get("PRICE_HISTORY", "1").strip().lower() not in {"0", "false", "no", "off"}
WATCH_INTERVAL_HOURS = min(max(float(os.environ.get("PRICE_WATCH_INTERVAL_HOURS", "24")), 1.0), 168.0)
WATCH_LEAD_DAYS = (2, 7, 14, 21, 28)  # so viele Tage im Voraus wird beobachtet: ergibt die Kurve „Preis vs. Tage bis Abreise“
MAX_WATCHES = 5
RETENTION_DAYS = 730
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations(
    obs_day TEXT NOT NULL, kind TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL, travel_date TEXT NOT NULL,
    provider TEXT NOT NULL, variant TEXT NOT NULL DEFAULT '', price_cents INTEGER NOT NULL,
    PRIMARY KEY(obs_day, kind, origin, destination, travel_date, provider, variant)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS obs_by_route ON observations(kind, origin, destination, travel_date);
CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL, destination TEXT NOT NULL, created TEXT NOT NULL, last_run TEXT);
"""


def _db_path() -> Path:
    return Path(os.environ.get("PRICE_HISTORY_DB", str(Path(HISTORY_CACHE_DIR) / "price-history.sqlite3")))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.executescript(_SCHEMA)
    return db


def _key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().date().isoformat()


# ---- mitschreiben -------------------------------------------------------------------------------


def _write(rows: list[tuple]) -> None:
    if not rows:
        return
    with _lock, _connect() as db:
        db.executemany(
            "INSERT INTO observations(obs_day, kind, origin, destination, travel_date, provider, variant, price_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(obs_day, kind, origin, destination, travel_date, provider, variant) DO UPDATE SET price_cents = MIN(price_cents, excluded.price_cents)",
            rows,
        )
        db.execute("DELETE FROM observations WHERE obs_day < ?", ((date.fromisoformat(_today()) - timedelta(days=RETENTION_DAYS)).isoformat(),))


def _rows_from_result(result: dict[str, Any]) -> list[tuple]:
    context = result.get("response_context") or {}
    route = context.get("route") or {}
    day = _today()
    rows: list[tuple] = []
    outbound_date = route.get("outbound_date")
    if not outbound_date:
        return rows
    flight = context.get("flight") or {}
    price = (flight.get("price") or {}).get("value")
    if isinstance(price, (int, float)) and price > 0 and route.get("origin_airport") and route.get("destination_airport"):
        rows.append((day, "flight", _key(route["origin_airport"]), _key(route["destination_airport"]), outbound_date,
                     str(flight.get("provider") or "Flug"), f"{route.get('stay_nights', 0)} Nächte", round(price * 100)))
    if not route.get("return_date") and not flight:
        best: dict[str, float] = {}
        for connection in (context.get("outbound") or {}).get("connections") or []:
            value = connection.get("price")
            if isinstance(value, (int, float)) and value > 0:
                provider = str(connection.get("provider") or "Bahn")
                best[provider] = min(best.get(provider, value), value)
        rows.extend((day, "ground", _key(route.get("origin")), _key(route.get("destination")), outbound_date, provider, "", round(value * 100)) for provider, value in best.items())
    return rows


def record_search(result: dict[str, Any]) -> None:
    """Preise einer frisch berechneten Suche festhalten; darf die Suche nie stören."""
    if not ENABLED:
        return
    try:
        _write(_rows_from_result(result))
    except Exception:  # noqa: BLE001
        LOG.warning("Preisverlauf konnte nicht gespeichert werden", exc_info=True)


# ---- auswerten ----------------------------------------------------------------------------------


def _days_before(obs_day: str, travel_date: str) -> int:
    return (date.fromisoformat(travel_date) - date.fromisoformat(obs_day)).days


def route_series(origin: str, destination: str, travel_date: str, kind: str = "ground") -> dict[str, list[tuple[str, float]]]:
    """Je Anbieter die Beobachtungen (Abrufdatum, Preis) für einen Reisetag."""
    with _lock, _connect() as db:
        rows = db.execute(
            "SELECT provider, variant, obs_day, price_cents FROM observations WHERE kind = ? AND origin = ? AND destination = ? AND travel_date = ? ORDER BY obs_day",
            (kind, _key(origin), _key(destination), travel_date),
        ).fetchall()
    series: dict[str, list[tuple[str, float]]] = {}
    for provider, variant, obs_day, cents in rows:
        series.setdefault(provider + (f" ({variant})" if variant else ""), []).append((obs_day, cents / 100))
    return series


def route_overview(origin: str, destination: str, kind: str = "ground") -> dict[str, Any]:
    """Über alle beobachteten Reisetage: je Anbieter niedrigster/typischer Preis und Preis nach Tagen bis zur Abreise."""
    with _lock, _connect() as db:
        rows = db.execute(
            "SELECT provider, variant, obs_day, travel_date, price_cents FROM observations WHERE kind = ? AND origin = ? AND destination = ?",
            (kind, _key(origin), _key(destination)),
        ).fetchall()
    buckets = ((0, 3, "0–3 Tage vorher"), (4, 7, "4–7 Tage"), (8, 14, "8–14 Tage"), (15, 30, "15–30 Tage"), (31, 999, "über 30 Tage"))
    by_provider: dict[str, dict[str, Any]] = {}
    for provider, variant, obs_day, travel_date, cents in rows:
        entry = by_provider.setdefault(provider + (f" ({variant})" if variant else ""), {"prices": [], "by_bucket": {label: [] for _, _, label in buckets}, "travel_dates": set(), "first": obs_day, "last": obs_day})
        price = cents / 100
        entry["prices"].append(price)
        entry["travel_dates"].add(travel_date)
        entry["first"], entry["last"] = min(entry["first"], obs_day), max(entry["last"], obs_day)
        try:
            ahead = _days_before(obs_day, travel_date)
        except ValueError:
            continue
        for low, high, label in buckets:
            if low <= ahead <= high:
                entry["by_bucket"][label].append(price)
    return {
        provider: {
            "observations": len(entry["prices"]), "travel_dates": len(entry["travel_dates"]), "lowest": min(entry["prices"]),
            "typical": round(statistics.median(entry["prices"]), 2), "highest": max(entry["prices"]), "first_seen": entry["first"], "last_seen": entry["last"],
            "median_by_days_before": {label: round(statistics.median(values), 2) for label, values in entry["by_bucket"].items() if values},
        }
        for provider, entry in by_provider.items()
    }


# ---- Beobachtungsliste --------------------------------------------------------------------------


def add_watch(origin: str, destination: str) -> dict[str, Any]:
    with _lock, _connect() as db:
        if db.execute("SELECT COUNT(*) FROM watches").fetchone()[0] >= MAX_WATCHES:
            raise ValueError(f"Es sind höchstens {MAX_WATCHES} Strecken möglich. Bitte erst eine entfernen.")
        existing = db.execute("SELECT id FROM watches WHERE lower(origin) = ? AND lower(destination) = ?", (_key(origin), _key(destination))).fetchone()
        if existing:
            return {"id": existing[0], "origin": origin, "destination": destination}
        cursor = db.execute("INSERT INTO watches(origin, destination, created) VALUES (?, ?, ?)", (origin.strip(), destination.strip(), _today()))
        return {"id": cursor.lastrowid, "origin": origin.strip(), "destination": destination.strip()}


def list_watches() -> list[dict[str, Any]]:
    with _lock, _connect() as db:
        return [{"id": i, "origin": o, "destination": d, "created": c, "last_run": r} for i, o, d, c, r in db.execute("SELECT id, origin, destination, created, last_run FROM watches ORDER BY id")]


def remove_watch(watch_id: int) -> bool:
    with _lock, _connect() as db:
        return db.execute("DELETE FROM watches WHERE id = ?", (watch_id,)).rowcount > 0


def _mark_run(watch_id: int) -> None:
    with _lock, _connect() as db:
        db.execute("UPDATE watches SET last_run = ? WHERE id = ?", (_today(), watch_id))


async def run_watches_once() -> int:
    """Ruft jede beobachtete Strecke für einige Reisetage ab; die Preise landen über ``record_search`` im Verlauf."""
    from .models import TripRequest
    from .service import search

    done = 0
    for watch in await asyncio.to_thread(list_watches):
        for lead in WATCH_LEAD_DAYS:
            travel_date = (date.fromisoformat(_today()) + timedelta(days=lead)).isoformat()
            try:
                await search(TripRequest(
                    travel_mode="ground", journey_type="one_way", origin=watch["origin"], destination=watch["destination"], departure_date=travel_date,
                    include_hotel=False, include_feeder=False, refresh_cache=True,
                ))
                done += 1
            except Exception:  # noqa: BLE001 - eine Strecke darf die anderen nicht stoppen
                LOG.warning("Beobachtung %s → %s für %s fehlgeschlagen", watch["origin"], watch["destination"], travel_date, exc_info=True)
            await asyncio.sleep(2)
        await asyncio.to_thread(_mark_run, watch["id"])
    return done


async def watch_loop(stop_event: asyncio.Event) -> None:
    try:  # kurz nach dem Start nicht sofort loslegen
        await asyncio.wait_for(stop_event.wait(), timeout=600)
        return
    except TimeoutError:
        pass
    while not stop_event.is_set():
        try:
            await run_watches_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            LOG.exception("Preisbeobachtung fehlgeschlagen; der Dienst läuft weiter")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=WATCH_INTERVAL_HOURS * 3600)
        except TimeoutError:
            continue


def start_watch_loop() -> tuple[asyncio.Task[None], asyncio.Event] | None:
    if not ENABLED:
        return None
    stop_event = asyncio.Event()
    return asyncio.create_task(watch_loop(stop_event), name="traviorel-price-watch"), stop_event
