"""Flüge (und Hotels) über den öffentlichen MCP-Dienst von Skiplagged, ohne trvl und ohne Schlüssel.

Skiplagged betreibt einen offenen MCP-Server (``mcp.skiplagged.com``) genau für solche Abfragen durch Programme.
Er antwortet mit Markdown-Tabellen; sie werden hier in das Flugformat gebracht, das ``trvl.compact_flight_options``
kennt. Preise kommen in Dollar und werden mit dem Tageskurs der EZB in Euro umgerechnet. Der Dienst begrenzt
Anfragen pro Adresse (Cloudflare Fehler 1015/HTTP 429): Nach einer Ablehnung ruht dieser Weg kurz, und
``trvl.flight_search`` greift auf trvl zurück.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from . import fx

_URL = os.environ.get("SKIPLAGGED_MCP_URL", "https://mcp.skiplagged.com/mcp")
_TIMEOUT = max(5, min(int(os.environ.get("SKIPLAGGED_TIMEOUT", "25")), 60))
_MIN_GAP = 1.5
_COOLDOWN = 90.0
_lock = asyncio.Lock()
_last_call = 0.0
_blocked_until = 0.0


class SkiplaggedError(RuntimeError):
    pass


def _post(payload: dict[str, Any], session: str | None) -> tuple[list[dict[str, Any]], str | None]:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "User-Agent": "Mozilla/5.0 (compatible; Traviorel)"}
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # nosec B310 - feste https-Adresse
            body = response.read(3_000_000).decode("utf-8", errors="replace")
            new_session = response.headers.get("Mcp-Session-Id") or session
    except urllib.error.HTTPError as exc:
        raise SkiplaggedError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise SkiplaggedError(f"nicht erreichbar: {exc}") from exc
    messages = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:") and line[5:].strip()]
    if not messages and body.strip().startswith("{"):
        messages = [json.loads(body)]
    return messages, new_session


def call_tool_sync(name: str, arguments: dict[str, Any]) -> str:
    _, session = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "traviorel", "version": "0.3"}}}, None)
    try:
        _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    except SkiplaggedError:
        pass  # Manche Server antworten darauf leer; die eigentliche Abfrage entscheidet.
    messages, _ = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}}, session)
    for message in messages:
        result = message.get("result") if isinstance(message, dict) else None
        if isinstance(result, dict):
            text = "\n".join(part.get("text", "") for part in result.get("content", []) if isinstance(part, dict))
            if result.get("isError"):
                raise SkiplaggedError(text[:200] or "Fehler vom Dienst")
            return text
        if isinstance(message, dict) and message.get("error"):
            raise SkiplaggedError(str((message["error"] or {}).get("message"))[:200])
    raise SkiplaggedError("leere Antwort")


async def call_tool(name: str, arguments: dict[str, Any]) -> str:
    global _last_call, _blocked_until
    async with _lock:
        now = time.monotonic()
        if now < _blocked_until:
            raise SkiplaggedError("ruht nach einer Ablehnung (Ratenbegrenzung)")
        wait = _MIN_GAP - (now - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            try:
                return await asyncio.to_thread(call_tool_sync, name, arguments)
            except SkiplaggedError as exc:
                if "timeout" not in str(exc).casefold():
                    raise
                # Der Dienst gibt bei Last gelegentlich sein eigenes 10-Sekunden-Limit weiter: einmal wiederholen.
                await asyncio.sleep(3)
                return await asyncio.to_thread(call_tool_sync, name, arguments)
        except SkiplaggedError as exc:
            if "429" in str(exc) or "403" in str(exc):
                _blocked_until = time.monotonic() + _COOLDOWN
            raise
        finally:
            _last_call = time.monotonic()


_ROW = re.compile(r"^\|\s*\$?([0-9][0-9.,]*)\s*\|(.+)\|\s*$")
_SEGMENT = re.compile(r"([A-Z]{3})\s*→\s*([A-Z]{3})\s*\((\d{4}-\d\d-\d\d[ T][\d:]+(?:[+-]\d\d:\d\d)?)\s*→\s*(\d{4}-\d\d-\d\d[ T][\d:]+(?:[+-]\d\d:\d\d)?)\)")
_DURATION = re.compile(r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?")


def _minutes(text: str) -> int:
    match = _DURATION.fullmatch(text.strip())
    if not match or not any(match.groups()):
        return 0
    days, hours, minutes = (int(value or 0) for value in match.groups())
    return days * 1440 + hours * 60 + minutes


def _iso(value: str) -> str:
    return value.replace(" ", "T", 1)


def parse_flights(markdown: str) -> list[dict[str, Any]]:
    """Zeilen der Ergebnistabelle in trvl-kompatible Flüge (Preis in USD, Umrechnung später)."""
    flights: list[dict[str, Any]] = []
    for line in markdown.splitlines():
        row = _ROW.match(line.strip())
        if not row:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            continue
        price = float(cells[0].lstrip("$").replace(",", ""))
        legs = []
        for departure, arrival, start, end in _SEGMENT.findall(cells[5]):
            legs.append({
                "departure_airport": {"code": departure, "name": ""}, "arrival_airport": {"code": arrival, "name": ""},
                "departure_time": _iso(start), "arrival_time": _iso(end), "duration": 0,
                "airline": cells[4].split(",")[0].strip(), "airline_code": "", "flight_number": "",
            })
        link = re.search(r"\((https?://[^)\s]+)\)", cells[6])
        if not legs or price <= 0:
            continue
        stops_text = cells[2].casefold()
        flights.append({
            "price": price, "currency": "USD", "duration": _minutes(cells[1]),
            "stops": 0 if "nonstop" in stops_text else int((re.search(r"(\d+)", stops_text) or [0, 0])[1]),
            "provider": "skiplagged", "legs": legs, "booking_url": link.group(1) if link else None,
        })
    return flights


def flights_to_eur(flights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dollarpreise in Euro; Flüge ohne verfügbaren Kurs bleiben mit ihrer Originalwährung stehen."""
    output = []
    for flight in flights:
        converted = fx.to_eur(flight["price"], flight.get("currency"))
        if converted is not None and flight.get("currency") != "EUR":
            flight = {**flight, "original_price": flight["price"], "original_currency": flight["currency"], "price": converted, "currency": "EUR"}
        output.append(flight)
    return output


async def flights(origin: str, destination: str, departure: str, return_date: str | None, adults: int, cabin: str, stops: str, limit: int = 30) -> dict[str, Any]:
    """Wie ``run_json_command``: ``{"ok": bool, "data": {"flights": [...]}, "error": ...}``."""
    arguments: dict[str, Any] = {"origin": origin, "destination": destination, "departureDate": departure, "adults": max(1, adults), "sort": "price", "limit": min(limit, 100)}
    if return_date:
        arguments["returnDate"] = return_date
    fare = {"economy": "economy", "premium_economy": "premium", "premium": "premium", "business": "business", "first": "first"}.get(str(cabin or "economy").casefold())
    if fare:
        arguments["fareClass"] = fare
    max_stops = {"nonstop": "none", "none": "none", "one_stop": "one", "one": "one"}.get(str(stops or "").casefold())
    if max_stops:
        arguments["maxStops"] = max_stops
    try:
        await fx.ensure_rates()
        text = await call_tool("sk_flights_search", arguments)
    except (SkiplaggedError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"Skiplagged: {exc}"}
    found = flights_to_eur(parse_flights(text))
    if not found:
        return {"ok": False, "error": "Skiplagged lieferte keine auswertbaren Flüge"}
    return {"ok": True, "data": {"success": True, "flights": found}, "raw": {}}


_HOTEL_ROW = re.compile(r"^\|\s*\*\*(.+?)\*\*(?:<br/>(.*?))?\s*\|\s*(?:(\d)★)?\s*·?\s*(?:([0-9.]+)/10)?\s*\|\s*\$?([0-9][0-9.,]*)\s*\|\s*\$?([0-9][0-9.,]*)\s*\|(.*)\|\s*$")


def parse_hotels(markdown: str, nights: int) -> list[dict[str, Any]]:
    """Zeilen der Hotel-Tabelle in trvl-kompatible Hotels (Preise in USD, Umrechnung später)."""
    hotels: list[dict[str, Any]] = []
    for line in markdown.splitlines():
        row = _HOTEL_ROW.match(line.strip())
        if not row:
            continue
        name, address, stars, rating, nightly, total, rest = row.groups()
        link = re.search(r"\((https?://[^)\s]+)\)", rest or "")
        nightly_price, total_price = float(nightly.replace(",", "")), float(total.replace(",", ""))
        if nightly_price <= 0:
            continue
        hotels.append({
            "name": name.strip(), "address": (address or "").strip(), "stars": int(stars) if stars else 0,
            "rating": float(rating) if rating else 0, "price": nightly_price, "currency": "USD", "price_basis": "room_nightly",
            "price_confidence": "unverified", "property_type": "hotel", "booking_url": link.group(1) if link else None,
            "sources": [{"provider": "skiplagged", "price": total_price, "currency": "USD", "price_basis": "stay_total"}] if total_price > 0 and nights > 0 else [],
            "freshness": "live",
        })
    return hotels


def hotels_to_eur(hotels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for hotel in hotels:
        rate_price = fx.to_eur(hotel["price"], hotel.get("currency"))
        if rate_price is None:
            continue  # ohne Kurs keine Euro-Angabe erfinden
        sources = [
            {**source, "price": fx.to_eur(source["price"], source.get("currency")) or 0, "currency": "EUR"}
            for source in hotel.get("sources", [])
        ]
        output.append({**hotel, "price": rate_price, "currency": "EUR", "sources": [s for s in sources if s["price"] > 0]})
    return output


async def hotels(location: str, checkin: str, checkout: str, adults: int, limit: int = 30) -> dict[str, Any]:
    """Wie ``run_json_command``: ``{"ok": bool, "data": {"hotels": [...]}, "error": ...}``."""
    from datetime import date
    nights = max(0, (date.fromisoformat(checkout) - date.fromisoformat(checkin)).days)
    try:
        await fx.ensure_rates()
        text = await call_tool("sk_hotels_search", {"city": location, "checkin": checkin, "checkout": checkout, "numAdults": max(1, adults), "sort": "price", "limit": min(limit, 100)})
    except (SkiplaggedError, json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "error": f"Skiplagged: {exc}"}
    found = hotels_to_eur(parse_hotels(text, nights))
    if not found:
        return {"ok": False, "error": "Skiplagged lieferte keine auswertbaren Hotels"}
    return {"ok": True, "data": {"success": True, "hotels": found}, "raw": {}}
