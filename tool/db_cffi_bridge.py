from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import urljoin, urlparse

from curl_cffi import requests
from curl_cffi.requests.impersonate import BrowserType
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from reisevergleich.internal_secret import read_internal_token

router = APIRouter()

_ALLOWED_HOSTS = {"app.services-bahn.de", "int.bahn.de", "www.bahn.de"}
_TOKEN = read_internal_token()
_DEFAULT_FIREFOX_POOL = ("firefox133", "firefox135", "firefox144", "firefox147")
_ROTATE_STATUSES = {403, 429}
_SESSION_TTL_SECONDS = max(60, min(int(os.environ.get("DB_CFFI_SESSION_TTL", "900")), 3600))
_MAX_SESSIONS = max(8, min(int(os.environ.get("DB_CFFI_MAX_SESSIONS", "128")), 512))


@dataclass
class _SessionEntry:
    session: requests.Session
    profile: str
    created_at: float
    last_used: float

_sessions: dict[str, _SessionEntry] = {}
_sessions_lock = threading.Lock()


class DbCffiRequest(BaseModel):
    method: str = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    session_key: str | None = Field(
        default=None,
        description="Interner Schlüssel für eine zusammengehörige DB-Suche/Split-Prüfung.",
    )


def _curl_cffi_version() -> str:
    try:
        return version("curl_cffi")
    except PackageNotFoundError:
        return "unknown"


def _supported_browser_profiles() -> set[str]:
    return {item.value for item in BrowserType}


def _profile_pool() -> tuple[str, ...]:
    supported = _supported_browser_profiles()
    configured = os.environ.get("DB_CFFI_IMPERSONATE_POOL", "").strip()
    requested = tuple(
        item.strip() for item in configured.split(",") if item.strip()
    ) if configured else _DEFAULT_FIREFOX_POOL
    valid = tuple(dict.fromkeys(item for item in requested if item in supported))
    if valid:
        return valid
    fallback = tuple(item for item in _DEFAULT_FIREFOX_POOL if item in supported)
    if fallback:
        return fallback
    if "firefox" in supported:
        return ("firefox",)
    raise RuntimeError("curl_cffi stellt kein unterstütztes Firefox-Impersonation-Profil bereit")


_FIREFOX_POOL = _profile_pool()


def _new_entry(*, exclude_profile: str | None = None) -> _SessionEntry:
    choices = [item for item in _FIREFOX_POOL if item != exclude_profile]
    if not choices:
        choices = list(_FIREFOX_POOL)
    profile = secrets.choice(choices)
    now = time.monotonic()
    entry = _SessionEntry(
        session=requests.Session(impersonate=profile, timeout=30),
        profile=profile,
        created_at=now,
        last_used=now,
    )
    return entry


def _cleanup_sessions(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    stale = [
        key for key, entry in _sessions.items()
        if current - entry.last_used > _SESSION_TTL_SECONDS
    ]
    for key in stale:
        entry = _sessions.pop(key, None)
        if entry is not None:
            try:
                entry.session.close()
            except Exception:
                pass
    if len(_sessions) <= _MAX_SESSIONS:
        return
    ordered = sorted(_sessions.items(), key=lambda item: item[1].last_used)
    for key, entry in ordered[: len(_sessions) - _MAX_SESSIONS]:
        _sessions.pop(key, None)
        try:
            entry.session.close()
        except Exception:
            pass


def _normalize_session_key(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        # Aufrufe ohne Sitzungsschlüssel teilen eine begrenzte Fallback-Sitzung.
        return "fallback"
    if len(value) > 160:
        value = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
    return value


def _session_entry(raw_key: str | None) -> tuple[str, _SessionEntry, bool]:
    key = _normalize_session_key(raw_key)
    now = time.monotonic()
    with _sessions_lock:
        _cleanup_sessions(now)
        entry = _sessions.get(key)
        reused = entry is not None
        if entry is None:
            entry = _new_entry()
            _sessions[key] = entry
        entry.last_used = now
    return key, entry, reused


def _rotate_session(key: str, old_profile: str | None) -> _SessionEntry:
    with _sessions_lock:
        old = _sessions.pop(key, None)
        if old is not None:
            try:
                old.session.close()
            except Exception:
                pass
        entry = _new_entry(exclude_profile=old_profile)
        _sessions[key] = entry
        return entry


def _clean_headers(raw: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key)
        if name.casefold() in {
            "host",
            "content-length",
            "connection",
            "user-agent",
            "accept-encoding",
        }:
            continue
        result[name] = str(value)
    return result


def _authorize(token: str | None) -> None:
    if not _TOKEN or not token or not secrets.compare_digest(token, _TOKEN):
        raise HTTPException(status_code=403, detail="forbidden")


_MAX_REDIRECTS = 5


class RedirectRejected(Exception):
    """A redirect left the allowlist or looped. Not a transport error, so no retry."""


def _request_with_entry(
    entry: _SessionEntry,
    method: str,
    url: str,
    headers: dict[str, str],
    body: str | bytes | None,
):
    # Redirects are followed here, not by curl_cffi, so every hop is held to the same
    # https + _ALLOWED_HOSTS rule as the first request. With allow_redirects=True a bahn.de
    # response could send the bridge anywhere and its body came back to the caller.
    data = body.encode("utf-8") if isinstance(body, str) else body
    for _ in range(_MAX_REDIRECTS + 1):
        response = entry.session.request(
            method,
            url,
            headers=headers,
            data=data,
            timeout=30,
            allow_redirects=False,
        )
        location = response.headers.get("location")
        if response.status_code not in (301, 302, 303, 307, 308) or not location:
            return response
        target = urljoin(url, location)
        parsed = urlparse(target)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
            raise RedirectRejected("redirect target not allowed")
        if response.status_code == 303 or (response.status_code in (301, 302) and method not in ("GET", "HEAD")):
            method, data = "GET", None
        url = target
    raise RedirectRejected("too many redirects")


@router.get("/internal/db-cffi/status", include_in_schema=False)
def db_cffi_status(
    x_db_cffi_token: str | None = Header(default=None, alias="X-DB-CFFI-Token"),
) -> dict[str, Any]:
    _authorize(x_db_cffi_token)
    with _sessions_lock:
        _cleanup_sessions()
        active = len(_sessions)
    return {
        "status": "ok",
        "curl_cffi_version": _curl_cffi_version(),
        "impersonate_pool": list(_FIREFOX_POOL),
        "selection": "per_logical_db_session",
        "sticky_within_session": True,
        "rotate_on_status": sorted(_ROTATE_STATUSES),
        "max_attempts": 2,
        "active_sessions": active,
        "session_ttl_seconds": _SESSION_TTL_SECONDS,
    }


@router.post("/internal/db-cffi/request", include_in_schema=False)
def db_cffi_request(
    payload: DbCffiRequest,
    x_db_cffi_token: str | None = Header(default=None, alias="X-DB-CFFI-Token"),
) -> dict[str, Any]:
    _authorize(x_db_cffi_token)

    method = payload.method.strip().upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise HTTPException(status_code=400, detail="unsupported method")

    parsed = urlparse(payload.url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise HTTPException(status_code=400, detail="target not allowed")

    headers = _clean_headers(payload.headers)
    if parsed.hostname == "int.bahn.de":
        headers.setdefault("Referer", "https://www.bahn.de/")
        headers.setdefault("Origin", "https://www.bahn.de")

    body = payload.body
    if body is not None and not isinstance(body, str):
        body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    key, entry, reused = _session_entry(payload.session_key)
    rotated = False
    last_exc: Exception | None = None

    for attempt in range(2):
        try:
            response = _request_with_entry(entry, method, payload.url, headers, body)
            if response.status_code in _ROTATE_STATUSES and attempt == 0 and len(_FIREFOX_POOL) > 1:
                entry = _rotate_session(key, entry.profile)
                reused = False
                rotated = True
                continue
            return {
                "status": int(response.status_code),
                "url": str(response.url),
                "http_version": int(response.http_version) if response.http_version is not None else None,
                "content_type": response.headers.get("content-type"),
                "body": response.text,
                "fingerprint_profile": entry.profile,
                "fingerprint_rotated": rotated,
                "session_reused": reused,
            }
        except RedirectRejected as exc:
            last_exc = exc
            break
        except Exception as exc:
            last_exc = exc
            if attempt == 0 and len(_FIREFOX_POOL) > 1:
                entry = _rotate_session(key, entry.profile)
                reused = False
                rotated = True
                continue
            break

    exc = last_exc or RuntimeError("unknown curl_cffi transport error")
    return {
        "status": 599,
        "url": payload.url,
        "http_version": None,
        "content_type": "application/json",
        "body": json.dumps(
            {"error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        ),
        "fingerprint_profile": entry.profile,
        "fingerprint_rotated": rotated,
        "session_reused": reused,
    }
