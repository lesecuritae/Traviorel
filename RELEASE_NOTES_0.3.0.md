# Traviorel 0.3.0 – NINA/BBK-Warnungen entlang der Reise

Traviorel zeigt nach einer erfolgreichen Routensuche aktuelle amtliche Warnungen an, die geografisch zur Reise passen.

## Neu

- Warnungen am Start, Ziel, an wichtigen Zwischenhalten und an Flughäfen einer trvl-Flugroute
- Geografische Zuordnung über die amtlichen NINA-/BBK-Warnflächen
- Kompakte Anzeige mit Warnsymbol, Titel, Beschreibung, Region und Quelle
- Keine Anzeige unspezifischer deutschlandweiter Meldungen ohne konkreten Reisebezug

## Sicher und additiv

- Keine Änderung oder automatische Umplanung von Verbindungen
- Flughafenwarnungen sind reine Ortsinformationen; Traviorel leitet daraus keine Ausfälle einzelner Bahn-, Bus- oder Flugverbindungen ab
- Kein Einfluss auf DB, FlixTrain, GTFS, Transitous, ÖPNV, Ranking oder Preise
- Separater Nachladepfad: Ein NINA-Ausfall beeinträchtigt die Reiseausgabe nicht
- Fünf Minuten Cache und begrenzte Parallelität vermeiden unnötige API-Aufrufe
