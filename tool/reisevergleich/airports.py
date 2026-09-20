from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

from .location_resolver import has_airport_context

AIRPORT_STATIONS = {
    "BER": "Flughafen BER - Terminal 1-2",
    "LEJ": "Leipzig/Halle Flughafen",
    "DUS": "Düsseldorf Flughafen",
    "FRA": "Frankfurt(M) Flughafen Fernbf",
    "MUC": "München Flughafen Terminal",
    "HAM": "Hamburg Airport",
    "CGN": "Köln/Bonn Flughafen",
    "HAJ": "Hannover Flughafen",
    "NUE": "Nürnberg Flughafen",
    "STR": "Flughafen/Messe",
    "AMS": "Schiphol Airport",
}

AIRPORT_CITY_NAMES = {
    "BER": "Berlin",
    "LEJ": "Leipzig",
    "DBV": "Dubrovnik",
    "BCN": "Barcelona",
    "DUS": "Düsseldorf",
    "FRA": "Frankfurt",
    "MUC": "München",
    "HAM": "Hamburg",
    "CGN": "Köln",
    "HAJ": "Hannover",
    "NUE": "Nürnberg",
    "STR": "Stuttgart",
}

CITY_TRANSIT_QUERIES = {
    "madrid": "Madrid Atocha",
    "wien": "Wien Hauptbahnhof",
    "vienna": "Wien Hauptbahnhof",
    "lissabon": "Lisbon Oriente",
    "lisbon": "Lisbon Oriente",
    "barcelona": "Barcelona Sants",
    "paris": "Paris Gare du Nord",
    "rom": "Roma Termini",
    "rome": "Roma Termini",
    "athen": "Athens Central Station",
    "athens": "Athens Central Station",
}

AIRPORT_TRANSIT_QUERIES = {
    "MAD": "Madrid Barajas Airport MAD",
    "VIE": "Vienna International Airport VIE",
    "LIS": "Lisbon Humberto Delgado Airport LIS",
    "BCN": "Barcelona El Prat Airport BCN",
    "CDG": "Paris Charles de Gaulle Airport CDG",
    "LHR": "London Heathrow Airport LHR",
    "FCO": "Rome Fiumicino Airport FCO",
    "ATH": "Athens International Airport ATH",
}

AIRPORT_ALIASES = {
    "berlin brandenburg": "BER",
    "berlin": "BER",
    "ber": "BER",
    "leipzig halle": "LEJ",
    "leipzig/halle": "LEJ",
    "leipzig": "LEJ",
    "lej": "LEJ",
    "dubrovnik": "DBV",
    "dbv": "DBV",
    "barcelona": "BCN",
    "bcn": "BCN",
    "düsseldorf": "DUS",
    "duesseldorf": "DUS",
    "dusseldorf": "DUS",
    "dus": "DUS",
    "frankfurt": "FRA",
    "fra": "FRA",
    "münchen": "MUC",
    "muenchen": "MUC",
    "munchen": "MUC",
    "muc": "MUC",
    "hamburg": "HAM",
    "ham": "HAM",
    "köln": "CGN",
    "koeln": "CGN",
    "koln": "CGN",
    "cgn": "CGN",
    "hannover": "HAJ",
    "haj": "HAJ",
    "nürnberg": "NUE",
    "nuernberg": "NUE",
    "nurnberg": "NUE",
    "nue": "NUE",
    "stuttgart": "STR",
    "str": "STR",
}

AIRPORT_PROVIDER_ALIASES = {
    "BER": ("ber", "berlin", "berlin brandenburg", "brandenburg", "schönefeld", "schoenefeld"),
    "LEJ": ("lej", "leipzig", "halle", "leipzig halle"),
    "DUS": ("dus", "düsseldorf", "duesseldorf", "dusseldorf"),
    "FRA": ("fra", "frankfurt"),
    "MUC": ("muc", "münchen", "muenchen", "munchen"),
    "HAM": ("ham", "hamburg"),
    "CGN": ("cgn", "köln", "koeln", "koln", "bonn"),
    "HAJ": ("haj", "hannover"),
    "NUE": ("nue", "nürnberg", "nuernberg", "nurnberg"),
    "STR": ("str", "stuttgart", "messe"),
}

AIRPORT_PROVIDER_QUERIES = {
    "BER": "Berlin Brandenburg Airport",  # so heißt der Flughafen in Transitous; „… Flughafen BER Terminal 1-2“ wurde dort nie sicher gefunden
    "LEJ": "Leipzig Halle Flughafen",
    "DUS": "Düsseldorf Flughafen",
    "FRA": "Frankfurt Flughafen",
    "MUC": "München Flughafen",
    "HAM": "Hamburg Flughafen Airport",
    "CGN": "Köln Bonn Flughafen",
    "HAJ": "Hannover Flughafen",
    "NUE": "Nürnberg Flughafen",
    "STR": "Flughafen/Messe",  # Transitous kennt den Halt so (ohne Stadtnamen)
}

TRVL_AIRPORT_SOURCE = Path(os.getenv("TRVL_AIRPORT_SOURCE", "/usr/share/reisevergleich/trvl_airports.go"))
GERMAN_CITY_ALIASES = {
    "rom": "rome", "lissabon": "lisbon", "wien": "vienna", "prag": "prague",
    "mailand": "milan", "venedig": "venice", "neapel": "naples", "brüssel": "brussels",
    "bruessel": "brussels", "kopenhagen": "copenhagen", "stockholm": "stockholm",
    "warschau": "warsaw", "krakau": "krakow", "athen": "athens", "bukarest": "bucharest",
    "belgrad": "belgrade", "kairo": "cairo", "peking": "beijing", "tokio": "tokyo",
}
PRIMARY_CITY_AIRPORTS = {
    "london":"LHR", "paris":"CDG", "rome":"FCO", "milan":"MXP", "istanbul":"IST",
    "new york":"JFK", "tokyo":"HND", "osaka":"KIX", "beijing":"PEK", "bangkok":"BKK",
    "chicago":"ORD", "washington":"IAD", "houston":"IAH",
}

@lru_cache(maxsize=1)
def _trvl_city_airports() -> dict[str, list[str]]:
    if not TRVL_AIRPORT_SOURCE.is_file():
        return {}
    text = TRVL_AIRPORT_SOURCE.read_text(encoding="utf-8", errors="ignore")
    names_match = re.search(r"var AirportNames = map\[string\]string\{(.*?)\n\}", text, re.S)
    search_match = re.search(r"var airportSearchCities = map\[string\]string\{(.*?)\n\}", text, re.S)
    names: dict[str, str] = {}
    search: dict[str, str] = {}
    if names_match:
        names = dict(re.findall(r'"([A-Z]{3})"\s*:\s*"([^"]+)"', names_match.group(1)))
    if search_match:
        search = dict(re.findall(r'"([A-Z]{3})"\s*:\s*"([^"]+)"', search_match.group(1)))
    mapping: dict[str, list[str]] = {}
    for code, display in names.items():
        city = search.get(code, display)
        suffix = city.rsplit(" ", 1)[-1] if " " in city else ""
        if len(suffix) == 3 and suffix.isupper():
            city = city.rsplit(" ", 1)[0]
        key = _normalize(city)
        if key:
            mapping.setdefault(key, []).append(code)
    for codes in mapping.values():
        codes.sort()
    return mapping

def airports_for_city(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = _normalize(value)
    # Bahnhofssuffixe sind für die Stadt-/Flughafenauflösung irrelevant.
    normalized = re.sub(r"\b(hbf|hauptbahnhof|bahnhof)\b.*$", "", normalized).strip()
    normalized = GERMAN_CITY_ALIASES.get(normalized, normalized)
    mapping = _trvl_city_airports()
    codes = list(mapping.get(normalized, []))
    primary = PRIMARY_CITY_AIRPORTS.get(normalized)
    if primary in codes:
        codes.remove(primary)
        codes.insert(0, primary)
    return codes


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9äöüß/]+", " ", value.casefold()).strip()


def iata_for_location(value: str | None) -> str | None:
    if not value:
        return None
    literal = value.strip()
    raw = literal.upper()
    if len(raw) == 3 and raw.isalpha() and literal == raw:
        return raw
    normalized = _normalize(value)
    matches: list[tuple[int, int, str]] = []
    for alias, code in AIRPORT_ALIASES.items():
        pos = normalized.find(alias)
        if pos >= 0:
            matches.append((pos, -len(alias), code))
    return min(matches)[2] if matches else None


def resolve_origin_airports(origin: str, explicit: list[str] | None = None) -> tuple[list[str], str]:
    if explicit:
        out: list[str] = []
        for raw in explicit:
            code = str(raw).strip().upper()
            if code and code not in out:
                out.append(code)
        return out[:6], "explicit"
    inferred = iata_for_location(origin)
    if inferred:
        return [inferred], "location_map"
    city_codes = airports_for_city(origin)
    return (city_codes[:6], "trvl_airport_map") if city_codes else ([], "unresolved")


def resolve_destination_airport(destination: str, explicit: str | None = None) -> tuple[str | None, str]:
    if explicit:
        return explicit.strip().upper(), "explicit"
    inferred = iata_for_location(destination)
    if inferred:
        return inferred, "location_map"
    city_codes = airports_for_city(destination)
    return (city_codes[0], "trvl_airport_map") if city_codes else (None, "unresolved")


def resolve_feeder_airport_station(airport_iata: str, requested_station: str | None) -> tuple[str, str]:
    if requested_station and requested_station.strip():
        return requested_station.strip(), "explicit"
    return AIRPORT_STATIONS.get(airport_iata, f"Flughafen {airport_iata}"), "airport_map"




def _normalized_words(value: str | None) -> set[str]:
    if not value:
        return set()
    return set(re.sub(r"[^a-z0-9äöüß]+", " ", value.casefold()).split())


def _contains_alias(value: str | None, alias: str) -> bool:
    normalized = re.sub(r"[^a-z0-9äöüß]+", " ", str(value or "").casefold()).strip()
    alias_normalized = re.sub(r"[^a-z0-9äöüß]+", " ", alias.casefold()).strip()
    if not normalized or not alias_normalized:
        return False
    return bool(re.search(rf"(?:^|\s){re.escape(alias_normalized)}(?:$|\s)", normalized))


def airport_from_station(value: str | None) -> str | None:
    if not value:
        return None
    explicit_iata = re.search(r"(?:^|[^A-Z])([A-Z]{3})(?:$|[^A-Z])", value)
    if explicit_iata:
        return explicit_iata.group(1)
    normalized = re.sub(r"[^a-z0-9äöüß]+", " ", value.casefold()).strip()
    for code, station in AIRPORT_STATIONS.items():
        station_normalized = re.sub(r"[^a-z0-9äöüß]+", " ", station.casefold()).strip()
        if normalized == station_normalized:
            return code
    for code, aliases in AIRPORT_PROVIDER_ALIASES.items():
        if any(_contains_alias(value, alias) for alias in aliases):
            return code
    return None


def airport_identity_matches(requested_station: str | None, candidate_station: str | None) -> bool:
    """Reject a provider location that belongs to a different known airport.

    Terminal overlap is intentionally irrelevant here: Frankfurt T2 and BER T1-2
    are different airports even though both mention terminal 2.
    """
    requested_code = airport_from_station(requested_station)
    if not requested_code:
        return True
    candidate_code = airport_from_station(candidate_station)
    if candidate_code:
        return candidate_code == requested_code
    aliases = AIRPORT_PROVIDER_ALIASES.get(requested_code, ())
    if aliases:
        return any(_contains_alias(candidate_station, alias) for alias in aliases)
    generic = {"airport", "flughafen", "international", "terminal", requested_code.casefold()}
    requested_words = _normalized_words(requested_station) - generic
    candidate_words = _normalized_words(candidate_station) - generic
    return bool(requested_words and requested_words.intersection(candidate_words))


def provider_location_query(value: str | None) -> str:
    text = str(value or "").strip()
    normalized = re.sub(r"[^a-z0-9äöüß]+", " ", text.casefold()).strip()
    canonical_airport_station = any(
        normalized == re.sub(r"[^a-z0-9äöüß]+", " ", station.casefold()).strip()
        for station in AIRPORT_STATIONS.values()
    )
    # City aliases such as "Leipzig" and "Frankfurt" are useful only when the
    # request actually expresses airport intent.  Applying them to every public
    # transport search silently turns central stations into airports.
    if not (has_airport_context(text) or canonical_airport_station):
        return text
    code = airport_from_station(text)
    return AIRPORT_PROVIDER_QUERIES.get(code, text)

def provider_wall_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def flight_local_value(option: dict[str, Any], direction: str, field: str) -> datetime | None:
    section = option.get(direction) if isinstance(option.get(direction), dict) else {}
    return provider_wall_datetime(section.get(field))


def stay_return_date(option: dict[str, Any], nights: int) -> str | None:
    arrival = flight_local_value(option, "outbound", "arrival")
    if arrival is None:
        return None
    return (arrival.date() + timedelta(days=nights)).isoformat()


def return_departure_date(option: dict[str, Any]) -> str | None:
    value = flight_local_value(option, "return", "departure")
    return value.date().isoformat() if value else None


def local_cutoff(value: datetime | None, minutes_before: int) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    cutoff = value - timedelta(minutes=minutes_before)
    return cutoff.date().isoformat(), cutoff.strftime("%H:%M")
