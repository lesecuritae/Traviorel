from __future__ import annotations

import contextvars
import re
import time
from typing import Any

_current_search: contextvars.ContextVar[str | None] = contextvars.ContextVar("traviorel_search_id", default=None)
_searches: dict[str, dict[str, Any]] = {}
_MAX_AGE = 900


def valid_search_id(value: str | None) -> str | None:
    value = str(value or "").strip()
    return value if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value) else None


def begin(search_id: str):
    now = time.time()
    for key in [key for key, item in _searches.items() if now - item.get("updated_at", 0) > _MAX_AGE]:
        _searches.pop(key, None)
    _searches[search_id] = {"search_id": search_id, "status": "running", "steps": {}, "updated_at": now}
    return _current_search.set(search_id)


def end(token, *, status: str = "completed") -> None:
    search_id = _current_search.get()
    if search_id in _searches:
        _searches[search_id]["status"] = status
        _searches[search_id]["updated_at"] = time.time()
    _current_search.reset(token)


def update(step: str, status: str, detail: str | None = None) -> None:
    search_id = _current_search.get()
    if not search_id or search_id not in _searches:
        return
    item = {"status": status, "updated_at": time.time()}
    if detail: item["detail"] = detail[:240]
    _searches[search_id]["steps"][step] = item
    _searches[search_id]["updated_at"] = item["updated_at"]


def get(search_id: str) -> dict[str, Any] | None:
    item = _searches.get(search_id)
    if not item: return None
    return {**item, "steps": {key: dict(value) for key, value in item["steps"].items()}}
