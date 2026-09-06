# Traviorel 0.2.6 – Simplified Search Experience

Traviorel 0.2.6 vereinfacht die Bodenreisesuche und hält technische Providerentscheidungen aus dem Suchformular heraus.

## Suchformular

- Zug und Bus sind als direkt sichtbare Verkehrsmittel unabhängig auswählbar.
- Traviorel fragt die dazu passenden Bahn- und Busanbieter automatisch ab.
- Split-Ticket bleibt als kleiner Haken direkt unter der Verkehrsmittelauswahl sichtbar.
- Das Deutschlandticket bleibt eine sichtbare Kernentscheidung.
- Seltene technische Einstellungen stehen nach dem Suchbutton in einem eigenen Bereich; „Weitere Optionen“ entfällt.

## Provider und Flix

- Separate FlixBus-, FlixTrain-, DB- oder Transitous-Schalter werden nicht mehr angezeigt.
- Stations-IDs, GTFS-Zuordnung und Provider-Mapping bleiben intern erhalten.
- Eine erfolgreiche Flix-Prüfung ohne Treffer meldet „Keine Verbindung verfügbar“.
- Ein technischer Flix-Fehler meldet „Flix konnte nicht geprüft werden“.

## Qualität

- Zug-only, Bus-only und kombinierte Suchen wurden für Leipzig–Dortmund, Leipzig–Kamen und Leipzig–Frankfurt geprüft.
- Desktop-Tests: 1440×1000 und 1920×1080.
- Mobile Tests: 390×844 und 412×915.
- Coverage-, Mobilfunk-, GTFS- und Preisberechnungen bleiben unverändert.
