# Traviorel 0.2.2

Traviorel 0.2.2 stabilisiert die providerübergreifende Stationszuordnung und aktiviert die bereits vorhandene OpenCellID-Offline-Pipeline für betreiberbezogene Mobilfunkwerte.

## Änderungen

- Stationskomplexe bewahren mehrere native IDs je Provider; Flix berücksichtigt Parent-, Child- und Geschwisterhaltestellen gemeinsam.
- OpenCellID-Auswertungen ordnen Betreiber ausschließlich über bekannte MCC/MNC-Paare zu. Unbekannte Kombinationen werden nicht geschätzt.
- Lokale OpenCellID-CSV-Bestände liefern Telekom-, Vodafone-, Telefónica/O2- und – sofern räumlich belegt – 1&1-Werte entlang der vollständigen Route.
- Interne Diagnose nennt geprüfte Streckenpunkte, gefundene Zellen, erkannte Betreiber, unbekannte MCC/MNC-Paare, API-Fehler und Datenqualität.
- Ohne ausreichende OpenCellID-Evidenz bleibt der sichere Hinweis „Betreiberdaten nicht verfügbar“ erhalten.

## Betrieb

Der Offline-Datenbestand ist aufgrund Größe und CC-BY-SA-4.0-Lizenz nicht Bestandteil der Images. Er wird über `OPENCELLID_CSV_PATH` aus dem persistenten Traviorel-State eingebunden. Ein API-Key ist nicht erforderlich und wird für den produktiven Offline-Betrieb nicht gesetzt.

## Validierung

- Vollständige Logik-, API- und Container-Tests
- Browser-Tests einschließlich Desktop-, Kalender- und Mobile-Viewports
- Live-Strecke Leipzig Hbf → Kassel-Wilhelmshöhe mit vollständiger Streckenabtastung und betreiberbezogenen OpenCellID-Werten
