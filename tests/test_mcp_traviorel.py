import asyncio
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ["DELAY_INDEX_DB"] = str(_tmp / "delay-index.sqlite3")
os.environ["DELAY_INDEX_SOURCE"] = str(_tmp / "data-{month}.parquet")
os.environ["DELAY_INDEX_MONTHS"] = "2"
os.environ["DB_CFFI_TOKEN"] = "test"

import duckdb  # noqa: E402

from reisevergleich import delay_index, mcp_server  # noqa: E402

# ---- Verspätungsindex aus einer kleinen, echt gebauten Parquet-Datei ----------------------------------
assert delay_index.wanted_months(__import__("datetime").date(2026, 9, 20), 3) == ["2026-08", "2026-07", "2026-06"]
assert delay_index.wanted_months(__import__("datetime").date(2026, 1, 5), 2) == ["2025-12", "2025-11"]

rows = []
for index in range(10):  # ICE 606 in Hamburg: 10 Ankünfte mit 0, 0, 3, 3, 6, 8, 12, 12, 40 Minuten und einem Ausfall
    delay = [0, 0, 3, 3, 6, 8, 12, 12, 40, 0][index]
    cancelled = index == 9
    rows.append(("ICE", "606", "08002549", f"2026-08-{index + 1:02d} 09:24:00", delay, cancelled))
rows.append(("ICE", "606", "08002549", "2026-08-20 09:24:00", 0, False))
rows.append(("Bus", "1", "08002549", "2026-08-20 09:24:00", 5, False))
values = ", ".join(f"('{t}', '{n}', '{e}', TIMESTAMP '{ts}', TIMESTAMP '{ts}' + INTERVAL {d} MINUTE, {str(c).lower()})" for t, n, e, ts, d, c in rows)
duckdb.connect().execute(
    f"COPY (SELECT * FROM (VALUES {values}) AS v(train_type, train_number, eva, arrival_planned_time, arrival_change_time, arrival_is_canceled)) "
    f"TO '{_tmp}/data-2026-08.parquet' (FORMAT parquet)"
)
assert delay_index.build_month("2026-08") == 1, "nur Zugarten der Bahn, eine Gruppe je Zug und Halt"
stats = asyncio.run(delay_index.arrival_stats("ice", "606", "8002549"))
assert stats["events"] == 11 and stats["ran"] == 10 and stats["cancelled_percent"] == 9.1
assert stats["on_time_5_percent"] == 50 and stats["on_time_10_percent"] == 70 and stats["within_30_percent"] == 90
assert abs(stats["average_delay_minutes"] - 8.4) < 0.11
assert asyncio.run(delay_index.arrival_stats("ICE", "606", "08002549")) == stats, "führende Nullen der EVA-Nummer sind egal"
assert asyncio.run(delay_index.arrival_stats("ICE", "999", "8002549")) is None
assert delay_index.built_months() == ["2026-08"]

# ---- MCP: Merkliste, Zeilen, Pünktlichkeit mit Umstieg ------------------------------------------------
mcp_server._refs.clear()
leg1 = {"mode": "train", "train_type": "ICE", "train_number": "606", "line_name": "ICE 606", "origin": "Leipzig Hbf", "destination": "Hamburg Hbf",
        "destination_id": "8002549", "departure": "2026-10-06T06:12+02:00", "arrival": "2026-10-06T09:24+02:00"}
leg2 = {"mode": "train", "train_type": "IC", "train_number": "77", "line_name": "IC 77", "origin": "Hamburg Hbf", "destination": "Kiel Hbf",
        "destination_id": "8000199", "departure": "2026-10-06T09:44+02:00", "arrival": "2026-10-06T10:50+02:00"}
connection = {"provider": "Deutsche Bahn", "type": "train", "legs": [leg1, leg2], "departure": leg1["departure"], "arrival": leg2["arrival"],
              "duration_minutes": 278, "transfers": 1, "price": 39.9, "currency": "EUR"}
ref = mcp_server._remember([connection])[0]
assert mcp_server._connection(ref) is connection
line = mcp_server._connection_line(ref, connection)
assert "ICE 606, IC 77" in line and "06:12 → 10:50" in line and "39,90 €" in line and "1 Umstiege" in line

result = asyncio.run(mcp_server.delay_history(ref))
text = result.content[0].text
assert "ICE 606 → Hamburg Hbf: 50 % höchstens 5 min, 70 % höchstens 10 min, 90 % höchstens 30 min" in text, text
assert "Umstieg in Hamburg Hbf (20 min): Anschluss erreichbar bei 70 % (Zug höchstens 10 min verspätet)" in text, text
assert "IC 77 → Kiel Hbf: keine Daten für diesen Halt" in text

flix = {"provider": "FlixBus", "type": "bus", "legs": [], "departure": "2026-10-06T16:20+02:00", "arrival": "2026-10-06T22:10+02:00", "duration_minutes": 350, "transfers": 0, "price": 19.49}
flix_ref = mcp_server._remember([flix])[0]
assert "Keine Verspätungsdaten" in asyncio.run(mcp_server.delay_history(flix_ref)).content[0].text or "keine Verspätungsdaten" in asyncio.run(mcp_server.delay_history(flix_ref)).content[0].text

try:
    mcp_server._connection("gibt-es-nicht")
except ValueError as exc:
    assert "Suche noch einmal" in str(exc)
else:
    raise AssertionError("unbekannte ref muss abgelehnt werden")

# ---- Index in der Weboberfläche: gleiche Form wie die alte Abfrage, Anschluss-Chance aus der Verteilung ------------
from reisevergleich import history  # noqa: E402

idx = history.index_statistics(stats)
assert idx["status"] == "ok" and idx["quality"] == "limited" and idx["fallback_level"] == "index" and idx["explicit_cancellations"] == 1
assert abs(idx["direct_reliability_rate"] - 7 / 11) < 0.001, idx["direct_reliability_rate"]
assert idx["on_time_5_rate"] == 0.5 and idx["on_time_10_rate"] == 0.7 and "_cdf" in idx
incoming = {"reliability": idx, "arrival": "2026-10-06T09:24+02:00", "destination": {"name": "Hamburg Hbf"}}
connection = history._connection(incoming, {"departure": "2026-10-06T09:44+02:00"})  # 20 Minuten Umstieg
assert connection["status"] == "ok" and connection["percent"] == 82 and connection["method"] == "index_arrival_cdf", connection
tight = history._connection(incoming, {"departure": "2026-10-06T09:29+02:00"})  # 5 Minuten: nur 5 von 11 Halten pünktlich genug
assert tight["percent"] == 45, tight
assert delay_index.share_within([(0, .4), (5, .7), (10, .9)], 7.5) == 0.8 and delay_index.share_within([(0, .4)], -1) == 0.0

async def enriched():
    route = {"legs": [dict(leg1, origin_id="8010205", destination_id="8002549")]}  # nur ein Zug im Index: kein Netzzugriff im Test
    return (await history.enrich_routes_history([route]))[0]

route = asyncio.run(enriched())
assert route["legs"][0]["reliability"]["fallback_level"] == "index", route["legs"][0]["reliability"]

# ---- aktuelle Verspätungen -------------------------------------------------------------------------------------
late = {"provider": "Deutsche Bahn", "type": "train", "origin": "Leipzig Hbf", "destination": "Berlin Hbf", "departure": "2026-09-20T18:51+02:00", "arrival": "2026-09-20T20:05+02:00",
        "duration_minutes": 74, "transfers": 0, "price": 70.1, "legs": [{"mode": "train", "line_name": "ICE 90", "train_number": "90", "train_type": "ICE", "origin": "Leipzig Hbf", "destination": "Berlin Hbf",
        "departure": "2026-09-20T18:51+02:00", "arrival": "2026-09-20T20:05+02:00", "departure_delay_minutes": 35, "arrival_delay_minutes": 34, "platform": "14", "planned_platform": "12"}]}
assert mcp_server._live_summary(late) == " – aktuell +34 min am Ziel"
assert mcp_server._live_summary({"legs": [{"cancelled": True}]}) == " – ein Zug fällt aus" and mcp_server._live_summary({"legs": [{"arrival_delay_minutes": 0}]}) == ""
assert mcp_server._live_summary({"legs": [{"arrival_delay_minutes": None}]}) == "" and mcp_server._live_summary({}) == ""

seen = []


async def fake_search(request):
    seen.append((request.refresh_cache, request.departure_date, request.departure_after))
    return {"response_context": {"outbound": {"connections": [late, {**late, "departure": "2026-09-20T19:16+02:00", "legs": [{**late["legs"][0], "train_number": "596"}]}]}}}

original_search = mcp_server.search
mcp_server.search = fake_search
live_ref = mcp_server._remember([late])[0]
text = asyncio.run(mcp_server.live_delays(live_ref)).content[0].text
assert seen == [(True, "2026-09-20", "18:31")], seen
assert "ICE 90: Abfahrt 18:51 +35 min (erwartet 19:26), Ankunft 20:05 +34 min (erwartet 20:39), Gleis 14 (geplant 12)" in text, text
gone = mcp_server._remember([{**late, "departure": "2026-09-20T17:00+02:00"}])[0]
assert "nicht mehr bei der Bahn" in asyncio.run(mcp_server.live_delays(gone)).content[0].text
mcp_server.search = original_search
print("Verspätungsindex und MCP-Hilfen: OK")
