from reisevergleich.trvl import _is_long_term_rental, compact_hotel_options


def _hotel(name, providers, basis="lead_in", price=100, **extra):
    return {
        "name": name, "price": price, "currency": "EUR", "price_basis": basis, "price_confidence": "unverified",
        "stars": 3, "property_type": "hotel", "sources": [{"provider": provider, "price": price, "price_basis": basis} for provider in providers],
        **extra,
    }


rows = [
    _hotel("citizenM Roma", ["google_hotels"], price=514),
    _hotel("Room Agoda", ["agoda"], basis="room_nightly", price=89),
    _hotel("1-bedroom apartment for rent in Trastevere", ["spotahome"], price=1600, property_type="apartment"),
    _hotel("Studio Uniplaces", ["uniplaces", "flatio"], price=700, property_type="apartment"),
    _hotel("Zimmer Monatsmiete", ["housinganywhere"], basis="monthly", price=900),
    _hotel("Mischtreffer", ["spotahome", "google_hotels"], price=120),
    _hotel("Ohne Quellen", [], price=95),
]

assert _is_long_term_rental(rows[2]) and _is_long_term_rental(rows[3]) and _is_long_term_rental(rows[4])
assert not _is_long_term_rental(rows[0]) and not _is_long_term_rental(rows[1])
assert not _is_long_term_rental(rows[5]), "ein Treffer, den auch ein normaler Anbieter führt, bleibt"
assert not _is_long_term_rental(rows[6])

names = [item["name"] for item in compact_hotel_options({"hotels": rows}, 20)]
assert names == ["Room Agoda", "Ohne Quellen", "Mischtreffer", "citizenM Roma"], names
assert compact_hotel_options({"hotels": rows[2:5]}, 20) == []
print("Langzeitmieten werden aus Hotelergebnissen entfernt: OK")
