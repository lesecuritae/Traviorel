from __future__ import annotations

import asyncio
import math
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from . import delay_index
from .cache import CACHE_SCHEMA, cached_call
from .config import (
    HISTORY_DEFAULT_WINDOW_DAYS, HISTORY_ENABLED, HISTORY_MAX_CONCURRENCY,
    HISTORY_REMOTE_TIMEOUT, HISTORY_SOURCE_REVISION, HISTORY_STATS_TTL, TZ,
)
from .history_cache import DETAIL_SCHEMA, SOURCE, DetailSpec, ensure_detail_cache, protect_detail_paths
from .utils import parse_datetime

STATS_SCHEMA = 2
DIRECT_ON_TIME_LIMIT_MINUTES = 10
_STOP_ID_RE = re.compile(r"^(.+-\d{10})-(\d+)$")
_TRAIN_TYPES = {"ICE", "IC", "EC", "ECE", "RE", "RB", "S", "IRE", "NJ", "RJ", "RJX"}


def normalize_eva(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    return str(int(text)) if text.isdecimal() else text


def normalize_train_type(value: Any) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").strip()).upper()
    return text or None


def normalize_train_number(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def derive_run_instance_id(stop_id: Any) -> str | None:
    if not isinstance(stop_id, str):
        return None
    match = _STOP_ID_RE.fullmatch(stop_id.strip())
    return match.group(1) if match else None


def _as_datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else parse_datetime(value)


def _delay_minutes(planned: Any, changed: Any) -> float | None:
    planned_dt, changed_dt = _as_datetime(planned), _as_datetime(changed)
    return (changed_dt - planned_dt).total_seconds() / 60 if planned_dt and changed_dt else None


def _station_num(row: dict[str, Any]) -> int | None:
    try:
        return int(row.get("train_line_station_num"))
    except (TypeError, ValueError):
        return None


def build_relation_samples(
    rows: Iterable[dict[str, Any]], origin_eva: str, destination_eva: str,
    *, window_start: date | None = None, window_end: date | None = None,
) -> list[dict[str, Any]]:
    origin_eva, destination_eva = normalize_eva(origin_eva) or "", normalize_eva(destination_eva) or ""
    runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        run_id = derive_run_instance_id(row.get("id"))
        if run_id:
            row["eva"] = normalize_eva(row.get("eva"))
            runs[run_id].append(row)
    deduplicated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for run_id, stops in runs.items():
        pairs = [
            (origin, destination)
            for origin in stops if origin.get("eva") == origin_eva
            for destination in stops if destination.get("eva") == destination_eva
            if _station_num(origin) is not None and _station_num(destination) is not None
            and _station_num(origin) < _station_num(destination)
        ]
        if not pairs:
            continue
        origin, destination = min(pairs, key=lambda pair: _station_num(pair[1]) - _station_num(pair[0]))
        planned_departure = _as_datetime(origin.get("departure_planned_time") or origin.get("time"))
        if not planned_departure:
            continue
        service_day = planned_departure.date()
        if (window_start and service_day < window_start) or (window_end and service_day > window_end):
            continue
        canceled = origin.get("is_canceled") is True or destination.get("is_canceled") is True
        sample = {
            "run_instance_id": run_id, "service_date": service_day,
            "planned_departure": planned_departure, "origin_eva": origin_eva,
            "destination_eva": destination_eva, "explicit_cancellation": canceled,
            "departure_delay_minutes": None if canceled else _delay_minutes(
                origin.get("departure_planned_time"), origin.get("departure_change_time")),
            "arrival_delay_minutes": None if canceled else _delay_minutes(
                destination.get("arrival_planned_time"), destination.get("arrival_change_time")),
        }
        key = (run_id, origin_eva, destination_eva)
        existing = deduplicated.get(key)
        score = lambda item: 3 if item["explicit_cancellation"] else sum(
            item.get(name) is not None for name in ("departure_delay_minutes", "arrival_delay_minutes"))
        if existing is None or score(sample) > score(existing):
            deduplicated[key] = sample
    return sorted(deduplicated.values(), key=lambda item: (item["service_date"], item["run_instance_id"]))


def _percentile(values: list[float], fraction: float) -> float | None:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)] if ordered else None


def _select_samples(samples: list[dict[str, Any]], planned: datetime | None) -> tuple[list[dict[str, Any]], str]:
    if not planned:
        return samples, "train_number_route"
    weekday, time_band = planned.weekday(), planned.hour // 4
    choices = (
        ([s for s in samples if s["planned_departure"].weekday() == weekday and s["planned_departure"].hour // 4 == time_band], "train_number_route_weekday_time", 10),
        ([s for s in samples if s["planned_departure"].weekday() == weekday], "train_number_route_weekday", 10),
        ([s for s in samples if s["planned_departure"].hour // 4 == time_band], "train_number_route_time", 10),
        (samples, "train_number_route", 5),
    )
    for selected, level, minimum in choices:
        if len(selected) >= minimum:
            return selected, level
    return samples, "train_number_route"


def calculate_statistics(
    samples: list[dict[str, Any]], window_days: int, planned_departure: datetime | None = None,
) -> dict[str, Any]:
    selected, fallback_level = _select_samples(samples, planned_departure)
    count = len(selected)
    cancellations = sum(item["explicit_cancellation"] is True for item in selected)
    departure = [float(item["departure_delay_minutes"]) for item in selected if item["departure_delay_minutes"] is not None]
    arrival = [float(item["arrival_delay_minutes"]) for item in selected if item["arrival_delay_minutes"] is not None]
    observed_outcomes = cancellations + len(arrival)
    days = sorted({item["service_date"] for item in selected})
    quality = "insufficient" if observed_outcomes < 5 else "limited" if observed_outcomes < 30 else "good"
    result: dict[str, Any] = {
        "status": "insufficient_data" if quality == "insufficient" else "ok",
        "source": SOURCE, "window_days": window_days, "sample_count": count,
        "reliability_sample_count": observed_outcomes, "observed_service_days": len(days),
        "explicit_cancellations": cancellations,
        "cancellation_rate": cancellations / observed_outcomes if observed_outcomes else None,
        "first_observation": days[0].isoformat() if days else None,
        "last_observation": days[-1].isoformat() if days else None,
        "quality": quality, "fallback_level": fallback_level,
        "direct_event": f"not_cancelled_and_arrival_delay_lte_{DIRECT_ON_TIME_LIMIT_MINUTES}_minutes",
        "direct_reliability_rate": (
            sum(value <= DIRECT_ON_TIME_LIMIT_MINUTES for value in arrival) / observed_outcomes
            if observed_outcomes else None
        ),
        "_arrival_delay_samples": arrival,
    }
    if departure:
        result.update(average_departure_delay_minutes=statistics.fmean(departure), median_departure_delay_minutes=statistics.median(departure))
    if arrival:
        result.update(
            average_arrival_delay_minutes=statistics.fmean(arrival), median_arrival_delay_minutes=statistics.median(arrival),
            min_arrival_delay_minutes=min(arrival), max_arrival_delay_minutes=max(arrival),
            p90_arrival_delay_minutes=_percentile(arrival, .90), p95_arrival_delay_minutes=_percentile(arrival, .95),
            on_time_5_rate=sum(value <= 5 for value in arrival) / len(arrival),
            on_time_10_rate=sum(value <= 10 for value in arrival) / len(arrival),
            late_over_10_rate=sum(value > 10 for value in arrival) / len(arrival),
        )
    return {key: round(value, 3) if isinstance(value, float) else value for key, value in result.items() if value is not None}


def index_statistics(stats: dict[str, Any]) -> dict[str, Any]:
    """Statistik aus dem lokalen Verspätungsindex in derselben Form wie ``calculate_statistics``."""
    events, ran, cancelled = int(stats["events"]), int(stats["ran"]), int(stats.get("cancelled", 0))
    cdf = [(int(t), float(share)) for t, share in stats["cdf"]]
    at = dict(cdf)
    quality = "insufficient" if events < 5 else "limited" if events < 30 else "good"
    on_time_10 = stats["on_time_10_percent"] / 100
    months = stats.get("months") or [None, None]
    result: dict[str, Any] = {
        "status": "insufficient_data" if quality == "insufficient" else "ok", "source": delay_index.DATASET,
        "window_days": 30 * max(1, len(delay_index.built_months())), "sample_count": events, "reliability_sample_count": events,
        "explicit_cancellations": cancelled, "cancellation_rate": cancelled / events,
        "first_observation": f"{months[0]}-01" if months[0] else None, "last_observation": f"{months[1]}-01" if months[1] else None,
        "quality": quality, "fallback_level": "index",
        "direct_event": f"not_cancelled_and_arrival_delay_lte_{DIRECT_ON_TIME_LIMIT_MINUTES}_minutes",
        "direct_reliability_rate": at.get(DIRECT_ON_TIME_LIMIT_MINUTES, on_time_10),
        "average_arrival_delay_minutes": stats["average_delay_minutes"],
        "on_time_5_rate": stats["on_time_5_percent"] / 100, "on_time_10_rate": on_time_10, "late_over_10_rate": 1 - on_time_10,
        "_cdf": cdf,
    }
    return {key: round(value, 3) if isinstance(value, float) else value for key, value in result.items() if value is not None}


def reliability_label(percent: int, kind: str) -> str:
    if kind == "connection":
        return "Anschluss " + ("sehr wahrscheinlich" if percent >= 90 else "wahrscheinlich" if percent >= 75 else "unsicher" if percent >= 55 else "eher unwahrscheinlich" if percent >= 35 else "unwahrscheinlich")
    return "Zuverlässigkeit " + ("sehr hoch" if percent >= 90 else "hoch" if percent >= 75 else "mittel" if percent >= 55 else "niedrig" if percent >= 35 else "sehr niedrig")


def query_local_history(paths: list[Path]) -> list[dict[str, Any]]:
    if not paths:
        return []
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("duckdb_unavailable") from exc
    con = duckdb.connect(":memory:")
    try:
        with protect_detail_paths(paths):
            cursor = con.execute("SELECT * FROM read_parquet(?, union_by_name=true)", [[str(path) for path in paths]])
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        con.close()


def _completed_window(window_days: int, today: date | None = None) -> tuple[date, date]:
    current = today or datetime.now(TZ).date()
    end = current.replace(day=1) - timedelta(days=1)
    return end - timedelta(days=window_days - 1), end


def _months(start: date, end: date) -> list[tuple[int, int]]:
    output, cursor = [], start.replace(day=1)
    while cursor <= end.replace(day=1):
        output.append((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return output


async def _statistics_for_leg(
    train_type: str, train_number: str, origin_eva: str, destination_eva: str,
    window_days: int, planned_departure: datetime | None, semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    start, end = _completed_window(window_days)
    key = {
        "stats_schema": STATS_SCHEMA, "detail_schema": DETAIL_SCHEMA, "cache_schema": CACHE_SCHEMA,
        "source_revision": HISTORY_SOURCE_REVISION, "train_type": train_type, "train_number": train_number,
        "origin_eva": origin_eva, "destination_eva": destination_eva,
        "window_start": start.isoformat(), "window_end": end.isoformat(),
        "planned_weekday": planned_departure.weekday() if planned_departure else None,
        "planned_time_band": planned_departure.hour // 4 if planned_departure else None,
    }

    async def produce() -> dict[str, Any]:
        async def ensure(spec: DetailSpec) -> Path:
            async with semaphore:
                return await ensure_detail_cache(spec)
        specs = [DetailSpec(train_type, train_number, year, month) for year, month in _months(start, end)]
        paths = list(await asyncio.gather(*(ensure(spec) for spec in specs)))
        adjacent = set(_months(start - timedelta(days=1), end + timedelta(days=1))) - {(s.year, s.month) for s in specs}
        current_month = datetime.now(TZ).date().replace(day=1)
        for year, month in sorted(adjacent):
            if date(year, month, 1) < current_month:
                try:
                    paths.append(await ensure(DetailSpec(train_type, train_number, year, month)))
                except Exception:
                    pass
        rows = await asyncio.to_thread(query_local_history, paths)
        samples = build_relation_samples(rows, origin_eva, destination_eva, window_start=start, window_end=end)
        return calculate_statistics(samples, window_days, planned_departure)

    return await cached_call("history.stats", key, HISTORY_STATS_TTL, produce)


def _leg_identity(leg: dict[str, Any]) -> tuple[str, str, str, str] | None:
    number = normalize_train_number(leg.get("train_number") or leg.get("fahrt_nr"))
    origin = leg.get("origin") if isinstance(leg.get("origin"), dict) else {"id": leg.get("origin_id")}
    destination = leg.get("destination") if isinstance(leg.get("destination"), dict) else {"id": leg.get("destination_id")}
    train_type = normalize_train_type(leg.get("train_type"))
    line = normalize_train_type(leg.get("line_name") or leg.get("line"))
    if train_type not in _TRAIN_TYPES and line:
        candidate = line.split(" ", 1)[0]
        train_type = candidate if candidate in _TRAIN_TYPES else None
    values = (train_type, number, normalize_eva(origin.get("id")), normalize_eva(destination.get("id")))
    return values if all(values) else None


def _is_rail_leg(leg: dict[str, Any]) -> bool:
    if leg.get("walking"):
        return False
    mode, product = str(leg.get("mode") or leg.get("type") or "").casefold(), str(leg.get("product") or "").casefold()
    if mode in {"bus", "tram", "subway", "ferry", "walking"} or product in {"bus", "tram", "subway", "ferry"}:
        return False
    return mode in {"train", "rail"} or product in {"national", "nationalexpress", "regional", "regionalexpress", "suburban"} or bool(leg.get("train_number"))


def _public_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in stats.items() if not key.startswith("_")}


def _summary(stats: dict[str, Any]) -> dict[str, Any]:
    public = _public_stats(stats)
    rate = stats.get("direct_reliability_rate")
    if stats.get("status") != "ok" or rate is None:
        return public
    percent = round(float(rate) * 100)
    return {**public, "kind": "direct", "percent": percent, "label": reliability_label(percent, "direct"), "approximate": stats.get("quality") != "good"}


def _connection(incoming: dict[str, Any], outgoing: dict[str, Any]) -> dict[str, Any]:
    stats = incoming.get("reliability") if isinstance(incoming.get("reliability"), dict) else {}
    arrival, departure = _as_datetime(incoming.get("arrival")), _as_datetime(outgoing.get("departure"))
    if not arrival or not departure or stats.get("status") != "ok":
        return {"status": "insufficient_data", "sample_count": stats.get("reliability_sample_count", 0)}
    transfer_minutes = (departure - arrival).total_seconds() / 60
    cdf = stats.get("_cdf")
    if cdf:
        delays: list[float] = []
        denominator = int(stats.get("reliability_sample_count", 0))
    else:
        delays = [float(value) for value in stats.get("_arrival_delay_samples", [])]
        denominator = len(delays) + int(stats.get("explicit_cancellations", 0))
    if transfer_minutes < 0 or denominator < 5:
        return {"status": "insufficient_data", "sample_count": denominator}
    rate = delay_index.share_within(cdf, transfer_minutes) if cdf else sum(delay <= transfer_minutes for delay in delays) / denominator
    percent = round(rate * 100)
    return {
        "status": "ok", "kind": "connection", "percent": percent,
        "label": reliability_label(percent, "connection"), "approximate": denominator < 30,
        "sample_count": denominator, "scheduled_transfer_minutes": round(transfer_minutes),
        "method": "index_arrival_cdf" if cdf else "empirical_incoming_arrival_cdf", "station": incoming.get("destination", {}).get("name") if isinstance(incoming.get("destination"), dict) else incoming.get("destination"),
    }


async def _enrich_route(route: dict[str, Any], semaphore: asyncio.Semaphore) -> dict[str, Any]:
    legs = route.get("legs")
    if not isinstance(legs, list):
        return route
    output = dict(route)

    async def enrich(leg: dict[str, Any]) -> dict[str, Any]:
        if not _is_rail_leg(leg):
            return dict(leg)
        identity = _leg_identity(leg)
        if identity is None:
            return {**leg, "reliability": {"status": "unavailable", "reason": "insufficient_train_identity"}}
        try:
            if delay_index.ENABLED:
                if delay_index.built_months():
                    indexed = await delay_index.arrival_stats(identity[0], identity[1], identity[3])
                    if indexed and "ran" in indexed:
                        return {**leg, "reliability": index_statistics(indexed)}
                else:
                    delay_index.request_build()  # der Index baut sich beim ersten Bedarf im Hintergrund auf
            stats = await asyncio.wait_for(
                _statistics_for_leg(*identity, HISTORY_DEFAULT_WINDOW_DAYS, _as_datetime(leg.get("departure")), semaphore),
                timeout=HISTORY_REMOTE_TIMEOUT,
            )
            return {**leg, "reliability": stats}
        except TimeoutError:
            reason = "history_timeout"
        except Exception as exc:
            reason = "duckdb_unavailable" if "duckdb_unavailable" in str(exc) else "history_source_unreachable"
        return {**leg, "reliability": {"status": "unavailable", "reason": reason}}

    enriched = list(await asyncio.gather(*(enrich(leg) if isinstance(leg, dict) else asyncio.sleep(0, result=leg) for leg in legs)))
    rail_indexes = [index for index, leg in enumerate(enriched) if isinstance(leg, dict) and _is_rail_leg(leg)]
    connections: list[dict[str, Any]] = []
    for previous, following in zip(rail_indexes, rail_indexes[1:]):
        connection = _connection(enriched[previous], enriched[following])
        connections.append(connection)
        enriched[following] = {**enriched[following], "connection_reliability": connection}
    public_legs = [{**leg, "reliability": _public_stats(leg["reliability"])} if isinstance(leg, dict) and isinstance(leg.get("reliability"), dict) else leg for leg in enriched]
    output["legs"] = public_legs
    if connections:
        available = [item for item in connections if item.get("status") == "ok"]
        if len(available) == len(connections):
            probability = math.prod(float(item["percent"]) / 100 for item in available)
            percent = round(probability * 100)
            output["reliability"] = {
                "status": "ok", "kind": "connection", "percent": percent,
                "label": reliability_label(percent, "connection"),
                "approximate": any(item.get("approximate") for item in available),
                "method": "independent_connection_estimate", "connections": connections,
            }
        else:
            output["reliability"] = {"status": "insufficient_data", "connections": connections}
    elif len(rail_indexes) == 1:
        output["reliability"] = _summary(enriched[rail_indexes[0]].get("reliability", {}))
    return output


async def enrich_routes_history(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not HISTORY_ENABLED:
        return routes
    semaphore = asyncio.Semaphore(HISTORY_MAX_CONCURRENCY)
    return list(await asyncio.gather(*(_enrich_route(route, semaphore) for route in routes)))
