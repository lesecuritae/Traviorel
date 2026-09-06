# Traviorel 0.2.7 – Route Data Stability Fix

Traviorel 0.2.7 stabilisiert Segmentdarstellung und Mobilfunkanalyse, ohne Provider-, Preis-, Kalender- oder Suchlogik zu verändern.

## Routendarstellung

- Stations-, Zeit-, Linien- und Betreiberobjekte werden vor der UI-Ausgabe normalisiert.
- FlixTrain, FlixBus, DB-/Regionalzugsegmente und Mischverbindungen erhalten verständliche Bezeichnungen.
- `[object Object]`, technische Stations-UUIDs und der unspezifische Fallback „Teilstrecke“ werden nicht mehr angezeigt.
- Ein Segment ohne verwertbare Daten heißt ausschließlich „Unbekannte Teilstrecke“.

## Coverage

- Bestätigte Start- und Zielkoordinaten werden providerneutral an die Verbindung übergeben.
- Vorhandene Geometrie, Stopovers und vollständige Segmente werden unabhängig von sichtbaren Provider- oder Liniennamen ausgewertet.
- Fehlende Daten und technische Analyzerfehler sind getrennte Zustände.
- Ältere Ergebnis-Caches ohne Coverage-Endpunkte werden über eine neue Schema-Generation sicher ignoriert.
- Die bestehenden BNetzA- und OpenCellID-Datenquellen bleiben unverändert.

## Qualität

- FlixTrain-Mischketten, FlixBus, DB und Transitous wurden als getrennte Routenschemata geprüft.
- Reale FlixTrain-Coverage Leipzig–Dortmund: 347,6 km und 91 ausgewertete Streckenpunkte.
- Desktop: 1440×1000 und 1920×1080.
- Mobile: 390×844 und 412×915.
