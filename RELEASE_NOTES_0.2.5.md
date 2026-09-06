# Traviorel 0.2.5 – Smart Location Resolution Fix

Traviorel 0.2.5 entfernt feste Stationszuweisungen aus der Ortsauflösung und macht wichtige Reiseentscheidungen direkt sichtbar.

## Orts- und Stationsauflösung

- Hardcoded Stadt→Hauptbahnhof-Zuweisungen sind entfernt.
- Eine Stadteingabe wie München, Leipzig, Frankfurt, Köln, Wien/Vienna oder Görlitz öffnet immer die Stationsauswahl.
- Schreibweisen und Übersetzungen werden weiterhin normalisiert, aber niemals als feste Stationsentscheidung verwendet.
- Hauptbahnhöfe und bedeutende Fernverkehrshalte stehen weiter oben, ohne automatisch ausgewählt zu werden.
- Flughafenhalte werden bei normalen Städteingaben ausgeblendet und nur bei passendem Flughafenbegriff berücksichtigt.
- Provider-native IDs für DB, Transitous und Flix bleiben nach der ausdrücklichen Nutzerauswahl erhalten.
- Geklammerte Providerzusätze wie „(bus station)“ beeinflussen die Flix-Stadtsuche nicht, während die konkrete Stations-ID erhalten bleibt.

## Reiseformular

- Split-Ticket-Suche ist als direkt sichtbarer Checkbox-Haken ein- und ausschaltbar.
- „Nur Hinfahrt“ deaktiviert Rückreisedatum und Kalender sichtbar; ein erneuter Klick stellt Hin- und Rückfahrt mit gültigem Datum wieder her.

## Validierung

- Vollständige Logik-, API- und Container-Suite
- DB-, Transitous-, Flix-, GTFS- und Stationsmapping-Regressionen
- Coverage- und Kalenderregressionen
- Desktop 1440×1000 und 1920×1080
- Mobile 390×844 und 412×915
- Reale Kandidatenlisten für München, Leipzig, Frankfurt, Köln, Wien/Vienna und Görlitz
