from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from typing import Any

from .config import MAX_FEEDER_HANDOFFS, TZ
from .db import rank_routes
from .feeder_common import _compact_feeder_segment, _route_price
from .feeder_db import _db_routes, _outbound_via_options, _return_via_options
from .feeder_flix import _compact_flix_feeder_segment, _flix_route_datetime, _flix_routes_for_targets
from .utils import as_float, parse_datetime


_PLACE_ALIASES = {"hannover": "hanover", "koeln": "cologne", "koln": "cologne", "köln": "cologne", "muenchen": "munich", "munchen": "munich", "münchen": "munich", "nuernberg": "nuremberg", "nurnberg": "nuremberg", "nürnberg": "nuremberg"}

def _place_key(value: Any) -> str:
    text = str(value or "").casefold().replace("ß", "ss")
    text = re.sub(r"\b(hbf|hauptbahnhof|central station|zob)\b", " ", text)
    words = re.findall(r"[a-zäöü]+", text)
    if not words:
        return ""
    return _PLACE_ALIASES.get(words[0], words[0])

def _flix_endpoint_matches(stop: Any, expected: str) -> bool:
    if not isinstance(stop, dict):
        return False
    actual = stop.get("station") or stop.get("city") or stop.get("address")
    return bool(_place_key(actual) and _place_key(actual) == _place_key(expected))

def _dedupe(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: dict[tuple[Any, ...], dict[str, Any]] = {}
    for option in options:
        key = (option.get("type"), tuple(option.get("split_stations") or []) or option.get("split_station"),
               option.get("departure"), option.get("arrival"), as_float(option.get("total_price")))
        found.setdefault(key, option)
    return sorted(found.values(), key=lambda x: (as_float(x.get("total_price")) or 1_000_000,
                                                  x.get("duration_minutes") or 1_000_000))[:8]


async def _flix_three_part(
    origin: str, destination: str, travel_date: str, departure_after: str,
    split_candidates: list[str], transfer_minutes: int,
    *, include_flixbus: bool, include_flixtrain: bool,
) -> list[dict[str, Any]]:
    pairs = [(a, b) for i, a in enumerate(split_candidates) for b in split_candidates[i + 1:] if a != b]

    async def one(board: str, alight: str) -> list[dict[str, Any]]:
        first, _, _ = await _db_routes(origin, board, travel_date, departure_after,
                                       mode="deutschlandticket", results=5, not_only_fast_routes=True)
        first = rank_routes(first, "fastest")[:2]
        flix, _ = await _flix_routes_for_targets(
            board, [alight], travel_date, departure_after,
            include_flixbus=include_flixbus, include_flixtrain=include_flixtrain, max_results=6,
        )
        output: list[dict[str, Any]] = []
        for local_first in first:
            start = parse_datetime(local_first.get("departure"))
            ready = parse_datetime(local_first.get("arrival"))
            if not start or not ready:
                continue
            ready += timedelta(minutes=transfer_minutes)
            for middle in flix:
                middle_departure = _flix_route_datetime(middle, "departure")
                middle_arrival = _flix_route_datetime(middle, "arrival")
                price = _route_price(middle)
                if (not _flix_endpoint_matches(middle.get("departure"), board) or not _flix_endpoint_matches(middle.get("arrival"), alight) or not middle_departure or not middle_arrival or middle_departure < ready or price <= 0):
                    continue
                tail_ready = middle_arrival + timedelta(minutes=transfer_minutes)
                tail_date = tail_ready.astimezone(TZ).date().isoformat()
                tail_after = tail_ready.astimezone(TZ).strftime("%H:%M")
                tail, _, _ = await _db_routes(alight, destination, tail_date, tail_after,
                                              mode="deutschlandticket", results=5, not_only_fast_routes=True)
                for local_last in rank_routes(tail, "fastest")[:2]:
                    last_departure = parse_datetime(local_last.get("departure"))
                    last_arrival = parse_datetime(local_last.get("arrival"))
                    if not last_departure or not last_arrival or last_departure < tail_ready:
                        continue
                    output.append({
                        "type": "deutschlandticket_plus_flix_plus_deutschlandticket",
                        "label": "Deutschlandticket, bezahlter Flix-Abschnitt, danach Deutschlandticket",
                        "split_stations": [board, alight],
                        "departure": start.astimezone(TZ).isoformat(),
                        "arrival": last_arrival.astimezone(TZ).isoformat(),
                        "duration_minutes": int((last_arrival - start).total_seconds() // 60),
                        "total_price": round(price, 2), "price_known": True,
                        "currency": middle.get("currency") or "EUR",
                        "requires_deutschlandticket": True,
                        "price_note": "Gesamtpreis enthält nur den Flix-Abschnitt; beide Nahverkehrsabschnitte sind mit vorhandenem Deutschlandticket mit 0 EUR Zusatzkosten angesetzt.",
                        "self_managed_transfers": 2,
                        "segments": [_compact_feeder_segment(local_first, deutschlandticket=True),
                                     _compact_flix_feeder_segment(middle),
                                     _compact_feeder_segment(local_last, deutschlandticket=True)],
                    })
                    break
        return output

    results = await asyncio.gather(*(one(a, b) for a, b in pairs[:6]), return_exceptions=True)
    return [option for result in results if isinstance(result, list) for option in result]


async def ground_mixed_options(
    origin: str, destination: str, travel_date: str, departure_after: str,
    split_candidates: list[str], *, include_flixbus: bool, include_flixtrain: bool,
    transfer_minutes: int = 15,
) -> list[dict[str, Any]]:
    # Each handoff multiplies the db-api searches one request causes; use the same clamp as
    # the flight-feeder path instead of a fixed 4.
    candidates = [x.strip() for x in split_candidates if str(x).strip()][:MAX_FEEDER_HANDOFFS]
    db_first = await asyncio.gather(*(
        _outbound_via_options(origin, destination, handoff, travel_date, departure_after, None,
                              transfer_minutes, deutschlandticket_first=True)
        for handoff in candidates
    ), return_exceptions=True)
    db_last = await asyncio.gather(*(
        _return_via_options(origin, destination, handoff, travel_date, departure_after,
                            transfer_minutes, deutschlandticket_last=True)
        for handoff in candidates
    ), return_exceptions=True)
    options = [x for group in (*db_first, *db_last) if isinstance(group, list) for x in group]
    options.extend(await _flix_three_part(
        origin, destination, travel_date, departure_after, candidates, transfer_minutes,
        include_flixbus=include_flixbus, include_flixtrain=include_flixtrain,
    ))
    return _dedupe(options)
