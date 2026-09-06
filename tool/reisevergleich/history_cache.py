from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from curl_cffi import requests as curl_requests

from . import cache
from .config import (
    HISTORY_CACHE_DIR, HISTORY_CACHE_MAX_GB, HISTORY_MAX_CONCURRENCY,
    HISTORY_REMOTE_TIMEOUT, HISTORY_SNAPSHOT_RETENTION_DAYS, HISTORY_SOURCE_REVISION, TZ,
)

SOURCE = "piebro/deutsche-bahn-data"
DETAIL_SCHEMA = 1
DETAIL_COLUMNS = (
    "id", "station_name", "xml_station_name", "eva", "train_number", "line_number",
    "final_destination_station", "time", "is_canceled", "train_type", "train_line_ride_id",
    "train_line_station_num", "arrival_planned_time", "arrival_change_time",
    "departure_planned_time", "departure_change_time",
)
_MONTH_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")
_locks: dict[str, asyncio.Lock] = {}
_active: set[str] = set()
_reading: set[str] = set()
_guard = threading.Lock()
_remote_fill_limiter = threading.BoundedSemaphore(max(1, HISTORY_MAX_CONCURRENCY))
_history_executor = ThreadPoolExecutor(
    max_workers=max(1, HISTORY_MAX_CONCURRENCY), thread_name_prefix="traviorel-history",
)
_snapshot_locks: dict[str, threading.Lock] = {}
_snapshot_locks_guard = threading.Lock()
_SNAPSHOT_SCHEMA = 1
_SECRET_KEYS = {"authorization", "cookie", "password", "secret", "token", "api_key", "apikey"}
_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class DetailSpec:
    train_type: str
    train_number: str
    year: int
    month: int

    @property
    def month_id(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def _snapshot_key(snapshot_id: str) -> str:
    value = str(snapshot_id).strip()
    if not value or len(value) > 160 or not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        raise ValueError("invalid history snapshot id")
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:48]
    return f"{label}-{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _snapshot_root(snapshot_id: str) -> Path:
    root = (Path(HISTORY_CACHE_DIR).resolve() / "snapshots").resolve()
    target = root / _snapshot_key(snapshot_id)
    if root not in target.resolve(strict=False).parents:
        raise ValueError("history snapshot path escapes configured root")
    return target


def _snapshot_lock(snapshot_id: str) -> threading.Lock:
    key = _snapshot_key(snapshot_id)
    with _snapshot_locks_guard:
        return _snapshot_locks.setdefault(key, threading.Lock())


def _validate_snapshot_payload(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
            if normalized in _SECRET_KEYS or any(part in _SECRET_KEYS for part in normalized.split("_")):
                raise ValueError(f"secret-like field is not allowed in history snapshots: {'.'.join((*path, str(key)))}")
            _validate_snapshot_payload(item, (*path, str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_snapshot_payload(item, (*path, str(index)))
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise TypeError(f"history snapshot value is not JSON-safe at {'.'.join(path)}")


def prune_daily_snapshots(*, today: date | None = None) -> int:
    """Keep the current day plus the previous retention-1 calendar days."""
    snapshots_root = Path(HISTORY_CACHE_DIR).resolve() / "snapshots"
    if not snapshots_root.is_dir() or snapshots_root.is_symlink():
        return 0
    cutoff = (today or datetime.now(TZ).date()) - timedelta(days=HISTORY_SNAPSHOT_RETENTION_DAYS - 1)
    removed = 0
    for path in snapshots_root.glob("*/*.json"):
        try:
            snapshot_day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if snapshot_day >= cutoff or path.is_symlink():
            continue
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    return removed


def save_daily_snapshot(
    snapshot_id: str, payload: dict[str, Any], *, observed_at: datetime | None = None,
) -> Path:
    """Atomically store one JSON-safe, secret-free observation per local calendar day."""
    if not isinstance(payload, dict):
        raise TypeError("history snapshot payload must be an object")
    _validate_snapshot_payload(payload)
    observed = observed_at or datetime.now(TZ)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=TZ)
    observed = observed.astimezone(TZ)
    document = {
        "schema_version": _SNAPSHOT_SCHEMA, "snapshot_id": snapshot_id,
        "snapshot_date": observed.date().isoformat(), "observed_at": observed.isoformat(),
        "payload": payload,
    }
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_SNAPSHOT_BYTES:
        raise ValueError("history snapshot exceeds size limit")
    directory = _snapshot_root(snapshot_id)
    target = directory / f"{observed.date().isoformat()}.json"
    with _snapshot_lock(snapshot_id):
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise RuntimeError("unsafe_history_snapshot_path")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
    prune_daily_snapshots(today=observed.date())
    return target


def load_daily_snapshots(
    snapshot_id: str, *, start_date: date | None = None, end_date: date | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    prune_daily_snapshots(today=today)
    directory = _snapshot_root(snapshot_id)
    if not directory.is_dir() or directory.is_symlink():
        return []
    output: list[dict[str, Any]] = []
    with _snapshot_lock(snapshot_id):
        for path in sorted(directory.glob("*.json")):
            try:
                snapshot_day = date.fromisoformat(path.stem)
                if (start_date and snapshot_day < start_date) or (end_date and snapshot_day > end_date):
                    continue
                document = json.loads(path.read_text(encoding="utf-8"))
                if (
                    not isinstance(document, dict) or document.get("schema_version") != _SNAPSHOT_SCHEMA
                    or document.get("snapshot_id") != snapshot_id or document.get("snapshot_date") != path.stem
                    or not isinstance(document.get("payload"), dict)
                ):
                    raise ValueError("invalid history snapshot document")
                _validate_snapshot_payload(document["payload"])
                output.append(document)
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
    return output


def detail_cache_key(spec: DetailSpec) -> str:
    raw = f"{DETAIL_SCHEMA}\0{spec.train_type}\0{spec.train_number}\0{spec.month_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _safe_segment(value: str) -> str:
    label = re.sub(r"[^A-Z0-9._-]+", "_", value.upper()).strip("._")
    if not label:
        raise ValueError("empty cache path segment")
    return f"{label[:48]}-{hashlib.sha256(value.encode()).hexdigest()[:10]}"


def detail_path(spec: DetailSpec) -> Path:
    root = Path(HISTORY_CACHE_DIR).resolve()
    path = root / _safe_segment(spec.train_type) / _safe_segment(spec.train_number) / f"{spec.month_id}.parquet"
    if root not in path.resolve(strict=False).parents:
        raise ValueError("history cache path escapes configured root")
    return path


def remote_urls(spec: DetailSpec) -> tuple[str, str]:
    if not _MONTH_RE.fullmatch(spec.month_id):
        raise ValueError("invalid history month")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", HISTORY_SOURCE_REVISION):
        raise ValueError("invalid history source revision")
    relative = f"monthly_processed_data/data-{spec.month_id}.parquet"
    return (
        f"hf://datasets/{SOURCE}/{relative}",
        f"https://huggingface.co/datasets/{SOURCE}/resolve/{HISTORY_SOURCE_REVISION}/{relative}",
    )


def _duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb_unavailable") from exc
    return duckdb


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def valid_parquet(path: Path) -> tuple[bool, int]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 8:
        return False, 0
    try:
        con = _duckdb().connect(":memory:")
        try:
            count = int(con.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0])
            names = {row[0] for row in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()}
        finally:
            con.close()
    except Exception:
        return False, 0
    return set(DETAIL_COLUMNS).issubset(names), count


def _copy_filtered(source: str, target: Path, spec: DetailSpec) -> int:
    con = _duckdb().connect(":memory:")
    try:
        if source.startswith(("hf://", "https://")):
            extension_home = Path(HISTORY_CACHE_DIR).resolve().parent
            extension_home.mkdir(parents=True, exist_ok=True)
            con.execute(f"SET home_directory={_quote(str(extension_home))}")
            con.execute(f"SET http_timeout={int(HISTORY_REMOTE_TIMEOUT)}")
        columns = ",".join(f'"{name}"' for name in DETAIL_COLUMNS)
        con.execute(
            f"COPY (SELECT {columns} FROM read_parquet(?) WHERE upper(trim(cast(train_type AS VARCHAR)))=? AND trim(cast(train_number AS VARCHAR))=?) TO {_quote(str(target))} (FORMAT PARQUET, COMPRESSION ZSTD)",
            [source, spec.train_type, spec.train_number],
        )
        return int(con.execute("SELECT count(*) FROM read_parquet(?)", [str(target)]).fetchone()[0])
    finally:
        con.close()


def _download_month(url: str, directory: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix="history-month-", suffix=".parquet", dir=directory)
    os.close(fd)
    path = Path(name)
    response = None
    try:
        response = curl_requests.get(
            url,
            headers={"User-Agent": "Traviorel/0.3.0"},
            impersonate="firefox",
            timeout=HISTORY_REMOTE_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        with path.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        if response is not None:
            response.close()


def query_remote_month(spec: DetailSpec, target: Path) -> tuple[int, str]:
    hf_url, https_url = remote_urls(spec)
    errors: list[str] = []
    for source, method in ((hf_url, "hf_range"), (https_url, "https_range")):
        try:
            return _copy_filtered(source, target, spec), method
        except Exception as exc:
            target.unlink(missing_ok=True)
            errors.append(f"{type(exc).__name__}: {exc}")
    downloaded: Path | None = None
    try:
        downloaded = _download_month(https_url, target.parent)
        return _copy_filtered(str(downloaded), target, spec), "curl_cffi_download_fallback"
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        raise RuntimeError("history_source_unreachable: " + " | ".join(errors)[-1500:]) from exc
    finally:
        if downloaded:
            downloaded.unlink(missing_ok=True)


def _write_detail(spec: DetailSpec) -> Path:
    target = detail_path(spec)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise RuntimeError("unsafe_history_cache_path")
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp.parquet")
    temporary.unlink(missing_ok=True)
    try:
        with _remote_fill_limiter:
            row_count, method = query_remote_month(spec, temporary)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        cache.history_detail_put({
            "cache_key": detail_cache_key(spec), "train_type": spec.train_type,
            "train_number": spec.train_number, "year": spec.year, "month": spec.month,
            "file_path": str(target), "row_count": row_count, "file_size": target.stat().st_size,
            "source": SOURCE, "source_revision": f"{HISTORY_SOURCE_REVISION}:{method}",
            "schema_version": DETAIL_SCHEMA,
        })
        _prune_lru()
        return target
    finally:
        temporary.unlink(missing_ok=True)


def _prune_lru() -> None:
    maximum = int(HISTORY_CACHE_MAX_GB * 1024 ** 3)
    entries = cache.history_detail_entries()
    total = sum(int(item["file_size"]) for item in entries)
    root = Path(HISTORY_CACHE_DIR).resolve()
    for item in entries:
        if total <= maximum:
            break
        key, path = str(item["cache_key"]), Path(str(item["file_path"]))
        with _guard:
            if key in _active or str(path.resolve(strict=False)) in _reading:
                continue
        if path.is_symlink() or root not in path.resolve(strict=False).parents:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            size = int(item["file_size"])
        cache.history_detail_delete(key)
        total -= size


@contextmanager
def protect_detail_paths(paths: list[Path]):
    resolved = {str(path.resolve(strict=False)) for path in paths}
    with _guard:
        _reading.update(resolved)
    try:
        yield
    finally:
        with _guard:
            _reading.difference_update(resolved)


async def ensure_detail_cache(spec: DetailSpec) -> Path:
    key = detail_cache_key(spec)
    async with _locks.setdefault(key, asyncio.Lock()):
        with _guard:
            _active.add(key)
        try:
            metadata = await asyncio.to_thread(cache.history_detail_get, key)
            target = detail_path(spec)
            if metadata and Path(str(metadata["file_path"])) == target and metadata.get("schema_version") == DETAIL_SCHEMA:
                valid, _ = await asyncio.to_thread(valid_parquet, target)
                if valid:
                    return target
            target.unlink(missing_ok=True)
            # Remote history reads may outlive a cancelled request. Keep them
            # out of asyncio's shared executor so they cannot starve GTFS,
            # cache, DB bridge, or later searches in the same process.
            return await asyncio.get_running_loop().run_in_executor(
                _history_executor, _write_detail, spec,
            )
        finally:
            with _guard:
                _active.discard(key)
