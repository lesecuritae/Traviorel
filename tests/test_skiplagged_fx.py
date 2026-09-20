from reisevergleich import fx, skiplagged
from reisevergleich.trvl import compact_flight_options, compact_hotel_options

FLIGHTS = """# Flight search results (BER → FCO)

| Price | Duration | Stops | Type | Airlines | Segments | Booking |
| --- | --- | --- | --- | --- | --- | --- |
| $86 | 27h 20m | 1 stop | — | Norwegian Air Sweden AOC | Outbound:<br/>BER → CPH (2026-10-20 17:25:00+02:00 → 2026-10-20 18:25:00+02:00)<br/>Layover in CPH – 23h 45m<br/>CPH → FCO (2026-10-21 18:10:00+02:00 → 2026-10-21 20:45:00+02:00) | [Book](https://skiplagged.com/flights/BER/FCO/2026-10-20#trip=D1) |
| $124 | 2h 10m | Nonstop | — | Ryanair | Outbound:<br/>BER → FCO (2026-10-20 22:05:00+02:00 → 2026-10-21 00:15:00+02:00) | [Book](https://skiplagged.com/flights/BER/FCO/2026-10-20#trip=FR134) |
| Preis kaputt | x | y |
"""

HOTELS = """# Hotels in Rome

| Hotel | Rating | Price/night | Total | Amenities | Booking |
| --- | --- | --- | --- | --- | --- |
| **Ibis Roma Fiera**<br/>63 Via Arturo Mercanti | 3★ · 7.0/10 | $91 | $341 | Free internet | [View deal](https://skiplagged.com/hotel/52555/ibis) |
| **Ohne Bewertung** | 4★ | $100 | $300 | — | [View deal](https://skiplagged.com/hotel/2/x) |
| **Gratis** | 3★ · 8.0/10 | $0 | $0 | — | [View deal](https://skiplagged.com/hotel/3/x) |
"""

ECB = "<Cube><Cube currency='USD' rate='1.1500'/><Cube currency='GBP' rate='0.8600'/></Cube>"

assert fx.parse_rates(ECB) == {"USD": 1.15, "GBP": 0.86}
fx._rates, fx._loaded_ok = {"USD": 1.15}, True
assert fx.to_eur(115, "USD") == 100.0 and fx.to_eur(50, "EUR") == 50.0 and fx.to_eur(50, "JPY") is None

flights = skiplagged.parse_flights(FLIGHTS)
assert [(f["price"], f["stops"], len(f["legs"])) for f in flights] == [(86.0, 1, 2), (124.0, 0, 1)], flights
assert flights[0]["legs"][1]["departure_airport"]["code"] == "CPH" and flights[0]["duration"] == 27 * 60 + 20
assert flights[1]["legs"][0]["arrival_time"] == "2026-10-21T00:15:00+02:00" and "#trip=FR134" in flights[1]["booking_url"]

eur = skiplagged.flights_to_eur(flights)
assert eur[1]["currency"] == "EUR" and eur[1]["price"] == 107.83 and eur[1]["original_price"] == 124.0
fx._rates.clear()
assert skiplagged.flights_to_eur(flights)[0]["currency"] == "USD", "ohne Kurs bleibt der Preis unverändert"
fx._rates.update({"USD": 1.15})

# Auch Dollar-Preise aus trvl selbst werden vor dem Vergleich in Euro umgerechnet.
options = compact_flight_options({"flights": [{"price": 115, "currency": "USD", "legs": [
    {"departure_airport": {"code": "BER"}, "arrival_airport": {"code": "FCO"}, "departure_time": "2026-10-20T10:00:00+02:00", "arrival_time": "2026-10-20T12:00:00+02:00"}]}]}, "BER", "FCO", 5)
assert options[0]["price"] == 100.0 and options[0]["currency"] == "EUR" and options[0]["original_currency"] == "USD"

hotels = skiplagged.hotels_to_eur(skiplagged.parse_hotels(HOTELS, 3))
assert [h["name"] for h in hotels] == ["Ibis Roma Fiera", "Ohne Bewertung"], hotels
assert hotels[0]["stars"] == 3 and hotels[0]["rating"] == 7.0 and hotels[0]["address"] == "63 Via Arturo Mercanti"
compact = compact_hotel_options({"hotels": hotels}, 10)
ibis = next(item for item in compact if item["name"] == "Ibis Roma Fiera")
assert ibis["nightly_price"] == 79.13 and ibis["verified_total_price"] == 296.52 and ibis["currency"] == "EUR"
assert compact[0]["name"] == "Ohne Bewertung", "günstigster Gesamtpreis zuerst"
print("Skiplagged-Flüge/-Hotels und EZB-Umrechnung: OK")
