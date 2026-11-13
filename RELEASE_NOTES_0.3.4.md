# Traviorel 0.3.4

## Deutsch

- Geprüfter trvl-Snapshot `b20e2265a63a4d229abff7b20f5649f2e9f948dc`, gekennzeichnet als `1.27.0+git.b20e226`. Kein neuer stabiler trvl-Release wird behauptet.
- Air France/KLM übernimmt den Gesamtpreis für Hin-/Rückflüge statt nur eines Streckenanteils. Wizz Air verwendet die aktualisierte API-Vorgabe29.24.0.
- TLS-/HTTP- und Browser-Abhängigkeiten aktualisiert; Go1.27.3-Builder und Upstream-Commit sind unveränderlich gepinnt. Ausgewählte Flug-, Hotel-, Provider- und Bodenverkehrstests laufen beim Build ohne Netzwerk und ohne Betreiberzugangsdaten.
- Seit v1.27.0 gab es keine direkte neue Hotel-Such-/Preisfunktion. Die gemeinsamen Transportänderungen werden auch gegen die Hoteltests geprüft. Zusätzliche Traviorel-Fixtures sichern Gesamtflugpreis und Hotel-Nacht-/Gesamtpreis ab.
- Lite bleibt ohne trvl. Die bestehenden direkten DB-, Transitous- und Flix-Wege bleiben erhalten.

## English

- Reviewed trvl snapshot `b20e2265a63a4d229abff7b20f5649f2e9f948dc`, identified as `1.27.0+git.b20e226`, not a new stable upstream release.
- Air France/KLM retains the complete round-trip ticket price instead of a single bound share. Wizz Air uses updated API default29.24.0.
- Updated TLS/HTTP and browser dependencies, immutable Go1.27.3 builder and source pins. Selected upstream flight, hotel, provider and ground tests run without network or operator credentials during the build.
- No new hotel search or pricing feature was present in the upstream changes since v1.27.0. Shared transport changes are covered by hotel tests. Additional Traviorel fixtures protect ticket totals and hotel nightly/stay-price separation.
- Lite still excludes trvl. Existing direct DB, Transitous and Flix paths are retained.

## Validation

Candidate build and regression results are recorded in the dependency audit before publication. Offline fixtures are not proof that every live provider is available.
