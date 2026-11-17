# Traviorel 0.3.7

## Deutsch

Scoped-Ultrareview der Kernvergleichs-, Darstellungs- und MCP-Logik (`tool/reisevergleich/{compare,presentation,mcp_server,planner}.py`) fand 10 vorbestehende Fehler:

- FlixBus/-Train erschienen trotz `include_bus=False`/falschem Zeitfenster, weil `flix_options` aus der ungefilterten Rohliste statt der bereits gefilterten Liste gebaut wurde.
- „Günstigster Live-Preis" war die nach Nutzerpräferenz (z. B. „schnellste") best-rangierte Verbindung, nicht die mit dem tatsächlich niedrigsten Preis.
- Ein Flug ohne exakt passenden Flughafencode (z. B. Stadt-/Metro-Code statt konkretem Flughafen beim Anbieter) verschwand aus der öffentlichen Antwort, während die Gesamtsumme seinen Preis weiter einrechnete.
- `missing_fields` ging bei Rundreise- und Reiseplan-Antworten verloren; der Status sagte „Angaben fehlen", verschwieg aber welche.
- Flug- und Hotelpreise in Fremdwährung (z. B. USD) wurden mit „€" formatiert, ohne die tatsächliche Währung zu prüfen.
- Ein reiner Nachtpreis wurde in `plan_flight_trip` als Gesamtpreis für den ganzen Aufenthalt bezeichnet.
- Ein Fehler bei einer optionalen Zusatzbuchung (Hotel, Transfer, Feeder, Flix-Anreicherung) konnte die ganze Reiseplanung oder Bodenvergleichs-Anfrage abbrechen, statt nur diese eine Komponente als fehlend zu markieren.
- Fehlende Echtzeitdaten an einem Fahrtende (nur Abfahrt oder nur Ankunft gemeldet) wurden in `live_delays` als „pünktlich" ausgegeben.
- Das Deutschlandticket ignorierte eine über `find_station` ausdrücklich gewählte Haltestelle und suchte stattdessen per Freitext-Namen, der an einer anderen, ähnlich benannten Station landen kann.
- `search_ground`s `prefer`/`limit` sortierten und kürzten nur das bereits zurückgekommene Ergebnis; die eigentliche Auswahl im Backend lief weiter mit Standardwerten, wodurch günstigere oder umstiegsärmere Verbindungen vorher schon aussortiert wurden.

## English

A scoped ultrareview of the core comparison/presentation/MCP logic (`tool/reisevergleich/{compare,presentation,mcp_server,planner}.py`) found 10 pre-existing bugs:

- FlixBus/-Train trips appeared despite `include_bus=False` or an unmatched departure window, because `flix_options` was built from the raw, unfiltered route list instead of the already-filtered one.
- "Cheapest live price" was the top of the preference-ranked list (e.g. "fastest"), not the route with the actual lowest price.
- A flight with no exactly matching airport code (e.g. a metro/city code vs. the provider's concrete airport) vanished from the public response while the total still counted its price.
- `missing_fields` was lost for ground-trip and trip-plan responses; the status said fields were missing without saying which.
- Flight and hotel prices in a foreign currency (e.g. USD) were formatted with "€" regardless of the actual currency.
- A nightly-only hotel price was labelled as the total price for the whole stay in `plan_flight_trip`.
- A failure in an optional add-on lookup (hotel, transfer, feeder, Flix enrichment) could fail the entire trip plan or ground comparison instead of marking just that one component as missing.
- Missing real-time data for one end of a leg (only departure or only arrival reported) was shown as "on time" in `live_delays`.
- The Deutschlandticket search ignored a station explicitly selected via `find_station`, searching by free-text name instead, which can resolve to a different, similarly named stop.
- `search_ground`'s `prefer`/`limit` only re-sorted and truncated whatever came back; the actual backend selection kept running on its defaults, so cheaper or fewer-transfer connections were already cut before this tool ever saw them.

## Validation

Added one regression test per finding in `tests/test_ultrareview_core_20261117.py` (12 tests). One (the Deutschlandticket station-selection fix) was spot-verified to actually fail when its fix is reverted, to confirm the coverage is meaningful. `bash scripts/check.sh && python -m pytest -q` passed (236 tests).
