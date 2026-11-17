from __future__ import annotations

import asyncio
import csv
import math
import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx

API_URL = os.getenv("OPENCELLID_API_URL", "https://opencellid.org/cell/getInArea")
API_KEY = os.getenv("OPENCELLID_API_KEY", "").strip()
CSV_PATH = os.getenv("OPENCELLID_CSV_PATH", "").strip()
SEARCH_RADIUS_KM = min(max(float(os.getenv("OPENCELLID_SEARCH_RADIUS_KM", "5")), 0.5), 20.0)
MAX_PROBES = min(max(int(os.getenv("OPENCELLID_MAX_PROBES", "16")), 2), 40)
LOG = logging.getLogger(__name__)

# Current German public-network assignments. Unknown MCC/MNC pairs are ignored.
# 262/23 is allocated to Drillisch Netz AG and is displayed as the 1&1 network.
OPERATOR_CODES: dict[str, set[tuple[int, int]]] = {
    "Telekom": {(262, 1), (262, 6)},
    "Vodafone": {(262, 2), (262, 4), (262, 9), (262, 42), (262, 43)},
    "Telefónica/O2": {(262, 3), (262, 5), (262, 7), (262, 8), (262, 11), (262, 12), (262, 20)},
    "1&1": {(262, 23)},
}
CODE_TO_OPERATOR = {code: operator for operator, codes in OPERATOR_CODES.items() for code in codes}
BROADBAND_RADIOS = {"LTE", "NR", "NBIOT"}


@dataclass(frozen=True)
class Cell:
    mcc: int
    mnc: int
    operator: str
    cell_id: int
    location: int | None
    radio_type: str
    latitude: float
    longitude: float
    range_m: float | None = None


def operator_for(mcc: Any, mnc: Any) -> str | None:
    try:
        return CODE_TO_OPERATOR.get((int(mcc), int(mnc)))
    except (TypeError, ValueError):
        return None


def parse_cell(row: dict[str, Any]) -> Cell | None:
    operator = operator_for(row.get("mcc"), row.get("mnc") or row.get("net"))
    radio = str(row.get("radio") or row.get("radio_type") or "").upper()
    if not operator or radio not in BROADBAND_RADIOS:
        return None
    try:
        return Cell(
            mcc=int(row["mcc"]), mnc=int(row.get("mnc") or row.get("net")), operator=operator,
            cell_id=int(row.get("cellid") or row.get("cell") or row.get("cell_id")),
            location=int(row.get("area") or row.get("lac") or row.get("tac")) if (row.get("area") or row.get("lac") or row.get("tac")) not in (None, "") else None,
            radio_type=radio, latitude=float(row.get("lat") or row.get("latitude")),
            longitude=float(row.get("lon") or row.get("longitude")),
            range_m=float(row["range"]) if row.get("range") not in (None, "") else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _distance_km(a: dict[str, Any], cell: Cell) -> float:
    lat1, lat2 = math.radians(float(a["latitude"])), math.radians(cell.latitude)
    dlat = lat2 - lat1
    dlon = math.radians(cell.longitude - float(a["longitude"]))
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(h))


def _weak_sections(points: list[dict[str, Any]], covered: list[bool]) -> list[dict[str, Any]]:
    gaps, start = [], None
    for index, value in enumerate([*covered, True]):
        if not value and start is None:
            start = index
        elif value and start is not None:
            end = index - 1
            distance = float(points[end].get("distance_km", end)) - float(points[start].get("distance_km", start))
            if distance >= 3:
                gaps.append({"from_km": round(float(points[start].get("distance_km", start)), 1), "to_km": round(float(points[end].get("distance_km", end)), 1), "length_km": round(distance, 1)})
            start = None
    return sorted(gaps, key=lambda item: item["length_km"], reverse=True)[:5]


def calculate(points: list[dict[str, Any]], cells: Iterable[Cell]) -> list[dict[str, Any]]:
    cells_by_operator = {name: [] for name in OPERATOR_CODES}
    for cell in cells:
        if cell.operator in cells_by_operator:
            cells_by_operator[cell.operator].append(cell)
    all_cells = [cell for values in cells_by_operator.values() for cell in values]
    spatial_hits = sum(any(_distance_km(point, cell) <= SEARCH_RADIUS_KM for cell in all_cells) for point in points)
    if len(all_cells) < 3 or not points or spatial_hits / len(points) < 0.25:
        return []
    output = []
    for operator, operator_cells in cells_by_operator.items():
        if not operator_cells:
            continue
        covered = []
        for point in points:
            covered.append(any(
                _distance_km(point, cell) <= max(SEARCH_RADIUS_KM, (cell.range_m or 0) / 1000)
                for cell in operator_cells
            ))
        output.append({
            "id": operator.casefold().replace("/", "_"), "name": operator,
            "coverage_percent": round(100 * sum(covered) / len(covered)) if covered else None,
            "evaluated_points": len(covered), "cell_count": len(operator_cells),
            "weak_sections": _weak_sections(points, covered),
            "data_quality": "good" if spatial_hits / len(points) >= 0.75 else "limited",
        })
    return output


def _load_csv(points: list[dict[str, Any]]) -> tuple[list[Cell], set[tuple[int, int]]]:
    if not CSV_PATH or not Path(CSV_PATH).is_file():
        return [], set()
    margin = SEARCH_RADIUS_KM / 80
    latitudes = [float(point["latitude"]) for point in points]
    longitudes = [float(point["longitude"]) for point in points]
    bounds = min(latitudes)-margin, min(longitudes)-margin, max(latitudes)+margin, max(longitudes)+margin
    cells = []
    unknown: set[tuple[int, int]] = set()
    with Path(CSV_PATH).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                latitude, longitude = float(row.get("lat") or row.get("latitude")), float(row.get("lon") or row.get("longitude"))
                code = (int(row.get("mcc")), int(row.get("mnc") or row.get("net")))
            except (TypeError, ValueError):
                continue
            if not (bounds[0] <= latitude <= bounds[2] and bounds[1] <= longitude <= bounds[3]):
                continue
            if code not in CODE_TO_OPERATOR:
                unknown.add(code)
                continue
            cell = parse_cell(row)
            if cell:
                cells.append(cell)
    return cells, unknown


def _probe_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(points) <= MAX_PROBES:
        return points
    return [points[round(index * (len(points) - 1) / (MAX_PROBES - 1))] for index in range(MAX_PROBES)]


async def _api_cells(points: list[dict[str, Any]]) -> tuple[list[Cell], int]:
    if not API_KEY:
        return [], 0
    semaphore = asyncio.Semaphore(4)
    margin = SEARCH_RADIUS_KM / 111

    async def fetch(client: httpx.AsyncClient, point: dict[str, Any], mnc: int) -> list[Cell]:
        bbox = f'{float(point["latitude"])-margin},{float(point["longitude"])-margin},{float(point["latitude"])+margin},{float(point["longitude"])+margin}'
        async with semaphore:
            response = await client.get(API_URL, params={"key": API_KEY, "BBOX": bbox, "mcc": 262, "mnc": mnc, "limit": 50, "format": "json"})
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("cells", []) if isinstance(payload, dict) else []
        return [cell for row in rows if isinstance(row, dict) and (cell := parse_cell(row))]

    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
        batches = await asyncio.gather(*(
            fetch(client, point, mnc)
            for point in _probe_points(points)
            for mnc in (1, 2, 3, 23)
        ), return_exceptions=True)
    unique: dict[tuple[int, int, int, str], Cell] = {}
    errors = 0
    for batch in batches:
        if isinstance(batch, list):
            for cell in batch:
                unique[(cell.mcc, cell.mnc, cell.cell_id, cell.radio_type)] = cell
        else:
            errors += 1
    return list(unique.values()), errors


async def sample_operators(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Return evidenced values plus internal, identifier-free diagnostics."""
    cells, unknown = await asyncio.to_thread(_load_csv, points)
    source = "csv" if cells or (CSV_PATH and Path(CSV_PATH).is_file()) else "not_configured"
    api_errors = 0
    if not cells:
        cells, api_errors = await _api_cells(points)
        if API_KEY:
            source = "api"
    networks = calculate(points, cells)
    debug = {
        "source": source,
        "evaluated_points": len(points),
        "cells_found": len(cells),
        "recognized_operators": [item["name"] for item in networks],
        "unknown_mcc_mnc": [f"{mcc}-{mnc:02d}" for mcc, mnc in sorted(unknown)],
        "api_errors": api_errors,
        "data_quality": "good" if networks and all(item.get("data_quality") == "good" for item in networks) else "limited" if networks else "insufficient",
    }
    LOG.info(
        "OpenCellID route sample: points=%d cells=%d operators=%s unknown_codes=%s source=%s quality=%s api_errors=%d",
        debug["evaluated_points"], debug["cells_found"], debug["recognized_operators"],
        debug["unknown_mcc_mnc"], source, debug["data_quality"], api_errors,
    )
    return {"networks": networks, "debug": debug}
