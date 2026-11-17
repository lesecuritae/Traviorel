from __future__ import annotations

import math
import struct
from importlib.resources import files
from typing import Any

SOURCE_NAME = "Bundesnetzagentur Mobilfunk-Monitoring"
SOURCE_URL = "https://gigabitgrundbuch.bund.de/GIGA/DE/MobilfunkMonitoring/Downloads/start.html"
SOURCE_DOWNLOAD_URL = "https://data.bundesnetzagentur.de/Bundesnetzagentur/GIGA/DE/MobilfunkMonitoring/2512/202601_MobilfunkMonitoring.zip"
SOURCE_REVISION = "2025-12"
SOURCE_LICENSE = "Datenlizenz Deutschland – Namensnennung – Version 2.0"
SOURCE_LICENSE_URL = "https://www.govdata.de/dl-de/by-2-0"
SOURCE_ATTRIBUTION = "© Bundesnetzagentur; Rasterbasis © GeoBasis-DE / BKG (2025)"
OPERATORS_CONSIDERED = ["Telekom", "Vodafone", "Telefónica (O2)", "1&1"]
PROVIDERS = {
    "at_least_one": ("Mindestens 1 Netz", 1),
    "at_least_two": ("Mindestens 2 Netze", 2),
    "at_least_three": ("Mindestens 3 Netze", 3),
}

_MAGIC = b"FWCOV01\0"
_HEADER_SIZE = 24
_DATA_RESOURCE = files("reisevergleich.coverage").joinpath("data", "mobile_broadband_2025_12.fwcov")
_dataset: tuple[bytes, int, int, int, int] | None = None


def _utm32(latitude: float, longitude: float) -> tuple[float, float]:
    """WGS84 to EPSG:25832, the reference system of the official grid."""
    a, eccentricity = 6378137.0, 0.00669438002290
    k0, central = 0.9996, math.radians(9.0)
    lat, lon = math.radians(latitude), math.radians(longitude)
    n = a / math.sqrt(1 - eccentricity * math.sin(lat) ** 2)
    t = math.tan(lat) ** 2
    c = eccentricity / (1 - eccentricity) * math.cos(lat) ** 2
    aa = math.cos(lat) * (lon - central)
    m = a * ((1 - eccentricity / 4 - 3 * eccentricity**2 / 64 - 5 * eccentricity**3 / 256) * lat
        - (3 * eccentricity / 8 + 3 * eccentricity**2 / 32 + 45 * eccentricity**3 / 1024) * math.sin(2 * lat)
        + (15 * eccentricity**2 / 256 + 45 * eccentricity**3 / 1024) * math.sin(4 * lat)
        - (35 * eccentricity**3 / 3072) * math.sin(6 * lat))
    x = 500000 + k0 * n * (aa + (1 - t + c) * aa**3 / 6 + (5 - 18*t + t*t + 72*c - 58*eccentricity/(1-eccentricity)) * aa**5 / 120)
    y = k0 * (m + n * math.tan(lat) * (aa**2 / 2 + (5 - t + 9*c + 4*c*c) * aa**4 / 24 + (61 - 58*t + t*t + 600*c - 330*eccentricity/(1-eccentricity)) * aa**6 / 720))
    return x, y


def _load() -> tuple[bytes, int, int, int, int]:
    global _dataset
    if _dataset is not None:
        return _dataset
    payload = _DATA_RESOURCE.read_bytes()
    if payload[:8] != _MAGIC or len(payload) < _HEADER_SIZE:
        raise ValueError("invalid Traviorel coverage dataset")
    min_north, max_north, min_east, max_east = struct.unpack_from("<4I", payload, 8)
    cells = (max_north - min_north + 1) * (max_east - min_east + 1)
    if len(payload) != _HEADER_SIZE + (cells + 1) // 2:
        raise ValueError("Traviorel coverage dataset size does not match its header")
    _dataset = payload, min_north, max_north, min_east, max_east
    return _dataset


def network_count(point: dict[str, Any]) -> int | None:
    latitude, longitude = float(point["latitude"]), float(point["longitude"])
    if not (47.0 <= latitude <= 55.2 and 5.5 <= longitude <= 15.6):
        return None
    x, y = _utm32(latitude, longitude)
    north, east = math.floor(y / 100), math.floor(x / 100)
    payload, min_north, max_north, min_east, max_east = _load()
    if not (min_north <= north <= max_north and min_east <= east <= max_east):
        return None
    width = max_east - min_east + 1
    index = (north - min_north) * width + (east - min_east)
    packed = payload[_HEADER_SIZE + index // 2]
    value = (packed >> 4) & 0x0F if index % 2 else packed & 0x0F
    return None if value == 15 else value


async def sample(points: list[dict[str, Any]]) -> dict[str, list[bool | None]]:
    counts = [network_count(point) for point in points]
    return {
        key: [None if count is None else count >= threshold for count in counts]
        for key, (_, threshold) in PROVIDERS.items()
    }
