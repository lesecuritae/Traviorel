from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import TZ, today_iso

# Free-text place names. Station search already caps its query at 120; the search models
# had no bound, and stop scoring cost grew with the query length for every stop row.
PLACE_MAX_LENGTH = 120


def reject_option_like(value: str, label: str) -> str:
    # Place names are passed to the trvl CLI as positional arguments. A value starting with
    # '-' would be parsed as a flag instead; no real place name starts that way.
    if value.startswith("-"):
        raise ValueError(f"{label} darf nicht mit '-' beginnen")
    return value


class StationSelection(BaseModel):
    """A user-confirmed stop with its provider-native identifier."""

    name: str = Field(min_length=1, max_length=180)
    provider: Literal["db", "transitous", "flix"]
    provider_id: str = Field(min_length=1, max_length=240)
    provider_ids: dict[str, str] = Field(default_factory=dict)
    provider_alias_ids: dict[str, list[str]] = Field(default_factory=dict)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def include_primary_provider(self):
        self.provider_ids = {
            key: value for key, value in self.provider_ids.items()
            if key in {"db", "transitous", "flix"} and isinstance(value, str) and value.strip()
        }
        self.provider_ids[self.provider] = self.provider_id
        self.provider_alias_ids = {
            key: list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))
            for key, values in self.provider_alias_ids.items()
            if key in {"db", "transitous", "flix"} and isinstance(values, list)
        }
        for key, value in self.provider_ids.items():
            self.provider_alias_ids[key] = list(dict.fromkeys([value, *self.provider_alias_ids.get(key, [])]))
        return self

    def id_for(self, provider: str) -> str | None:
        return self.provider_ids.get(provider)

    def ids_for(self, provider: str) -> list[str]:
        return self.provider_alias_ids.get(provider) or ([self.provider_ids[provider]] if provider in self.provider_ids else [])


class WarningRouteRequest(BaseModel):
    routes: list[dict[str, Any]] = Field(min_length=1, max_length=8)


class TravelWarning(BaseModel):
    id: str
    title: str
    description: str | None = None
    location: str | None = None
    severity: str | None = None
    source: Literal["NINA/BBK"] = "NINA/BBK"
    issuer: str | None = None
    starts_at: str | None = None
    expires_at: str | None = None
    affected_stops: list[str] = Field(default_factory=list)


def _future_or_today(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    parsed = date.fromisoformat(value)
    today = date.fromisoformat(today_iso())
    if parsed < today:
        raise ValueError(
            f"{field_name} liegt in der Vergangenheit ({value}). Heute ist {today.isoformat()}. "
            "Historische Fahrplansuchen werden nicht unterstützt."
        )
    return value


class ReiseRequest(BaseModel):
    origin: str = Field(max_length=PLACE_MAX_LENGTH, description="Startort oder Bahnhof")
    destination: str = Field(max_length=PLACE_MAX_LENGTH, description="Zielort oder Bahnhof")
    travel_date: str = Field(description="Reisedatum YYYY-MM-DD; kein Datum in der Vergangenheit verwenden")
    departure_after: str = Field(default="06:00", description="Früheste Abfahrt HH:MM")
    preference: Literal["balanced", "cheapest", "fastest", "fewest_transfers"] = "balanced"
    include_train: bool = True
    include_bus: bool = True
    include_flixtrain: bool = True
    include_flixbus: bool = True
    max_transfers: int | None = Field(default=None, ge=0, le=8)
    max_results: int = Field(default=24, ge=1, le=48)
    split_ticket_check: bool = True
    deutschlandticket: bool = False
    flix_origin_stop_id: str | None = None
    flix_destination_stop_id: str | None = None
    origin_station: StationSelection | None = None
    destination_station: StationSelection | None = None

    @field_validator("origin", "destination")
    @classmethod
    def station_not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Start und Ziel dürfen nicht leer sein")
        return reject_option_like(value, "Start und Ziel")

    @field_validator("travel_date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        return str(_future_or_today(value, "travel_date"))

    @field_validator("departure_after")
    @classmethod
    def valid_time(cls, value: str) -> str:
        datetime.strptime(value, "%H:%M")
        return value

    @field_validator("preference", mode="before")
    @classmethod
    def normalize_preference(cls, value: Any) -> str:
        normalized = re.sub(r"[\s-]+", "_", str(value or "balanced").strip().casefold())
        aliases = {
            "price": "cheapest", "preis": "cheapest", "günstig": "cheapest", "guenstig": "cheapest",
            "billig": "cheapest", "cheap": "cheapest", "cost": "cheapest",
            "time": "fastest", "duration": "fastest", "schnell": "fastest", "fast": "fastest",
            "transfers": "fewest_transfers", "umstiege": "fewest_transfers", "direct": "fewest_transfers",
            "ausgewogen": "balanced",
        }
        return aliases.get(normalized, normalized)

    @model_validator(mode="after")
    def different_stations(self):
        if not self.include_train and not self.include_bus:
            raise ValueError("Mindestens Zug oder Bus muss ausgewählt sein")
        if self.origin.casefold() == self.destination.casefold():
            raise ValueError("Start und Ziel müssen verschieden sein")
        if self.origin_station and self.origin.casefold() != self.origin_station.name.casefold():
            raise ValueError("origin muss der ausgewählten origin_station entsprechen")
        if self.destination_station and self.destination.casefold() != self.destination_station.name.casefold():
            raise ValueError("destination muss der ausgewählten destination_station entsprechen")
        return self


class DeutschlandticketRequest(BaseModel):
    origin: str = Field(max_length=PLACE_MAX_LENGTH)
    destination: str = Field(max_length=PLACE_MAX_LENGTH)
    travel_date: str
    departure_after: str = "06:00"
    max_transfers: int | None = Field(default=None, ge=0, le=10)
    max_results: int = Field(default=24, ge=1, le=48)

    @field_validator("origin", "destination")
    @classmethod
    def station_not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Start und Ziel dürfen nicht leer sein")
        return reject_option_like(value, "Start und Ziel")

    @field_validator("travel_date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        return str(_future_or_today(value, "travel_date"))

    @field_validator("departure_after")
    @classmethod
    def valid_time(cls, value: str) -> str:
        datetime.strptime(value, "%H:%M")
        return value


class FlightRequest(BaseModel):
    origin_iata: str = Field(min_length=3, max_length=3)
    destination_iata: str = Field(min_length=3, max_length=3)
    departure_date: str
    return_date: str | None = None
    adults: int = Field(default=1, ge=1, le=9)
    cabin: Literal["economy", "premium_economy", "business", "first"] = "economy"
    stops: Literal["any", "nonstop", "one_stop", "two_plus"] = "any"
    max_price: int | None = Field(default=None, gt=0)
    max_results: int = Field(default=6, ge=1, le=10)

    @field_validator("origin_iata", "destination_iata")
    @classmethod
    def valid_iata(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("IATA-Code muss aus genau drei Buchstaben bestehen")
        return value

    @field_validator("departure_date")
    @classmethod
    def valid_departure(cls, value: str) -> str:
        return str(_future_or_today(value, "departure_date"))

    @field_validator("return_date")
    @classmethod
    def valid_return(cls, value: str | None) -> str | None:
        return _future_or_today(value, "return_date")

    @model_validator(mode="after")
    def validate_route(self):
        if self.origin_iata == self.destination_iata:
            raise ValueError("Start- und Zielflughafen müssen verschieden sein")
        if self.return_date and date.fromisoformat(self.return_date) < date.fromisoformat(self.departure_date):
            raise ValueError("return_date darf nicht vor departure_date liegen")
        return self


class HotelRequest(BaseModel):
    location: str = Field(max_length=PLACE_MAX_LENGTH)
    checkin_date: str
    checkout_date: str
    adults: int = Field(default=1, ge=1, le=12)
    min_rating: float | None = Field(default=None, ge=0, le=10)
    max_nightly_price: float | None = Field(default=None, gt=0)
    min_stars: int = Field(default=3, ge=0, le=5)
    property_type: Literal["hotel", "apartment", "hostel", "resort", "bnb", "villa", "any"] = "hotel"
    max_results: int = Field(default=6, ge=1, le=10)

    @field_validator("location")
    @classmethod
    def valid_location(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Hotelort darf nicht leer sein")
        return reject_option_like(value, "Hotelort")

    @field_validator("checkin_date")
    @classmethod
    def valid_checkin(cls, value: str) -> str:
        return str(_future_or_today(value, "checkin_date"))

    @field_validator("checkout_date")
    @classmethod
    def valid_checkout(cls, value: str) -> str:
        return str(_future_or_today(value, "checkout_date"))

    @model_validator(mode="after")
    def validate_stay(self):
        if date.fromisoformat(self.checkout_date) <= date.fromisoformat(self.checkin_date):
            raise ValueError("checkout_date muss nach checkin_date liegen")
        return self


class TripRequest(BaseModel):
    """Strukturierte Eingabe für eine komplette Reise."""

    travel_mode: Literal["flight", "ground"] = "flight"
    journey_type: Literal["round_trip", "one_way"] = "round_trip"
    origin: str = Field(max_length=PLACE_MAX_LENGTH, description="Startort oder Startbahnhof")
    destination: str = Field(max_length=PLACE_MAX_LENGTH, description="Zielort")
    departure_date: str = Field(description="Abreisetag YYYY-MM-DD")
    departure_after: str = Field(default="06:00", description="Früheste Abfahrt zum Flughafen HH:MM")

    return_mode: Literal["duration", "date"] = Field(
        default="duration",
        description="Rückreise entweder aus einer Dauer ableiten oder ein konkretes Datum verwenden.",
    )
    duration_value: int | None = Field(default=7, ge=0, le=61)
    duration_unit: Literal["days", "nights", "weeks"] = "nights"
    return_date: str | None = Field(default=None, description="Konkreter Rückreisetag bei return_mode=date")

    deutschlandticket: bool = Field(
        default=False,
        description=(
            "Wenn true, werden Deutschlandticket-Verbindungen und Mischkombinationen mit bezahltem "
            "Fernverkehr/Flix berücksichtigt und in der Zubringeransicht bevorzugt."
        ),
    )
    deutschlandticket_only: bool = Field(
        default=False,
        description="Nur vollständig vom vorhandenen Deutschlandticket abgedeckte Bodenverbindungen zulassen.",
    )
    include_feeder: bool = True
    include_train: bool = True
    include_bus: bool = True
    include_flixtrain: bool = True
    include_flixbus: bool = True
    split_ticket_check: bool = True
    # Bounded before the validator runs: it keeps 8, and dedup cost grew with the raw list.
    feeder_split_candidates: list[str] = Field(default_factory=list, max_length=32)
    feeder_transfer_minutes: int = Field(default=15, ge=5, le=60)
    feeder_preference: Literal["dticket_first", "cheapest", "fastest", "balanced"] = "dticket_first"

    origin_airports: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Optionale IATA-Abflughäfen. Leer = deterministisch aus origin ableiten.",
    )
    destination_airport: str | None = Field(
        default=None,
        description="Optionaler IATA-Zielflughafen. Leer = deterministisch aus destination ableiten.",
    )
    feeder_airport_station: str | None = None

    adults: int = Field(default=1, ge=1, le=9)
    cabin: Literal["economy", "premium_economy", "business", "first"] = "economy"
    stops: Literal["any", "nonstop", "one_stop", "two_plus"] = "any"
    flight_max_price: int | None = Field(default=None, gt=0)

    include_hotel: bool = True
    hotel_property_type: Literal["hotel", "apartment", "hostel", "resort", "bnb", "villa", "any", "none"] = "hotel"
    hotel_min_stars: int = Field(default=3, ge=0, le=5)
    hotel_min_rating: float | None = Field(default=None, ge=0, le=10)
    hotel_max_nightly_price: float | None = Field(default=None, gt=0)
    include_destination_transfer: bool = True

    airport_buffer_minutes: int = Field(default=120, ge=90, le=360)
    destination_airport_buffer_minutes: int = Field(default=120, ge=60, le=360)
    return_airport_buffer_minutes: int = Field(default=60, ge=30, le=240)

    max_results: int = Field(default=24, ge=1, le=48)
    flix_origin_stop_id: str | None = None
    flix_destination_stop_id: str | None = None
    origin_station: StationSelection | None = None
    destination_station: StationSelection | None = None
    refresh_cache: bool = False

    @field_validator("origin", "destination")
    @classmethod
    def required_location(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Start und Ziel dürfen nicht leer sein")
        return reject_option_like(value, "Start und Ziel")

    @field_validator("departure_date", "return_date")
    @classmethod
    def valid_trip_dates(cls, value: str | None, info) -> str | None:
        return _future_or_today(value, info.field_name)

    @field_validator("departure_after")
    @classmethod
    def valid_departure_time(cls, value: str) -> str:
        datetime.strptime(value, "%H:%M")
        return value

    @field_validator("origin_airports")
    @classmethod
    def valid_origin_airports(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in values:
            code = str(raw).strip().upper()
            if len(code) != 3 or not code.isalpha():
                raise ValueError("Jeder Abflughafen muss ein dreistelliger IATA-Code sein")
            if code not in seen:
                seen.add(code)
                out.append(code)
        return out[:6]

    @field_validator("destination_airport")
    @classmethod
    def valid_destination_airport(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("destination_airport muss ein dreistelliger IATA-Code sein")
        return code

    @field_validator("feeder_split_candidates")
    @classmethod
    def valid_split_candidates(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in values:
            station = str(raw).strip()
            if station and station.casefold() not in seen:
                seen.add(station.casefold())
                out.append(station)
                if len(out) == 8:
                    break
        return out

    @model_validator(mode="after")
    def validate_trip(self):
        if self.travel_mode == "ground" and not self.include_train and not self.include_bus:
            raise ValueError("Mindestens Zug oder Bus muss ausgewählt sein")
        if self.travel_mode == "ground" and bool(self.origin_station) != bool(self.destination_station):
            raise ValueError("Start- und Zielstation müssen gemeinsam ausgewählt werden")
        if self.origin_station and self.origin.casefold() != self.origin_station.name.casefold():
            raise ValueError("origin muss der ausgewählten origin_station entsprechen")
        if self.destination_station and self.destination.casefold() != self.destination_station.name.casefold():
            raise ValueError("destination muss der ausgewählten destination_station entsprechen")
        if self.deutschlandticket_only and not self.deutschlandticket:
            raise ValueError("deutschlandticket_only setzt deutschlandticket=true voraus")
        if self.journey_type == "one_way":
            self.return_date = None
            self.duration_value = 0
            self.include_hotel = False
        elif self.return_mode == "date":
            if not self.return_date:
                raise ValueError("Bei return_mode=date ist return_date erforderlich")
            if date.fromisoformat(self.return_date) <= date.fromisoformat(self.departure_date):
                raise ValueError("return_date muss nach departure_date liegen")
        else:
            if self.duration_value is None:
                raise ValueError("Bei return_mode=duration ist duration_value erforderlich")
            if self.duration_unit == "days" and self.duration_value < 2:
                raise ValueError("Eine Reise in Tagen muss mindestens 2 Tage umfassen")
            if self.duration_unit == "weeks" and self.duration_value * 7 > 60:
                raise ValueError("Die Aufenthaltsdauer darf höchstens 60 Nächte betragen")
            if self.duration_unit == "nights" and self.duration_value > 60:
                raise ValueError("Die Aufenthaltsdauer darf höchstens 60 Nächte betragen")
            if self.duration_unit == "days" and self.duration_value > 61:
                raise ValueError("Die Reisedauer darf höchstens 61 Tage betragen")

        # Für Hotel/Hostel/Apartment sind Sterne nicht gleichermaßen sinnvoll.
        if self.hotel_property_type in {"hostel", "apartment", "bnb", "villa", "any"} and self.hotel_min_stars == 3:
            self.hotel_min_stars = 0
        return self

    @property
    def stay_nights(self) -> int | None:
        if self.journey_type == "one_way":
            return 0
        if self.return_mode != "duration" or self.duration_value is None:
            return None
        if self.duration_unit == "weeks":
            return self.duration_value * 7
        if self.duration_unit == "days":
            # "8 Tage" entspricht sieben Übernachtungen. Wer exakt acht
            # Übernachtungen möchte, wählt die Einheit "Nächte".
            return max(1, self.duration_value - 1)
        return self.duration_value

    @property
    def effective_feeder_preference(self) -> str:
        if self.deutschlandticket and self.feeder_preference == "dticket_first":
            return "dticket_first"
        if self.feeder_preference == "dticket_first":
            return "cheapest"
        return self.feeder_preference


class PriceCalendarRequest(TripRequest):
    """A bounded multi-day comparison using the regular ground search."""

    calendar_days: int = Field(default=7, ge=1, le=14)

    @model_validator(mode="after")
    def ground_only(self):
        if self.travel_mode != "ground":
            raise ValueError("Die flexible Preissuche ist nur für Bodenreisen verfügbar")
        return self


class CoverageRequest(BaseModel):
    """Provider-neutral route snapshot used by the optional coverage analyzer."""

    route: dict[str, Any]
