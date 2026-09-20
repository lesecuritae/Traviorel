from __future__ import annotations

import asyncio
from datetime import date, timedelta

import os

# Diese Tests prüfen den trvl-Weg (und den Rückfall darauf); die eigenen Direktwege sind in test_flix_api.py und
# test_skiplagged_fx.py abgedeckt und dürfen hier nicht ins Netz gehen.
for _name in ("FLIX_NATIVE", "FLIGHT_NATIVE", "HOTEL_NATIVE"):
    os.environ[_name] = "0"

from reisevergleich import trvl
from reisevergleich.models import FlightRequest, HotelRequest, ReiseRequest

future = (date.today() + timedelta(days=35)).isoformat()
ret = (date.today() + timedelta(days=42)).isoformat()

commands: list[list[str]] = []


def timeout_result(command):
    return {"ok": False, "error": "Zeitüberschreitung", "raw": {"code": 124, "command": command}}


def fake_run_json(command: list[str], timeout: int):
    commands.append(list(command))
    if command[1] == "flights":
        provider = command[command.index("--provider") + 1]
        if provider == "skiplagged":
            return {
                "ok": True,
                "data": {
                    "flights": [{
                        "price": 99.0,
                        "currency": "EUR",
                        "duration": 300,
                        "provider": "Skiplagged",
                        "legs": [
                            {"departure_airport": {"code": "AAA"}, "arrival_airport": {"code": "BBB"}, "departure_time": f"{future}T10:00:00+02:00", "arrival_time": f"{future}T12:30:00+02:00", "direction": "outbound", "airline": "X"},
                            {"departure_airport": {"code": "BBB"}, "arrival_airport": {"code": "AAA"}, "departure_time": f"{ret}T10:00:00+02:00", "arrival_time": f"{ret}T12:30:00+02:00", "direction": "inbound", "airline": "X"},
                        ],
                    }]
                },
                "raw": {"code": 0, "command": command},
            }
        if provider == "ryanair":
            return timeout_result(command)
        return {"ok": True, "data": {"flights": []}, "raw": {"code": 0, "command": command}}

    if command[1] == "hotels":
        if "--enrich-rooms=false" not in command:
            return timeout_result(command)
        return {
            "ok": True,
            "data": {"hotels": [{"name": "Test Hotel", "stars": 3, "price": 80, "price_basis": "nightly", "currency": "EUR"}]},
            "raw": {"code": 0, "command": command},
        }

    if command[1] == "ground" and "--provider" in command:
        provider = command[command.index("--provider") + 1]
        if provider == "flixbus":
            return timeout_result(command)
        if provider == "transitous":
            return {
                "ok": True,
                "data": {"routes": [{
                    "provider": "Transitous", "type": "train", "price": 5, "currency": "EUR",
                    "departure": {"station": "Center", "time": f"{future}T08:00:00+02:00"},
                    "arrival": {"station": "Airport", "time": f"{future}T08:30:00+02:00"},
                    "duration_minutes": 30,
                }]},
                "raw": {"code": 0, "command": command},
            }
        return {"ok": True, "data": {"routes": []}, "raw": {"code": 0, "command": command}}

    if command[1] == "airport-transfer":
        provider = command[command.index("--provider") + 1]
        if provider == "transitous":
            return {
                "ok": True,
                "data": {"routes": [{
                    "provider": "Transitous", "type": "metro", "price": 5, "currency": "EUR",
                    "departure": {"station": "BBB", "time": f"{future}T13:00:00+02:00"},
                    "arrival": {"station": "Zielstadt", "time": f"{future}T13:30:00+02:00"},
                    "duration_minutes": 30,
                }]},
                "raw": {"code": 0, "command": command},
            }
        return timeout_result(command)

    if command[1] == "route":
        return timeout_result(command)

    raise AssertionError(command)


def fake_flix_routes(command: list[str], timeout: int):
    return {
        "ok": True,
        "data": {"routes": [
            {"provider": "flixbus", "type": "bus", "departure": {"city": "Frankfurt", "time": f"{future}T09:00:00+02:00"}, "arrival": {"city": "Berlin", "time": f"{future}T17:00:00+02:00"}, "duration_minutes": 480, "price": 19.99, "currency": "EUR"},
            {"provider": "flixbus", "type": "bus", "departure": {"city": "Frankfurt", "time": f"{future}T10:00:00+02:00"}, "arrival": {"city": "Cologne", "time": f"{future}T12:30:00+02:00"}, "duration_minutes": 150, "price": 24.99, "currency": "EUR"},
        ]},
        "raw": {"code": 0, "command": command},
    }


async def main():
    original = trvl.run_json_command
    trvl.run_json_command = fake_run_json
    try:
        flight = await trvl.flight_search(FlightRequest(
            origin_iata="AAA", destination_iata="BBB", departure_date=future, return_date=ret, max_results=3,
        ))
        assert flight["status"] == "ok", flight
        assert len(flight["flight_options"]) == 1, flight
        assert flight["provider_summary"]["default_aggregate_used"] is False
        assert any(x["provider"] == "ryanair" and x["timed_out"] for x in flight["provider_statuses"])
        flight_commands = [c for c in commands if c[1] == "flights"]
        assert flight_commands and all("--provider" in c for c in flight_commands), flight_commands

        hotel = await trvl.hotel_search(HotelRequest(
            location="Zielstadt", checkin_date=future, checkout_date=ret, min_stars=3, property_type="hotel", max_results=3,
        ))
        assert hotel["status"] == "ok", hotel
        assert hotel["hotel_options"][0]["name"] == "Test Hotel"
        assert hotel["provider_statuses"][0]["timed_out"] is True
        assert hotel["provider_statuses"][1]["ok"] is True

        transfer = await trvl.airport_transfer_search("BBB", "Zielstadt", future, arrival_after="12:30", max_results=3)
        assert transfer["status"] == "ok", transfer
        assert transfer["options"][0]["provider"] == "Transitous"
        assert any(x["timed_out"] for x in transfer["provider_statuses"])

        back = await trvl.return_transfer_search("Zielstadt", "BBB", future, arrive_before="09:00", max_results=3)
        assert back["status"] == "ok", back
        assert back["options"][0]["provider"] == "Transitous"

        flix = await trvl.flix_search(ReiseRequest(
            origin="Startstadt", destination="Zielstadt", travel_date=future, max_results=3,
        ))
        assert flix["status"] == "empty"
        assert flix["provider_status"]["timed_out"] is True

        trvl.run_json_command = fake_flix_routes
        flix = await trvl.flix_search(ReiseRequest(
            origin="Frankfurt(Main)Hbf", destination="Köln Hbf", travel_date=future, max_results=3,
        ))
        assert flix["status"] == "ok", flix
        assert len(flix["routes"]) == 1, flix
        assert flix["routes"][0]["arrival"]["city"] == "Cologne", flix
    finally:
        trvl.run_json_command = original

    print("Provider-Isolation und harte Einzel-Timeouts: OK")


asyncio.run(main())
