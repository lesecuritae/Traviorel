"""MCP-Server für Traviorel: Reisen suchen und vergleichen, Mobilfunk, Warnungen, historische Pünktlichkeit.

Der Server läuft im selben Prozess wie Traviorel und ruft dessen Bausteine direkt auf (kein Umweg über HTTP). Er ist nur
lesend: Traviorel bucht nichts. Eine Suche merkt sich ihre Verbindungen unter einer kurzen Nummer (``ref``); mit ihr
fragen ``mobile_coverage``, ``travel_warnings`` und ``delay_history`` später genau diese Verbindung ab.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from datetime import datetime
from collections import OrderedDict
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from . import delay_index, price_history as prices
from .coverage import analyze_route
from .models import PriceCalendarRequest, TripRequest
from .service import price_calendar, search
from .station_catalog import search_stations
from .warnings import warnings_for_routes

INSTRUCTIONS = (
    "Reisevergleich für Reisen aus Deutschland: Bahn (Deutsche Bahn, Deutschlandticket), Flix, Nahverkehr, Flüge, "
    "Hotels und Transfers. search_ground vergleicht Bodenverbindungen, plan_flight_trip plant eine Flugreise mit "
    "Anreise, Hotel und Gesamtkosten, price_calendar zeigt den günstigsten Tag. Jede Verbindung hat eine ref; damit "
    "liefern mobile_coverage (Mobilfunk entlang der Strecke), travel_warnings (amtliche Warnungen) und "
    "delay_history (wie pünktlich die Verbindung historisch war) weitere Angaben. Traviorel bucht nichts; Preise "
    "sind Richtwerte und müssen beim Anbieter bestätigt werden."
)

REF_TTL_SECONDS = 3600
MAX_REFS = 400

mcp = MCPServer("traviorel", instructions=INSTRUCTIONS)
_refs: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()


# ---- Merkliste für Verbindungen ---------------------------------------------------------------


def _remember(connections: list[dict[str, Any]]) -> list[str]:
    now = time.time()
    for key in [k for k, (stamp, _) in _refs.items() if now - stamp > REF_TTL_SECONDS]:
        _refs.pop(key, None)
    tag = secrets.token_hex(2)
    refs = []
    for index, connection in enumerate(connections):
        ref = f"{tag}-{index + 1}"
        _refs[ref] = (now, connection)
        refs.append(ref)
    while len(_refs) > MAX_REFS:
        _refs.popitem(last=False)
    return refs


def _connection(ref: str) -> dict[str, Any]:
    entry = _refs.get(str(ref).strip())
    if entry is None or time.time() - entry[0] > REF_TTL_SECONDS:
        raise ValueError("Diese Verbindung kenne ich nicht (mehr). Bitte die Suche noch einmal ausführen und die neue ref nehmen.")
    return entry[1]


# ---- Formatierung -----------------------------------------------------------------------------


def _hm(value: Any) -> str:
    text = str(value or "")
    return text[11:16] if len(text) >= 16 else text


def _euro(value: Any) -> str:
    try:
        return f"{float(value):.2f}".replace(".", ",") + " €"
    except (TypeError, ValueError):
        return "Preis offen"


def _duration(minutes: Any) -> str:
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return ""
    return f"{total // 60} h {total % 60:02d} min" if total >= 60 else f"{total} min"


def _find_connections(result: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if found:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "connections" and isinstance(value, list) and value:
                    found.extend(item for item in value if isinstance(item, dict))
                    return
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(result)
    return found


def _connection_line(ref: str, connection: dict[str, Any]) -> str:
    legs = [leg for leg in connection.get("legs") or [] if isinstance(leg, dict)]
    provider = str(connection.get("provider") or "")
    names = [str(leg.get("line_name") or leg.get("line") or leg.get("train_type") or "") for leg in legs if leg.get("mode") not in {"walking", "walk"}]
    lines = ", ".join(dict.fromkeys(name for name in names if name and len(name) <= 24 and "'" not in name)) if provider in {"Deutsche Bahn", "Transitous"} else ""
    lines = lines or ("Bus" if connection.get("type") == "bus" else "Zug" if connection.get("type") == "train" else "")
    price = _euro(connection["price"]) if connection.get("price") is not None else "Preis offen"
    note = f" – {connection['price_note']}" if connection.get("price_note") and connection.get("price") is None else ""
    return (
        f"- [{ref}] {connection.get('provider', '')} {lines}: {_hm(connection.get('departure'))} → {_hm(connection.get('arrival'))} "
        f"({_duration(connection.get('duration_minutes'))}, {connection.get('transfers', 0)} Umstiege), {price}{note}"
    )


def _text(text: str, structured: dict[str, Any]) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=structured)


# ---- Werkzeuge --------------------------------------------------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def search_ground(
    origin: str,
    destination: str,
    date: str,
    departure_after: str = "06:00",
    prefer: str = "balanced",
    deutschlandticket: bool = False,
    include_flix: bool = True,
    limit: int = 8,
) -> CallToolResult:
    """Bodenverbindungen vergleichen: Deutsche Bahn, Nahverkehr, FlixBus und FlixTrain, mit Preisen.

    Args:
        origin: Start, zum Beispiel "Leipzig Hbf".
        destination: Ziel, zum Beispiel "Hamburg Hbf".
        date: Reisetag YYYY-MM-DD.
        departure_after: Früheste Abfahrt HH:MM.
        prefer: "balanced", "cheapest", "fastest" oder "fewest_transfers".
        deutschlandticket: Ein Deutschlandticket ist vorhanden (Nahverkehr kostet dann 0 €).
        include_flix: Flix mit einbeziehen.
        limit: Höchstens so viele Verbindungen (1 bis 20).
    """
    request = TripRequest(
        travel_mode="ground", journey_type="one_way", origin=origin, destination=destination, departure_date=date,
        departure_after=departure_after, deutschlandticket=deutschlandticket, include_flixbus=include_flix, include_flixtrain=include_flix,
        include_hotel=False, include_feeder=False,
    )
    result = await search(request)
    connections = _find_connections(result)
    key = {"cheapest": lambda c: (c.get("price") is None, c.get("price") or 0), "fastest": lambda c: c.get("duration_minutes") or 10**6,
           "fewest_transfers": lambda c: (c.get("transfers") or 0, c.get("duration_minutes") or 10**6)}.get(prefer)
    if key:
        connections = sorted(connections, key=key)
    connections = connections[: max(1, min(int(limit), 20))]
    if not connections:
        return _text(f"Keine Verbindungen von {origin} nach {destination} am {date} gefunden.", {"connections": []})
    refs = _remember(connections)
    lines = [f"{len(connections)} Verbindungen {origin} → {destination} am {date}:"] + [_connection_line(r, c) for r, c in zip(refs, connections, strict=True)]
    lines.append("Mit der ref in Klammern gibt es mehr: mobile_coverage, travel_warnings, delay_history.")
    return _text("\n".join(lines), {"connections": [{"ref": r, **{k: c.get(k) for k in ("provider", "type", "origin", "destination", "departure", "arrival", "duration_minutes", "transfers", "price", "currency", "offer_url")}} for r, c in zip(refs, connections, strict=True)]})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def plan_flight_trip(
    origin: str,
    destination: str,
    departure_date: str,
    nights: int = 7,
    adults: int = 1,
    hotel: bool = True,
    nonstop_only: bool = False,
    hotel_min_stars: int = 3,
) -> CallToolResult:
    """Flugreise planen: Anreise zum Flughafen, Flug, Hotel, Transfers und Gesamtkosten.

    Args:
        origin: Start, zum Beispiel "Leipzig".
        destination: Ziel, zum Beispiel "Rom".
        departure_date: Abreisetag YYYY-MM-DD.
        nights: Aufenthalt in Nächten (0 bis 60).
        adults: Anzahl Erwachsene.
        hotel: Hotel mit suchen.
        nonstop_only: Nur Direktflüge.
        hotel_min_stars: Mindestens so viele Sterne.
    """
    request = TripRequest(
        travel_mode="flight", journey_type="round_trip" if nights > 0 else "one_way", origin=origin, destination=destination,
        departure_date=departure_date, duration_value=nights, duration_unit="nights", adults=adults,
        include_hotel=hotel and nights > 0, stops="nonstop" if nonstop_only else "any", hotel_min_stars=hotel_min_stars,
    )
    result = await search(request)
    context = result.get("response_context") or {}
    route = context.get("route") or {}
    flight = context.get("flight") or {}
    lines = [f"Reise {route.get('origin', origin)} → {route.get('destination', destination)}, {route.get('outbound_date', departure_date)}"
             + (f" bis {route['return_date']}" if route.get("return_date") else "") + f" ({route.get('origin_airport', '?')} → {route.get('destination_airport', '?')})"]
    price = flight.get("price") or {}
    if price.get("value") is not None:
        out = flight.get("outbound") or {}
        lines.append(f"Flug: {_euro(price['value'])} ({flight.get('provider', '')}), Hinflug {_hm(out.get('departure'))} → {_hm(out.get('arrival'))}, {out.get('stops', 0)} Zwischenstopps. {flight.get('offer_url', '')}")
    else:
        lines.append("Flug: kein Preis gefunden.")
    for option in ((context.get("hotel") or {}).get("options") or [])[:3]:
        hotel_price = (option.get("price") or {}).get("value")
        lines.append(f"Hotel: {option.get('name')} ({option.get('stars', '?')}★, Bewertung {option.get('rating', '?')}) {_euro(hotel_price)} gesamt")
    cost = context.get("cost_summary") or {}
    if cost:
        total = cost.get("verified_total") if cost.get("verified_total") is not None else cost.get("known_subtotal")
        lines.append(f"Kosten gesamt: {_euro(total)}" + ("" if cost.get("complete") else f" (unvollständig, es fehlt: {', '.join(cost.get('missing_components') or [])})"))
    for notice in (context.get("notices") or [])[:2]:
        lines.append(f"Hinweis: {notice}")
    return _text("\n".join(lines), {"route": route, "flight": flight, "hotels": (context.get("hotel") or {}).get("options", [])[:3], "cost_summary": cost})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def price_calendar_search(origin: str, destination: str, date: str, days: int = 7, deutschlandticket: bool = False) -> CallToolResult:
    """Günstigsten Tag für eine Bodenreise finden: vergleicht mehrere Tage ab dem Startdatum.

    Args:
        origin: Start, zum Beispiel "Leipzig Hbf".
        destination: Ziel, zum Beispiel "Hamburg Hbf".
        date: Erster Tag YYYY-MM-DD.
        days: Anzahl Tage (1 bis 14).
        deutschlandticket: Ein Deutschlandticket ist vorhanden.
    """
    request = PriceCalendarRequest(
        travel_mode="ground", journey_type="one_way", origin=origin, destination=destination, departure_date=date,
        calendar_days=max(1, min(int(days), 14)), deutschlandticket=deutschlandticket, include_hotel=False, include_feeder=False,
    )
    result = await price_calendar(request)
    rows = [row for row in result.get("days") or [] if isinstance(row, dict)]
    cheapest = result.get("cheapest_date")
    lines = [f"Preiskalender {origin} → {destination}:"]
    for row in rows:
        price = _euro(row["price"]) if row.get("price") is not None else "kein Preis"
        lines.append(f"- {row.get('date')}: {price}" + (f" ({row.get('connection_count')} Verbindungen)" if row.get("connection_count") else "") + (" ← am günstigsten" if row.get("date") == cheapest else ""))
    if not rows:
        lines.append("Keine Preise gefunden.")
    return _text("\n".join(lines), {"cheapest_date": cheapest, "days": rows})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def mobile_coverage(ref: str) -> CallToolResult:
    """Mobilfunkabdeckung entlang einer Verbindung (Bundesnetzagentur-Daten und OpenCellID).

    Args:
        ref: Nummer einer Verbindung aus search_ground.
    """
    result = await analyze_route(_connection(ref))
    return _text(_compact(result, "Mobilfunk entlang der Verbindung"), result)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def travel_warnings(ref: str) -> CallToolResult:
    """Aktuelle amtliche Warnungen (NINA/BBK) entlang einer Verbindung.

    Args:
        ref: Nummer einer Verbindung aus search_ground.
    """
    result = await warnings_for_routes([_connection(ref)])
    return _text(_compact(result, "Warnungen entlang der Verbindung"), result)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def delay_history(ref: str) -> CallToolResult:
    """Wie pünktlich war diese Bahnverbindung historisch? Je Zug: Anteil der Ankünfte mit höchstens 5, 10 und 30 Minuten Verspätung,
    durchschnittliche Verspätung, Ausfälle, und die Chance, einen Anschluss zu erreichen. Grundlage sind die offenen Verspätungsdaten der
    Deutschen Bahn der letzten Monate.

    Args:
        ref: Nummer einer Verbindung aus search_ground.
    """
    connection = _connection(ref)
    legs = [leg for leg in connection.get("legs") or [] if isinstance(leg, dict) and leg.get("mode") == "train" and leg.get("train_type") and leg.get("train_number")]
    if not legs:
        return _text("Für diese Verbindung gibt es keine Verspätungsdaten (nur Züge der Deutschen Bahn und ihrer Partner).", {"legs": []})
    state = delay_index.request_build()
    if state["state"] in {"disabled", "empty", "building"} and not delay_index.built_months():
        if state["state"] == "disabled":
            return _text("Der Verspätungsindex ist abgeschaltet (DELAY_INDEX=0).", {"index": state})
        months = len(state.get("wanted_months") or [])
        return _text(f"Der Verspätungsindex wird gerade zum ersten Mal aufgebaut ({months} Monate, etwa 2 Minuten je Monat). Bitte in einigen Minuten noch einmal fragen.", {"index": state})
    rows = []
    for leg in legs:
        stats = await delay_index.arrival_stats(leg["train_type"], leg["train_number"], leg.get("destination_id") or "")
        rows.append({"train": f"{leg['train_type']} {leg['train_number']}", "to": leg.get("destination"), "arrival": leg.get("arrival"), "stats": stats, "leg": leg})
    lines = [f"Historische Pünktlichkeit ({', '.join(state['built_months'][::-1]) or 'Index wird aufgebaut'}):"]
    for row in rows:
        st = row["stats"]
        if not st or "ran" not in st:
            lines.append(f"- {row['train']} → {row['to']}: keine Daten für diesen Halt.")
            continue
        lines.append(
            f"- {row['train']} → {row['to']}: {st['on_time_5_percent']} % höchstens 5 min, {st['on_time_10_percent']} % höchstens 10 min, "
            f"{st['within_30_percent']} % höchstens 30 min verspätet; Ø {str(st['average_delay_minutes']).replace('.', ',')} min; "
            f"{str(st['cancelled_percent']).replace('.', ',')} % Ausfälle (Grundlage: {st['events']} Halte)."
        )
    transfers = []
    for incoming, outgoing in zip(rows, rows[1:], strict=False):
        try:
            minutes = int((_dt(outgoing["leg"].get("departure")) - _dt(incoming["arrival"])).total_seconds() // 60)
        except (TypeError, ValueError):
            continue
        st = incoming["stats"] or {}
        if "ran" not in st:
            continue
        for limit, key in ((30, "within_30_percent"), (10, "on_time_10_percent"), (5, "on_time_5_percent")):
            if minutes >= limit:
                chance = f"{st[key]} % (Zug höchstens {limit} min verspätet)"
                break
        else:
            chance = "unter 5 min Umstieg: nur bei pünktlichem Zug möglich"
        transfers.append({"at": incoming["to"], "minutes": minutes, "chance": chance})
        lines.append(f"- Umstieg in {incoming['to']} ({minutes} min): Anschluss erreichbar bei {chance}, sofern der zweite Zug pünktlich fährt.")
    for row in rows:
        row.pop("leg", None)
    return _text("\n".join(lines), {"legs": rows, "transfers": transfers, "index": state})


def _dt(value: Any):
    from datetime import datetime

    return datetime.fromisoformat(str(value))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def price_history(origin: str, destination: str, date: str = "", kind: str = "ground") -> CallToolResult:
    """Wie hat sich der Preis einer Strecke entwickelt? Nur was dieser Server selbst gesehen hat (beginnt leer und wächst mit jeder Suche
    und mit den beobachteten Strecken).

    Args:
        origin: Start, genau wie bei der Suche, zum Beispiel "Leipzig Hbf". Bei kind="flight" der Flughafencode, zum Beispiel "LEJ".
        destination: Ziel, zum Beispiel "Hamburg Hbf" oder "FCO".
        date: Reisetag YYYY-MM-DD für den Verlauf dieser einen Fahrt; leer für die Übersicht über alle beobachteten Tage.
        kind: "ground" (Bahn/Flix) oder "flight".
    """
    if kind not in {"ground", "flight"}:
        raise ValueError('kind muss "ground" oder "flight" sein.')
    if date:
        series = await asyncio.to_thread(prices.route_series, origin, destination, date, kind)
        if not series:
            return _text(f"Für {origin} → {destination} am {date} habe ich noch keine Preise gesehen. Nach einer Suche und mit watch_route wächst der Verlauf.", {"series": {}})
        lines = [f"Preisverlauf {origin} → {destination} am {date}:"]
        for provider, points in series.items():
            values = [price for _, price in points]
            latest_day, latest = points[-1]
            ahead = (datetime.fromisoformat(date) - datetime.fromisoformat(latest_day)).days
            trend = ""
            if len(points) > 1:
                delta = latest - points[0][1]
                trend = f"; seit {points[0][0]} {'+' if delta >= 0 else ''}{delta:.2f} €".replace(".", ",")
            lines.append(f"- {provider}: zuletzt {_euro(latest)} (am {latest_day}, {ahead} Tage vor Abreise), niedrigster {_euro(min(values))}, höchster {_euro(max(values))}, {len(points)} Beobachtung(en){trend}")
        return _text("\n".join(lines), {"series": {p: pts for p, pts in series.items()}})
    overview = await asyncio.to_thread(prices.route_overview, origin, destination, kind)
    if not overview:
        return _text(f"Für {origin} → {destination} habe ich noch keine Preise gesehen.", {"overview": {}})
    lines = [f"Preise {origin} → {destination} (alle beobachteten Reisetage):"]
    for provider, info in overview.items():
        lines.append(f"- {provider}: typisch {_euro(info['typical'])}, niedrigster {_euro(info['lowest'])}, höchster {_euro(info['highest'])} ({info['observations']} Beobachtungen für {info['travel_dates']} Reisetage, {info['first_seen']} bis {info['last_seen']})")
        if info["median_by_days_before"]:
            lines.append("    Typischer Preis nach Vorlauf: " + ", ".join(f"{label}: {_euro(value)}" for label, value in info["median_by_days_before"].items()))
    return _text("\n".join(lines), {"overview": overview})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
async def watch_route(origin: str, destination: str) -> CallToolResult:
    """Eine Bodenstrecke beobachten: Der Server fragt sie einmal am Tag für einige Reisetage ab, damit der Preisverlauf wächst (höchstens 5 Strecken).

    Args:
        origin: Start, zum Beispiel "Leipzig Hbf".
        destination: Ziel, zum Beispiel "Hamburg Hbf".
    """
    watch = await asyncio.to_thread(prices.add_watch, origin, destination)
    return _text(f"Ich beobachte {watch['origin']} → {watch['destination']} (Nr. {watch['id']}): täglich für Reisetage in 2, 7, 14, 21 und 28 Tagen.", watch)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def list_watched_routes() -> CallToolResult:
    """Welche Strecken werden für den Preisverlauf beobachtet?"""
    watches = await asyncio.to_thread(prices.list_watches)
    lines = [f"- Nr. {w['id']}: {w['origin']} → {w['destination']} (seit {w['created']}, zuletzt {w['last_run'] or 'noch nicht'})" for w in watches]
    return _text("\n".join(lines) or "Es wird keine Strecke beobachtet.", {"watches": watches})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
async def stop_watching(watch_id: int) -> CallToolResult:
    """Beobachtung einer Strecke beenden (die bisherigen Preise bleiben).

    Args:
        watch_id: Nummer aus list_watched_routes.
    """
    removed = await asyncio.to_thread(prices.remove_watch, int(watch_id))
    return _text(f"Beobachtung {watch_id} ist beendet." if removed else f"Eine Beobachtung {watch_id} gibt es nicht.", {"removed": removed})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def find_station(query: str, limit: int = 8) -> CallToolResult:
    """Bahnhöfe und Haltestellen zu einem Suchbegriff finden (für genaue Start- und Zielangaben).

    Args:
        query: Name, zum Beispiel "Leipzig".
        limit: Höchstens so viele Treffer (1 bis 20).
    """
    result = await search_stations(query, max(1, min(int(limit), 20)))
    stations = [row for row in result.get("stations") or [] if isinstance(row, dict)]
    lines = [f"- {row.get('name')}" + (f" ({row['region']})" if row.get("region") else "") for row in stations]
    return _text("\n".join(lines) or "Keine Treffer.", {"stations": [{"name": r.get("name"), "region": r.get("region"), "ids": r.get("provider_ids")} for r in stations]})


def _compact(value: Any, title: str, limit: int = 1800) -> str:
    """Knappe, lesbare Textfassung eines Ergebnisses (JSON, gekürzt), damit der Assistent nicht zu viel Text bekommt."""
    import json

    text = json.dumps(value, ensure_ascii=False, default=str)
    return f"{title}:\n{text[:limit]}" + (" …" if len(text) > limit else "")
