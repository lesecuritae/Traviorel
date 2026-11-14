# Traviorel 0.3.4

## Deutsch

- Geprüfter trvl-Snapshot `b20e2265a63a4d229abff7b20f5649f2e9f948dc`, gekennzeichnet als `1.27.0+git.b20e226`. Kein neuer stabiler trvl-Release wird behauptet.
- Air France/KLM übernimmt den Gesamtpreis für Hin-/Rückflüge statt nur eines Streckenanteils. Wizz Air verwendet die aktualisierte API-Vorgabe 29.24.0.
- TLS-/HTTP- und Browser-Abhängigkeiten aktualisiert; Go 1.27.3-Builder und Upstream-Commit sind unveränderlich gepinnt. Ausgewählte Flug-, Hotel-, Provider- und Bodenverkehrstests laufen beim Build ohne Netzwerk und ohne Betreiberzugangsdaten.
- Seit v1.27.0 gab es keine direkte neue Hotel-Such-/Preisfunktion. Die gemeinsamen Transportänderungen werden auch gegen die Hoteltests geprüft. Zusätzliche Traviorel-Fixtures sichern Gesamtflugpreis und Hotel-Nacht-/Gesamtpreis ab.
- Lite bleibt ohne trvl. Die bestehenden direkten DB-, Transitous- und Flix-Wege bleiben erhalten.

- MCP-Abfahrtstafeln wählen Haltestellen passend zum Verkehrsmittel serverseitig, statt vom Modell gelieferte IDs zu übernehmen. Quellenbasierte Namensvarianten und Bus-/Bahn-Zuordnung werden für mehrere Städte geprüft; echte Mehrdeutigkeiten bleiben sichtbar.
- Eigenes MCP-Werkzeug `search_hotels` verwendet die vorhandene Hotelsuche ohne Flugplanung und trennt belegte Gesamtpreise von Nachtpreisen.

## English

- Reviewed trvl snapshot `b20e2265a63a4d229abff7b20f5649f2e9f948dc`, identified as `1.27.0+git.b20e226`, not a new stable upstream release.
- Air France/KLM retains the complete round-trip ticket price instead of a single bound share. Wizz Air uses updated API default 29.24.0.
- Updated TLS/HTTP and browser dependencies, immutable Go 1.27.3 builder and source pins. Selected upstream flight, hotel, provider and ground tests run without network or operator credentials during the build.
- No new hotel search or pricing feature was present in the upstream changes since v1.27.0. Shared transport changes are covered by hotel tests. Additional Traviorel fixtures protect ticket totals and hotel nightly/stay-price separation.
- Lite still excludes trvl. Existing direct DB, Transitous and Flix paths are retained.

- MCP departure boards resolve mode-specific stops server-side rather than accepting model-selected IDs. Source-driven aliases and bus/rail selection are tested across multiple cities; genuine ambiguity remains explicit.
- Dedicated `search_hotels` MCP tool reuses accommodation search without flight planning and separates verified stay totals from nightly prices.

## Validation

Python 3.13: check.sh passed and 186 pytest tests passed, with 0 failures / 0 skips. All five selected upstream Go packages and the full/Lite/DB-API image builds passed. Offline fixtures are not proof that every live provider is available.

Isolated live MCP and Lite checks passed. Both the normal hotel path and a fresh forced-trvl control returned 3 hotels with verified stay totals. One earlier forced-trvl attempt timed out; the old/new controls completed in 26.8s/29.0s. Intermittent Google throttling and unavailable browser-dependent Booking coverage remain explicit limits. No claim of complete live-provider availability is made.

Follow-up real Hermes tests exposed a stale gateway tool catalog, model-selected rail IDs in explicit bus requests, and a 60-second client timeout on an 81-second full trip plan. The MCP fixes above address the source defects; refreshed gateway discovery and end-to-end model acceptance are recorded separately and must not be inferred from offline tests.
