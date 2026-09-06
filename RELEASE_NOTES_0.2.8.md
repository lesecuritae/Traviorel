# Traviorel 0.2.8 – FlixTrain Partial Route Fix

Traviorel 0.2.8 behebt die Ursache fehlerhafter Teilstrecken in mehrteiligen FlixTrain-Liveverbindungen.

## Routing und Parsing

- Zwischenhalte aus der Flix-Live-API werden anhand ihrer nativen UUID im aktuellen Flix-GTFS-Katalog aufgelöst.
- Stationsname, Stations-ID und Koordinaten bleiben gemeinsam erhalten.
- Der klassifizierte Verkehrsträger wird auf jedes Leg übertragen, sodass FlixTrain nicht als FlixBus dargestellt wird.
- Einteilige und mehrteilige Flix-Verbindungen verwenden dieselbe Struktur; DB- und Transitous-Routen bleiben unverändert.

## Regression

- Reproduziert mit Leipzig Hbf → Dortmund Hbf am 30. August 2026.
- Die Live-Verbindung 12:04–20:36 enthält drei korrekt benannte FlixTrain-Legs über Halle (Saale) Hbf und Berlin Hbf.
- Zeitzonen und lokale ISO-Zeitstempel bleiben unverändert.
- Vollständige Testsuite und Docker-Build sind Bestandteil der Release-Prüfung.
