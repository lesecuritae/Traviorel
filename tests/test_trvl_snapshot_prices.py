"""Synthetic CLI-contract fixtures; no live fares or provider access."""
from reisevergleich.trvl import compact_flight_options, compact_hotel_options


def test_corrected_afklm_round_trip_total_survives_our_adapter():
    # Upstream's old bug selected the outbound share (105.53), rather than
    # the product total. Our adapter must retain the corrected ticket price.
    rows = compact_flight_options({"flights": [{
        "provider": "afklm", "price": 229.06, "currency": "EUR", "fare_type": "round_trip",
        "legs": [
            {"direction": "outbound", "origin": "AAA", "destination": "BBB",
             "departure": "2030-09-15T10:00:00Z", "arrival": "2030-09-15T12:00:00Z"},
            {"direction": "inbound", "origin": "BBB", "destination": "AAA",
             "departure": "2030-09-22T10:00:00Z", "arrival": "2030-09-22T12:00:00Z"},
        ],
    }]}, "AAA", "BBB", 4)
    assert len(rows) == 1
    assert rows[0]["price"] == 229.06
    assert rows[0]["currency"] == "EUR"
    assert rows[0]["outbound"] and rows[0]["return"]


def test_hotel_room_total_is_preserved_separately_from_nightly_rate():
    rows = compact_hotel_options({"hotels": [{
        "name": "Fixture Hotel", "price": 80, "currency": "EUR", "price_basis": "nightly",
        "room_types": [{"nightly_price": 80, "total_price": 240}],
    }]}, 4)
    assert rows[0]["verified_total_price"] == 240
    assert rows[0]["nightly_price"] == 80


def test_hotel_headline_without_total_is_not_promoted_to_verified_stay_price():
    rows = compact_hotel_options({"hotels": [{
        "name": "Fixture Headline", "price": 80, "currency": "EUR", "price_basis": "nightly",
    }]}, 4)
    assert rows[0]["nightly_price"] == 80
    assert not rows[0].get("verified_total_price")
