import asyncio
import os
import tempfile
from pathlib import Path

os.environ["PRICE_HISTORY_DB"] = str(Path(tempfile.mkdtemp()) / "price-history.sqlite3")
os.environ["DB_CFFI_TOKEN"] = "test"

from reisevergleich import mcp_server, price_history as ph  # noqa: E402


def result(price_db, price_flix, travel_date="2026-10-06", origin="Leipzig Hbf", destination="Hamburg Hbf"):
    return {"response_context": {
        "route": {"origin": origin, "destination": destination, "outbound_date": travel_date},
        "outbound": {"connections": [
            {"provider": "Deutsche Bahn", "price": price_db}, {"provider": "Deutsche Bahn", "price": price_db + 40},
            {"provider": "FlixBus", "price": price_flix}, {"provider": "Transitous", "price": None},
        ]},
    }}


# Verlauf einer Fahrt: Preis steigt, je näher der Reisetag rückt.
for day, price in (("2026-09-06", 29.99), ("2026-09-20", 44.99), ("2026-10-01", 74.99)):
    ph._today = lambda day=day: day
    ph.record_search(result(price, 19.49))
ph._today = lambda: "2026-10-01"
ph.record_search(result(69.99, 19.49))  # derselbe Tag noch einmal: der niedrigere Preis gilt

series = ph.route_series("leipzig hbf", "HAMBURG HBF", "2026-10-06")
assert series["Deutsche Bahn"] == [("2026-09-06", 29.99), ("2026-09-20", 44.99), ("2026-10-01", 69.99)], series
assert [p for _, p in series["FlixBus"]] == [19.49, 19.49, 19.49] and "Transitous" not in series

overview = ph.route_overview("Leipzig Hbf", "Hamburg Hbf")
db = overview["Deutsche Bahn"]
assert db["lowest"] == 29.99 and db["highest"] == 69.99 and db["typical"] == 44.99 and db["travel_dates"] == 1
assert db["median_by_days_before"] == {"4–7 Tage": 69.99, "15–30 Tage": 37.49}, db["median_by_days_before"]  # 5 Tage vorher; 16 und 30 Tage vorher

# Flug
ph._today = lambda: "2026-09-20"
ph.record_search({"response_context": {"route": {"origin": "Leipzig", "destination": "Rom", "outbound_date": "2026-10-20", "return_date": "2026-10-24", "stay_nights": 4,
                                                 "origin_airport": "LEJ", "destination_airport": "FCO"}, "flight": {"provider": "skiplagged", "price": {"value": 391.8}}}})
assert ph.route_series("LEJ", "FCO", "2026-10-20", "flight") == {"skiplagged (4 Nächte)": [("2026-09-20", 391.8)]}

# Ergebnisse ohne Preise oder ohne Route schaden nicht.
ph.record_search({}); ph.record_search({"response_context": {"route": {"outbound_date": "2026-10-06"}}})

# MCP-Werkzeuge
text = asyncio.run(mcp_server.price_history("Leipzig Hbf", "Hamburg Hbf", "2026-10-06")).content[0].text
assert "Deutsche Bahn: zuletzt 69,99 € (am 2026-10-01, 5 Tage vor Abreise), niedrigster 29,99 €, höchster 69,99 €, 3 Beobachtung(en); seit 2026-09-06 +40,00 €" in text, text
overview_text = asyncio.run(mcp_server.price_history("Leipzig Hbf", "Hamburg Hbf")).content[0].text
assert "typisch 44,99 €" in overview_text and "Typischer Preis nach Vorlauf" in overview_text
assert "noch keine Preise" in asyncio.run(mcp_server.price_history("Nirgends", "Nirgendwo", "2026-10-06")).content[0].text

# Beobachtungsliste
first = ph.add_watch("Leipzig Hbf", "Hamburg Hbf")
assert ph.add_watch("leipzig hbf", "hamburg hbf")["id"] == first["id"], "keine Doppelten"
for index in range(4):
    ph.add_watch(f"Ort {index}", "Ziel")
try:
    ph.add_watch("Einer zu viel", "Ziel")
except ValueError as exc:
    assert "höchstens 5" in str(exc)
else:
    raise AssertionError("mehr als fünf Strecken dürfen nicht möglich sein")
assert len(ph.list_watches()) == 5 and ph.remove_watch(first["id"]) and len(ph.list_watches()) == 4
print("Preisverlauf: mitschreiben, auswerten, Beobachtungsliste: OK")
