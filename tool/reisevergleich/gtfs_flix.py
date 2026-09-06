from __future__ import annotations

import asyncio
import copy
import csv
import io
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .config import FLIX_GTFS_DIR, FLIX_GTFS_MAX_AGE, FLIX_GTFS_TIMEOUT, FLIX_GTFS_URL, TRANSITOUS_USER_AGENT, TZ
from .db import rank_routes
from .location_resolver import CITY_ALIASES, exact_location_key, has_airport_context, location_key
from .utils import as_float, parse_datetime

LOG = logging.getLogger(__name__)
REQUIRED = {"agency.txt", "routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "calendar.txt", "calendar_dates.txt"}
GTFS_SCHEMA_VERSION = 2
MAX_ROUTE_GEOMETRY_POINTS = 300
_lock = asyncio.Lock()


def _database_current(database: Path) -> bool:
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return bool(row and json.loads(row[0]) == GTFS_SCHEMA_VERSION)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return False


def _fresh_database() -> Path | None:
    """Return the current feed without waiting for an unrelated refresh.

    Database refreshes are built in a temporary directory and installed with an
    atomic replace.  Readers can therefore safely keep using a fresh database
    while another request performs an explicit refresh.
    """
    database = Path(FLIX_GTFS_DIR) / "flix.sqlite3"
    try:
        if database.is_file() and _database_current(database) and time.time() - database.stat().st_mtime < FLIX_GTFS_MAX_AGE:
            return database
    except OSError:
        return None
    return None


def gtfs_seconds(value: str) -> int:
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Ungültige GTFS-Zeit: {value!r}")
    hours, minutes, seconds = (int(part) for part in parts)
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"Ungültige GTFS-Zeit: {value!r}")
    return hours * 3600 + minutes * 60 + seconds


def gtfs_local_datetime(service_day: date, seconds: int, agency_timezone: str | None) -> datetime:
    """Convert GTFS wall-clock seconds in the agency timezone to Traviorel local time."""
    timezone_name = str(agency_timezone or "").strip() or str(TZ)
    try:
        source_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        LOG.warning("Unbekannte GTFS-Zeitzone %r; verwende %s", timezone_name, TZ)
        source_timezone = TZ
    base = datetime.combine(service_day, datetime.min.time(), tzinfo=source_timezone)
    return (base + timedelta(seconds=seconds)).astimezone(TZ)


# Der Flix-GTFS-Feed (transitous eu_flixbus) führt viele Städte ausschließlich unter
# ihrem englischen Namen: "Munich central bus station", "Cologne", "Vienna". Eine Suche
# nach "München" trifft damit keinen einzigen Stop, und die komplette Flix-Seite des
# Vergleichs fällt still weg — die Antwort enthält dann nur noch Bahnverbindungen, ohne
# dass ein Fehler sichtbar wird.
#
# Beide Schreibweisen sind nötig: _key entfernt Diakritika ("München" -> "munchen"),
# Nutzereingaben verwenden aber oft die ASCII-Umschrift ("Muenchen").
# Aufgenommen sind nur Städte, deren deutscher Name im Feed nicht vorkommt und deren
# englischer Name dort existiert. "Rom" und "Turin" fehlen deshalb bewusst: "rom" ist im
# Feed als eigenes Token vorhanden, "Turin" ist in beiden Sprachen identisch. "Zürich"
# braucht nur die ASCII-Form, weil die Diakritika-Entfernung bereits "zurich" ergibt.
def _key(value: str) -> str:
    return location_key(value)


def _place_tokens(value: str) -> set[str]:
    ignored = {"hbf", "hauptbahnhof", "zob", "busbahnhof", "bahnhof", "station", "flixbus", "stop", "bf"}
    return {token for token in _key(value).split() if token not in ignored}


def stop_score(query: str, name: str) -> int:
    if exact_location_key(query) == exact_location_key(name):
        return 110
    query_key, name_key = _key(query), _key(name)
    if not query_key or not name_key:
        return 0
    airport_penalty = 20 if not has_airport_context(query) and has_airport_context(name) else 0
    if query_key == name_key:
        return 100 - airport_penalty
    query_tokens, name_tokens = _place_tokens(query), _place_tokens(name)
    if query_tokens and query_tokens == name_tokens:
        return 90 - airport_penalty
    if name_key.startswith(query_key + " "):
        return 80 - airport_penalty
    if query_tokens and query_tokens <= name_tokens:
        return 75 - airport_penalty
    if query_key in name_key:
        return 60 - airport_penalty
    return 0


def service_active(calendar: dict[str, str] | None, exceptions: dict[str, int], day: date) -> bool:
    key = day.strftime("%Y%m%d")
    if key in exceptions:
        return exceptions[key] == 1
    if not calendar:
        return False
    try:
        if not calendar["start_date"] <= key <= calendar["end_date"]:
            return False
        return calendar[day.strftime("%A").casefold()] == "1"
    except (KeyError, TypeError):
        return False


def _rows(archive: zipfile.ZipFile, name: str):
    with archive.open(name) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
        yield from csv.DictReader(text)


def _build_database(zip_path: Path, database_path: Path, metadata: dict[str, Any]) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        names = {Path(name).name for name in archive.namelist()}
        missing = REQUIRED - names
        if missing:
            raise ValueError(f"Unvollständiger GTFS-Feed: {', '.join(sorted(missing))}")
        with sqlite3.connect(database_path) as db:
            db.executescript("""
                PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
                CREATE TABLE agency(agency_id TEXT PRIMARY KEY, name TEXT, timezone TEXT);
                CREATE TABLE route(route_id TEXT PRIMARY KEY, agency_id TEXT, short_name TEXT, long_name TEXT, route_type INTEGER);
                CREATE TABLE trip(trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT, shape_id TEXT);
                CREATE TABLE stop(stop_id TEXT PRIMARY KEY, name TEXT, parent_station TEXT, timezone TEXT, latitude REAL, longitude REAL);
                CREATE TABLE stop_time(trip_id TEXT, stop_id TEXT, sequence INTEGER, arrival INTEGER, departure INTEGER);
                CREATE TABLE shape(shape_id TEXT, sequence INTEGER, latitude REAL, longitude REAL);
                CREATE TABLE calendar(service_id TEXT PRIMARY KEY, monday TEXT, tuesday TEXT, wednesday TEXT, thursday TEXT, friday TEXT, saturday TEXT, sunday TEXT, start_date TEXT, end_date TEXT);
                CREATE TABLE exception(service_id TEXT, date TEXT, exception_type INTEGER, PRIMARY KEY(service_id,date));
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            """)
            db.executemany("INSERT INTO agency VALUES(?,?,?)", ((r.get("agency_id", ""), r.get("agency_name", ""), r.get("agency_timezone") or "UTC") for r in _rows(archive, "agency.txt")))
            db.executemany("INSERT INTO route VALUES(?,?,?,?,?)", ((r.get("route_id", ""), r.get("agency_id", ""), r.get("route_short_name", ""), r.get("route_long_name", ""), int(r.get("route_type") or -1)) for r in _rows(archive, "routes.txt")))
            db.executemany("INSERT INTO trip VALUES(?,?,?,?)", ((r.get("trip_id", ""), r.get("route_id", ""), r.get("service_id", ""), r.get("shape_id", "")) for r in _rows(archive, "trips.txt")))
            db.executemany("INSERT INTO stop VALUES(?,?,?,?,?,?)", ((r.get("stop_id", ""), r.get("stop_name", ""), r.get("parent_station", ""), r.get("stop_timezone", ""), float(r["stop_lat"]) if r.get("stop_lat") else None, float(r["stop_lon"]) if r.get("stop_lon") else None) for r in _rows(archive, "stops.txt")))
            stop_times = []
            for r in _rows(archive, "stop_times.txt"):
                try:
                    stop_times.append((r["trip_id"], r["stop_id"], int(r["stop_sequence"]), gtfs_seconds(r["arrival_time"]), gtfs_seconds(r["departure_time"])))
                except (KeyError, TypeError, ValueError):
                    continue
                if len(stop_times) >= 25000:
                    db.executemany("INSERT INTO stop_time VALUES(?,?,?,?,?)", stop_times); stop_times.clear()
            db.executemany("INSERT INTO stop_time VALUES(?,?,?,?,?)", stop_times)
            if "shapes.txt" in names:
                shape_rows = []
                for r in _rows(archive, "shapes.txt"):
                    try:
                        shape_rows.append((r["shape_id"], int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))
                    except (KeyError, TypeError, ValueError):
                        continue
                    if len(shape_rows) >= 25000:
                        db.executemany("INSERT INTO shape VALUES(?,?,?,?)", shape_rows); shape_rows.clear()
                db.executemany("INSERT INTO shape VALUES(?,?,?,?)", shape_rows)
            weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
            db.executemany("INSERT OR REPLACE INTO calendar VALUES(?,?,?,?,?,?,?,?,?,?)", ((r.get("service_id", ""), *(r.get(day, "0") for day in weekdays), r.get("start_date", ""), r.get("end_date", "")) for r in _rows(archive, "calendar.txt")))
            db.executemany("INSERT OR REPLACE INTO exception VALUES(?,?,?)", ((r.get("service_id", ""), r.get("date", ""), int(r.get("exception_type") or 0)) for r in _rows(archive, "calendar_dates.txt")))
            metadata["schema_version"] = GTFS_SCHEMA_VERSION
            db.executemany("INSERT INTO meta VALUES(?,?)", ((key, json.dumps(value)) for key, value in metadata.items()))
            db.executescript("CREATE INDEX stop_time_stop ON stop_time(stop_id,trip_id,sequence); CREATE INDEX stop_time_trip ON stop_time(trip_id,sequence); CREATE INDEX trip_service ON trip(service_id); CREATE INDEX shape_trip ON shape(shape_id,sequence); ANALYZE;")
            if db.execute("SELECT count(*) FROM stop_time").fetchone()[0] == 0:
                raise ValueError("GTFS-Feed enthält keine gültigen stop_times")


def _refresh_sync(force: bool = False) -> Path:
    directory = Path(FLIX_GTFS_DIR); directory.mkdir(parents=True, exist_ok=True)
    database = directory / "flix.sqlite3"
    if database.exists() and not force and _database_current(database) and time.time() - database.stat().st_mtime < FLIX_GTFS_MAX_AGE:
        return database
    headers = {}
    meta_path = directory / "feed.json"
    if meta_path.exists() and _database_current(database):
        try:
            previous = json.loads(meta_path.read_text())
            if previous.get("etag"): headers["If-None-Match"] = previous["etag"]
            if previous.get("last_modified"): headers["If-Modified-Since"] = previous["last_modified"]
        except (OSError, ValueError):
            pass
    try:
        with httpx.Client(timeout=httpx.Timeout(FLIX_GTFS_TIMEOUT, connect=min(10, FLIX_GTFS_TIMEOUT)), follow_redirects=True, headers={"User-Agent": TRANSITOUS_USER_AGENT, "Accept": "application/zip"}) as client:
            response = client.get(FLIX_GTFS_URL, headers=headers)
        if response.status_code == 304 and database.exists():
            os.utime(database, None); return database
        response.raise_for_status()
        if len(response.content) < 1024:
            raise ValueError("GTFS-Download ist unerwartet klein")
        metadata = {"url": FLIX_GTFS_URL, "etag": response.headers.get("etag"), "last_modified": response.headers.get("last-modified"), "downloaded_at": datetime.now(timezone.utc).isoformat()}
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            zip_path = Path(temporary) / "feed.zip"; zip_path.write_bytes(response.content)
            new_database = Path(temporary) / "feed.sqlite3"
            _build_database(zip_path, new_database, metadata)
            os.replace(new_database, database)
        temporary_meta = directory / "feed.json.tmp"
        temporary_meta.write_text(json.dumps(metadata, indent=2)); os.replace(temporary_meta, meta_path)
    except Exception:
        if database.exists():
            LOG.exception("Flix-GTFS-Aktualisierung fehlgeschlagen; letzter gültiger Feed bleibt aktiv")
            return database
        raise
    return database


async def ensure_feed(force: bool = False) -> Path:
    if not force:
        database = _fresh_database()
        if database is not None:
            return database
    async with _lock:
        return await asyncio.to_thread(_refresh_sync, force)


def _downsample_coordinates(points: list[list[float]]) -> list[list[float]]:
    if len(points) <= MAX_ROUTE_GEOMETRY_POINTS:
        return points
    step = (len(points) - 1) / (MAX_ROUTE_GEOMETRY_POINTS - 1)
    return [points[round(index * step)] for index in range(MAX_ROUTE_GEOMETRY_POINTS)]


def _attach_gtfs_path(database: Path, route: dict[str, Any]) -> None:
    trip_id = str(route.get("_trip_id") or "")
    origin_sequence = int(route.get("_origin_sequence") or 0)
    destination_sequence = int(route.get("_destination_sequence") or 0)
    if not trip_id or destination_sequence <= origin_sequence:
        return
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        stops = list(db.execute(
            """SELECT s.stop_id,s.name,s.latitude,s.longitude,st.sequence
               FROM stop_time st JOIN stop s ON s.stop_id=st.stop_id
               WHERE st.trip_id=? AND st.sequence BETWEEN ? AND ? ORDER BY st.sequence""",
            (trip_id, origin_sequence, destination_sequence),
        ))
        shape_id = str(route.get("_shape_id") or "")
        shapes = list(db.execute(
            "SELECT latitude,longitude FROM shape WHERE shape_id=? ORDER BY sequence", (shape_id,),
        )) if shape_id else []
    stopovers = [
        {
            "name": row["name"],
            "id": row["stop_id"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
        }
        for row in stops
    ]
    if route.get("legs") and stopovers:
        route["legs"][0]["stopovers"] = stopovers
    if len(shapes) < 2 or len(stops) < 2:
        return
    points = [[float(row["longitude"]), float(row["latitude"])] for row in shapes]

    def nearest(stop: sqlite3.Row) -> int:
        latitude, longitude = float(stop["latitude"]), float(stop["longitude"])
        return min(range(len(points)), key=lambda index: (points[index][0] - longitude) ** 2 + (points[index][1] - latitude) ** 2)

    if any(row["latitude"] is None or row["longitude"] is None for row in (stops[0], stops[-1])):
        return
    start, end = nearest(stops[0]), nearest(stops[-1])
    segment = points[start:end + 1] if start <= end else list(reversed(points[end:start + 1]))
    if len(segment) >= 2:
        route["geometry"] = {"type": "LineString", "coordinates": _downsample_coordinates(segment)}


def _search_sync(database: Path, request) -> dict[str, Any]:
    requested_day = date.fromisoformat(request.travel_date)
    requested_local = datetime.combine(requested_day, datetime.strptime(request.departure_after, "%H:%M").time(), tzinfo=TZ)
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        stops = list(db.execute("SELECT stop_id,name,parent_station,timezone FROM stop"))
        origins = sorted(((stop_score(request.origin, r["name"]), r) for r in stops), reverse=True, key=lambda pair: pair[0])
        destinations = sorted(((stop_score(request.destination, r["name"]), r) for r in stops), reverse=True, key=lambda pair: pair[0])
        origin_selection = getattr(request, "origin_station", None)
        destination_selection = getattr(request, "destination_station", None)
        selected_origin = origin_selection if origin_selection and origin_selection.id_for("flix") else None
        selected_destination = destination_selection if destination_selection and destination_selection.id_for("flix") else None
        explicit_origin_ids = selected_origin.ids_for("flix") if selected_origin else ([getattr(request, "flix_origin_stop_id", None)] if getattr(request, "flix_origin_stop_id", None) else [])
        explicit_destination_ids = selected_destination.ids_for("flix") if selected_destination else ([getattr(request, "flix_destination_stop_id", None)] if getattr(request, "flix_destination_stop_id", None) else [])

        def stop_families(stop_ids: list[str]) -> list[str]:
            """Include a GTFS station, its parent and every child of that parent."""
            expanded = {str(stop_id) for stop_id in stop_ids if stop_id}
            pending = list(expanded)
            while pending:
                stop_id = pending.pop()
                row = db.execute("SELECT parent_station FROM stop WHERE stop_id=?", (stop_id,)).fetchone()
                parent = str(row[0]) if row and row[0] else stop_id
                if parent not in expanded:
                    expanded.add(parent); pending.append(parent)
                expanded.update(str(child[0]) for child in db.execute("SELECT stop_id FROM stop WHERE parent_station=?", (parent,)))
            return sorted(expanded)

        origin_ids = stop_families(explicit_origin_ids) if explicit_origin_ids else ([r["stop_id"] for score, r in origins if score >= max(60, origins[0][0] - 20)][:24] if origins and origins[0][0] else [])
        destination_ids = stop_families(explicit_destination_ids) if explicit_destination_ids else ([r["stop_id"] for score, r in destinations if score >= max(60, destinations[0][0] - 20)][:24] if destinations and destinations[0][0] else [])
        if not origin_ids or not destination_ids:
            return {"status": "empty", "routes": [], "candidate_routes": [], "provider_status": {"provider": "flix-gtfs", "ok": True, "error": "Start oder Ziel nicht im Flix-GTFS gefunden"}}
        placeholders_o, placeholders_d = ",".join("?" * len(origin_ids)), ",".join("?" * len(destination_ids))
        sql = f"""SELECT t.trip_id,t.service_id,t.shape_id,r.route_id,r.agency_id,r.short_name,r.long_name,r.route_type,a.timezone,
          so.name origin_name,sd.name destination_name,o.departure departure_seconds,d.arrival arrival_seconds,o.sequence origin_sequence,d.sequence destination_sequence
          FROM stop_time o JOIN stop_time d ON d.trip_id=o.trip_id AND d.sequence>o.sequence
          JOIN trip t ON t.trip_id=o.trip_id JOIN route r ON r.route_id=t.route_id JOIN agency a ON a.agency_id=r.agency_id
          JOIN stop so ON so.stop_id=o.stop_id JOIN stop sd ON sd.stop_id=d.stop_id
          WHERE o.stop_id IN ({placeholders_o}) AND d.stop_id IN ({placeholders_d}) ORDER BY o.departure LIMIT 1000"""
        candidates = list(db.execute(sql, (*origin_ids, *destination_ids)))
        services = {r["service_id"] for r in candidates}
        calendars = {r["service_id"]: dict(r) for r in db.execute(f"SELECT * FROM calendar WHERE service_id IN ({','.join('?' * len(services))})", tuple(services))} if services else {}
        exceptions: dict[tuple[str, str], int] = {}
        if services:
            days = ((requested_day - timedelta(days=1)).strftime('%Y%m%d'), requested_day.strftime('%Y%m%d'))
            for row in db.execute(f"SELECT * FROM exception WHERE service_id IN ({','.join('?' * len(services))}) AND date IN (?,?)", (*services, *days)):
                exceptions[(row["service_id"], row["date"])] = row["exception_type"]
    output = []
    seen = set()
    for row in candidates:
      for service_day in (requested_day - timedelta(days=1), requested_day):
        day_key = service_day.strftime('%Y%m%d')
        day_exceptions = {day_key: exceptions[(row["service_id"], day_key)]} if (row["service_id"], day_key) in exceptions else {}
        if not service_active(calendars.get(row["service_id"]), day_exceptions, service_day): continue
        agency = row["agency_id"].upper(); route_type = row["route_type"]
        kind = "train" if "FLIXTRAIN" in agency or route_type in {2, 100, 101, 102, 103, 106} else "bus"
        if kind == "train" and not request.include_flixtrain or kind == "bus" and not request.include_flixbus: continue
        departure = gtfs_local_datetime(service_day, row["departure_seconds"], row["timezone"])
        arrival = gtfs_local_datetime(service_day, row["arrival_seconds"], row["timezone"])
        if departure < requested_local or departure >= requested_local + timedelta(hours=36): continue
        fingerprint = (row["trip_id"], departure.isoformat(), arrival.isoformat())
        if fingerprint in seen: continue
        seen.add(fingerprint)
        provider = "FlixTrain" if kind == "train" else "FlixBus"
        line = row["short_name"] or provider
        output.append({"id": f"gtfs-{row['trip_id']}-{row['origin_sequence']}-{day_key}", "provider": provider, "provider_code": "flix", "flix_kind": kind, "type": kind, "line": line, "origin": row["origin_name"], "destination": row["destination_name"], "departure": departure.isoformat(), "arrival": arrival.isoformat(), "duration_minutes": int((arrival-departure).total_seconds()//60), "transfers": 0, "price": None, "price_complete": False, "price_note": "Fahrplandaten aus GTFS; kein Live-Preis verfügbar.", "deutschlandticket_covered": False, "booking_url": "https://global.flixbus.com/", "legs": [{"provider": provider, "mode": kind, "line": line, "origin": row["origin_name"], "destination": row["destination_name"], "departure": departure.isoformat(), "arrival": arrival.isoformat()}], "gtfs_service_id": row["service_id"], "gtfs_route_id": row["route_id"], "_trip_id": row["trip_id"], "_shape_id": row["shape_id"], "_origin_sequence": row["origin_sequence"], "_destination_sequence": row["destination_sequence"]})
    ranked = sorted(output, key=lambda route: (route["departure"], route["arrival"], route["provider"]))
    for route in ranked[:request.max_results]:
        _attach_gtfs_path(database, route)
    for route in ranked:
        for key in ("_trip_id", "_shape_id", "_origin_sequence", "_destination_sequence"):
            route.pop(key, None)
    return {"status": "ok" if ranked else "empty", "routes": ranked[:request.max_results], "candidate_routes": ranked, "candidate_counts": {"train": sum(r["flix_kind"]=="train" for r in ranked), "bus": sum(r["flix_kind"]=="bus" for r in ranked), "mixed": 0}, "provider_status": {"provider": "flix-gtfs", "ok": True, "route_count": len(ranked)}, "error": None}


async def search(request, *, force_refresh: bool = False) -> dict[str, Any]:
    started = time.monotonic()
    try:
        database = await ensure_feed(force_refresh)
        result = await asyncio.to_thread(_search_sync, database, request)
        result["provider_status"]["duration_ms"] = round((time.monotonic()-started)*1000)
        return result
    except Exception as exc:
        LOG.exception("Flix-GTFS-Suche fehlgeschlagen")
        return {"status": "failed", "routes": [], "candidate_routes": [], "provider_status": {"provider": "flix-gtfs", "ok": False, "error": f"{type(exc).__name__}: {exc}"}, "error": str(exc)}


def _stop_suggestions_sync(database: Path, query: str) -> list[dict[str, Any]]:
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT stop_id,name,timezone,latitude,longitude,parent_station FROM stop").fetchall()
    ranked = sorted(((stop_score(query, row[1]), row) for row in rows), reverse=True, key=lambda item: (item[0], item[1][1]))
    output, names = [], set()
    for score, row in ranked:
        normalized = _key(row[1])
        if score < 60 or normalized in names: continue
        names.add(normalized)
        output.append({"station_id": row[0], "name": row[1], "timezone": row[2], "latitude": row[3], "longitude": row[4], "parent_station": row[5] or None, "location_type": 0 if row[5] else 1})
        if len(output) >= 12: break
    return output


async def discover_stops(origin: str, destination: str) -> dict[str, Any]:
    try:
        database = await ensure_feed()
        origin_stops, destination_stops = await asyncio.gather(
            asyncio.to_thread(_stop_suggestions_sync, database, origin),
            asyncio.to_thread(_stop_suggestions_sync, database, destination),
        )
        return {"origin_stops": origin_stops, "destination_stops": destination_stops, "source": "flix-gtfs"}
    except Exception as exc:
        return {"origin_stops": [], "destination_stops": [], "source": "flix-gtfs", "error": f"{type(exc).__name__}: {exc}"}


def _live_stop_ids(data: dict[str, Any]) -> set[str]:
    station_ids: set[str] = set()
    for collection in (data.get("candidate_routes"), data.get("routes")):
        for route in collection or []:
            if not isinstance(route, dict):
                continue
            for leg in route.get("legs") or []:
                if not isinstance(leg, dict):
                    continue
                for side in ("departure", "arrival"):
                    stop = leg.get(side)
                    if not isinstance(stop, dict):
                        continue
                    station_id = str(stop.get("station_id") or stop.get("station") or "").strip()
                    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}", station_id):
                        station_ids.add(station_id)
    return station_ids


def _gtfs_stop_directory(database: Path, station_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not station_ids:
        return {}
    placeholders = ",".join("?" for _ in station_ids)
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        rows = list(db.execute(
            f"SELECT stop_id,name,latitude,longitude FROM stop WHERE stop_id IN ({placeholders})",
            tuple(sorted(station_ids)),
        ))
    return {
        str(row["stop_id"]): {
            "station": row["name"],
            "station_id": row["stop_id"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
        }
        for row in rows
    }


async def normalize_live_routes(
    live: dict[str, Any], *, database: Path | None = None,
) -> dict[str, Any]:
    """Normalize trvl's Flix legs against the authoritative Flix GTFS stops.

    trvl exposes intermediate stops as UUIDs while the autocomplete endpoint
    only resolves place-name searches.  The GTFS feed uses the same UUIDs, so
    it is the deterministic source for leg endpoints.  Route classification
    remains authoritative for the operator shown on every leg.
    """
    output = copy.deepcopy(live)
    station_ids = _live_stop_ids(output)
    if database is None and station_ids:
        try:
            database = await ensure_feed()
        except Exception:
            database = None
    try:
        directory = await asyncio.to_thread(_gtfs_stop_directory, database, station_ids) if database else {}
    except (OSError, sqlite3.Error):
        LOG.exception("Flix-Live-Haltepunkte konnten nicht aus GTFS aufgelöst werden")
        directory = {}

    visited: set[int] = set()
    for collection in (output.get("candidate_routes"), output.get("routes")):
        for route in collection or []:
            if not isinstance(route, dict) or id(route) in visited:
                continue
            visited.add(id(route))
            kind = str(route.get("flix_kind") or route.get("type") or "").casefold()
            operator = "FlixTrain" if kind == "train" else "FlixBus" if kind == "bus" else "FlixBus/FlixTrain"
            for leg in route.get("legs") or []:
                if not isinstance(leg, dict):
                    continue
                leg_kind = str(leg.get("type") or leg.get("mode") or kind).casefold() or kind
                leg["mode"] = leg_kind
                leg["provider"] = "FlixTrain" if leg_kind == "train" else "FlixBus" if leg_kind == "bus" else operator
                for side in ("departure", "arrival"):
                    stop = leg.get(side)
                    if not isinstance(stop, dict):
                        continue
                    station_id = str(stop.get("station_id") or stop.get("station") or "").strip()
                    if station_id in directory:
                        stop.update({key: value for key, value in directory[station_id].items() if value not in (None, "")})
                    elif station_id in station_ids:
                        stop["station_id"] = station_id
    return output


def enrich_live_prices(schedule: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """Merge validated live Flix journeys and enrich matching GTFS journeys.

    The GTFS query intentionally models direct trips only.  Flix live results
    may contain valid transfer itineraries (for example Leipzig–Dresden–
    Görlitz), so they must remain available even without a same-trip GTFS row.
    """
    def route_time(route: dict[str, Any], side: str):
        value = route.get(side)
        if isinstance(value, dict): value = value.get("time")
        return parse_datetime(value)

    live_routes = live.get("candidate_routes") or live.get("routes") or []
    by_match: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for route in live_routes:
        departure, arrival = route_time(route, "departure"), route_time(route, "arrival")
        price = as_float(route.get("price"))
        kind = str(route.get("flix_kind") or route.get("type") or "").casefold()
        if not departure or not arrival or price <= 0 or kind not in {"bus", "train"}:
            continue
        key = (kind, departure.astimezone(TZ).isoformat(timespec="minutes"), arrival.astimezone(TZ).isoformat(timespec="minutes"))
        by_match.setdefault(key, []).append(route)

    enriched = 0
    candidates = schedule.get("candidate_routes") or []
    for route in candidates:
        departure, arrival = route_time(route, "departure"), route_time(route, "arrival")
        if not departure or not arrival: continue
        key = (str(route.get("flix_kind") or "").casefold(), departure.astimezone(TZ).isoformat(timespec="minutes"), arrival.astimezone(TZ).isoformat(timespec="minutes"))
        matches = by_match.get(key) or []
        if len(matches) != 1: continue
        match = matches[0]
        route["price"] = round(as_float(match.get("price")), 2)
        route["currency"] = match.get("currency") or "EUR"
        route["price_complete"] = True
        route["price_note"] = "Livepreis aus der Flix-Such-API; vor Buchung erneut prüfen."
        if match.get("booking_url"): route["booking_url"] = match["booking_url"]
        enriched += 1

    def fingerprint(route: dict[str, Any]) -> tuple[str, str, str]:
        departure, arrival = route_time(route, "departure"), route_time(route, "arrival")
        return (
            str(route.get("flix_kind") or route.get("type") or "").casefold(),
            departure.astimezone(TZ).isoformat(timespec="minutes") if departure else "",
            arrival.astimezone(TZ).isoformat(timespec="minutes") if arrival else "",
        )

    known = {fingerprint(route) for route in candidates}
    live_added = 0
    if (live.get("provider_status") or {}).get("ok") is True:
        for route in live_routes:
            key = fingerprint(route)
            if not all(key) or key in known:
                continue
            candidates.append(dict(route))
            known.add(key)
            live_added += 1

    candidates = rank_routes(candidates, "balanced")
    schedule["candidate_routes"] = candidates
    visible_limit = max(len(schedule.get("routes") or []), len(live.get("routes") or []))
    schedule["routes"] = candidates[:visible_limit]
    counts = {
        kind: sum(str(route.get("flix_kind") or route.get("type") or "").casefold() == kind for route in candidates)
        for kind in ("train", "bus", "mixed")
    }
    schedule["candidate_counts"] = counts
    schedule["status"] = "ok" if candidates else ("failed" if schedule.get("status") == "failed" else "empty")
    schedule.setdefault("provider_status", {})["live_pricing"] = {
        "provider": "flix-api", "ok": (live.get("provider_status") or {}).get("ok") is True,
        "matched_prices": enriched, "candidate_prices": sum(len(items) for items in by_match.values()),
        "live_routes_added": live_added,
        "error": (live.get("provider_status") or {}).get("error"),
    }
    return schedule
