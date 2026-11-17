from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from .keyed_locks import KeyedAsyncLocks

CACHE_SCHEMA = 5
DEFAULT_DB = "/var/lib/reisevergleich/cache.sqlite3"
_stats_var: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar("reise_cache_stats", default=None)
_refresh_var: contextvars.ContextVar[bool] = contextvars.ContextVar("reise_cache_refresh", default=False)
_refreshed_keys_var: contextvars.ContextVar[set[str] | None] = contextvars.ContextVar("reise_cache_refreshed_keys", default=None)
_locks = KeyedAsyncLocks()


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _key(namespace: str, value: Any) -> str:
    return hashlib.sha256((namespace + "\0" + _json(value)).encode("utf-8")).hexdigest()


def _db_path() -> Path:
    requested = Path(os.getenv("REISE_CACHE_DB", DEFAULT_DB))
    try:
        requested.parent.mkdir(parents=True, exist_ok=True)
        with requested.parent.joinpath(".write-test").open("w") as fh:
            fh.write("ok")
        requested.parent.joinpath(".write-test").unlink(missing_ok=True)
        return requested
    except OSError:
        fallback = Path("/tmp/reisevergleich/cache.sqlite3")
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(_db_path(), timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("""
        CREATE TABLE IF NOT EXISTS component_cache (
            cache_key TEXT PRIMARY KEY,
            namespace TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            schema_version INTEGER NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS component_cache_expiry ON component_cache(expires_at)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS journeys (
            journey_id TEXT PRIMARY KEY,
            request_key TEXT NOT NULL UNIQUE,
            request_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            schema_version INTEGER NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS journeys_expiry ON journeys(expires_at)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS history_detail_cache (
            cache_key TEXT PRIMARY KEY, train_type TEXT NOT NULL, train_number TEXT NOT NULL,
            year INTEGER NOT NULL, month INTEGER NOT NULL, file_path TEXT NOT NULL,
            row_count INTEGER NOT NULL, file_size INTEGER NOT NULL, created_at REAL NOT NULL,
            updated_at REAL NOT NULL, last_accessed REAL NOT NULL, source TEXT NOT NULL,
            source_revision TEXT NOT NULL, schema_version INTEGER NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS history_detail_cache_lru ON history_detail_cache(last_accessed)")
    db.commit()
    return db


def begin_scope(*, refresh: bool = False) -> tuple[contextvars.Token, contextvars.Token, contextvars.Token]:
    stats_token = _stats_var.set({"component_hits": 0, "component_misses": 0, "journey_hits": 0})
    refresh_token = _refresh_var.set(bool(refresh))
    refreshed_token = _refreshed_keys_var.set(set())
    return stats_token, refresh_token, refreshed_token


def end_scope(token: tuple[contextvars.Token, contextvars.Token, contextvars.Token]) -> None:
    stats_token, refresh_token, refreshed_token = token
    _refreshed_keys_var.reset(refreshed_token)
    _refresh_var.reset(refresh_token)
    _stats_var.reset(stats_token)


def stats() -> dict[str, int]:
    return dict(_stats_var.get() or {"component_hits": 0, "component_misses": 0, "journey_hits": 0})


def _bump(name: str) -> None:
    current = _stats_var.get()
    if current is not None:
        current[name] = current.get(name, 0) + 1


def _get_component_sync(namespace: str, key_data: Any) -> Any | None:
    now = time.time()
    key = _key(namespace, key_data)
    with _connect() as db:
        row = db.execute(
            "SELECT payload FROM component_cache WHERE cache_key=? AND expires_at>? AND schema_version=?",
            (key, now, CACHE_SCHEMA),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])


def _set_component_sync(namespace: str, key_data: Any, value: Any, ttl: int) -> None:
    now = time.time()
    with _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO component_cache(cache_key,namespace,payload,created_at,expires_at,schema_version) VALUES(?,?,?,?,?,?)",
            (_key(namespace, key_data), namespace, _json(value), now, now + ttl, CACHE_SCHEMA),
        )
        db.execute("DELETE FROM component_cache WHERE expires_at<=?", (now,))
        db.commit()


DEGRADED_STATUSES = frozenset({"failed", "manual_required", "partial", "unavailable"})


def _cacheable_component(value: Any) -> bool:
    # Some producers return (result, diagnostics). A failed DB search came back as a tuple,
    # so the dict-only check never saw its status and cached it for every client.
    if isinstance(value, tuple) and value:
        value = value[0]
    return not (isinstance(value, dict) and value.get("status") in DEGRADED_STATUSES)


async def cached_call(
    namespace: str,
    key_data: Any,
    ttl: int,
    producer: Callable[[], Awaitable[Any]],
    *,
    refresh: bool = False,
) -> Any:
    key = _key(namespace, key_data)
    effective_refresh = bool(refresh or _refresh_var.get())
    refreshed_keys = _refreshed_keys_var.get()
    already_refreshed = bool(refreshed_keys is not None and key in refreshed_keys)

    if not effective_refresh or already_refreshed:
        cached = await asyncio.to_thread(_get_component_sync, namespace, key_data)
        if cached is not None:
            _bump("component_hits")
            return cached

    async with _locks.hold(key):
        refreshed_keys = _refreshed_keys_var.get()
        already_refreshed = bool(refreshed_keys is not None and key in refreshed_keys)
        if not effective_refresh or already_refreshed:
            cached = await asyncio.to_thread(_get_component_sync, namespace, key_data)
            if cached is not None:
                _bump("component_hits")
                return cached

        _bump("component_misses")
        value = await producer()
        if _cacheable_component(value):
            await asyncio.to_thread(_set_component_sync, namespace, key_data, value, ttl)
            refreshed_keys = _refreshed_keys_var.get()
            if refreshed_keys is not None:
                refreshed_keys.add(key)
        return value


def _journey_request_data(request: Any) -> dict[str, Any]:
    data = request.model_dump(exclude_none=True) if hasattr(request, "model_dump") else dict(request)
    data.pop("refresh_cache", None)
    data.pop("journey_id", None)
    return data


def _get_journey_sync(request_data: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    now = time.time()
    request_key = _key("journey", request_data)
    with _connect() as db:
        row = db.execute(
            "SELECT journey_id,result_json FROM journeys WHERE request_key=? AND expires_at>? AND schema_version=?",
            (request_key, now, CACHE_SCHEMA),
        ).fetchone()
        if row is None:
            return None
        result = json.loads(row[1])
        # Rows written before degraded results stopped being saved must not be served.
        if isinstance(result, dict) and result.get("status") in DEGRADED_STATUSES:
            return None
        return row[0], result


def _save_journey_sync(request_data: dict[str, Any], result: dict[str, Any], ttl: int) -> str:
    now = time.time()
    request_key = _key("journey", request_data)
    with _connect() as db:
        row = db.execute("SELECT journey_id,created_at FROM journeys WHERE request_key=?", (request_key,)).fetchone()
        journey_id = row[0] if row else uuid.uuid4().hex
        created = row[1] if row else now
        db.execute(
            "INSERT OR REPLACE INTO journeys(journey_id,request_key,request_json,result_json,created_at,updated_at,expires_at,schema_version) VALUES(?,?,?,?,?,?,?,?)",
            (journey_id, request_key, _json(request_data), _json(result), created, now, now + ttl, CACHE_SCHEMA),
        )
        db.execute("DELETE FROM journeys WHERE expires_at<=?", (now,))
        db.commit()
        return journey_id


async def get_cached_journey(request: Any) -> tuple[str, dict[str, Any]] | None:
    hit = await asyncio.to_thread(_get_journey_sync, _journey_request_data(request))
    if hit is not None:
        _bump("journey_hits")
    return hit


async def save_journey(request: Any, result: dict[str, Any], ttl: int = 1800) -> str:
    return await asyncio.to_thread(_save_journey_sync, _journey_request_data(request), result, ttl)


def _health_sync() -> dict[str, Any]:
    path = _db_path()
    with _connect() as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ok", "path": str(path), "schema": CACHE_SCHEMA}


async def health() -> dict[str, Any]:
    return await asyncio.to_thread(_health_sync)


def history_detail_get(cache_key: str) -> dict[str, Any] | None:
    now = time.time()
    with _connect() as db:
        row = db.execute(
            "SELECT cache_key,train_type,train_number,year,month,file_path,row_count,file_size,created_at,updated_at,last_accessed,source,source_revision,schema_version FROM history_detail_cache WHERE cache_key=?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        db.execute("UPDATE history_detail_cache SET last_accessed=? WHERE cache_key=?", (now, cache_key))
        db.commit()
    names = ("cache_key", "train_type", "train_number", "year", "month", "file_path", "row_count", "file_size", "created_at", "updated_at", "last_accessed", "source", "source_revision", "schema_version")
    return dict(zip(names, row, strict=True))


def history_detail_put(metadata: dict[str, Any]) -> None:
    now = time.time()
    with _connect() as db:
        db.execute(
            "INSERT INTO history_detail_cache(cache_key,train_type,train_number,year,month,file_path,row_count,file_size,created_at,updated_at,last_accessed,source,source_revision,schema_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET file_path=excluded.file_path,row_count=excluded.row_count,file_size=excluded.file_size,updated_at=excluded.updated_at,last_accessed=excluded.last_accessed,source=excluded.source,source_revision=excluded.source_revision,schema_version=excluded.schema_version",
            (metadata["cache_key"], metadata["train_type"], metadata["train_number"], metadata["year"], metadata["month"], metadata["file_path"], metadata["row_count"], metadata["file_size"], now, now, now, metadata["source"], metadata["source_revision"], metadata["schema_version"]),
        )
        db.commit()


def history_detail_entries() -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT cache_key,file_path,file_size,last_accessed FROM history_detail_cache ORDER BY last_accessed").fetchall()
    return [dict(zip(("cache_key", "file_path", "file_size", "last_accessed"), row, strict=True)) for row in rows]


def history_detail_delete(cache_key: str) -> None:
    with _connect() as db:
        db.execute("DELETE FROM history_detail_cache WHERE cache_key=?", (cache_key,))
        db.commit()


def history_stats_snapshot_entries() -> list[dict[str, Any]]:
    """Return completed, unexpired reliability results for the background archiver."""
    now = time.time()
    with _connect() as db:
        rows = db.execute(
            "SELECT cache_key,payload,created_at FROM component_cache "
            "WHERE namespace=? AND expires_at>? AND schema_version=? ORDER BY cache_key",
            ("history.stats", now, CACHE_SCHEMA),
        ).fetchall()
    output: list[dict[str, Any]] = []
    for cache_key, payload, updated_at in rows:
        try:
            value = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("status") in {"ok", "insufficient_data"}:
            output.append({"cache_key": cache_key, "payload": value, "updated_at": updated_at})
    return output
