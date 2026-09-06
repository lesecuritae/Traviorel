# Traviorel 0.2.3

Traviorel 0.2.3 stabilisiert die Reiseoberfläche, die gemeinsame Kalenderbedienung und providerübergreifende Flix-Verbindungen. Coverage-Berechnung und OpenCellID-Datenbasis bleiben unverändert.

## Flix und Routing

- Gültige Live-Flix-Verbindungen mit Umstiegen bleiben sichtbar, auch wenn der GTFS-Feed keine direkte Fahrt auf derselben `trip_id` enthält.
- Bahnhofsbezeichnungen werden für die Flix-Stadtsuche allgemein normalisiert; konkrete Start- und Zielhaltestellen bleiben über bestätigte Flix-Alias-IDs abgesichert.
- Abweichende Bezeichnungen derselben Station erzeugen keine unnötigen Zubringer mehr.
- FlixTrain und FlixBus sind direkt im Hauptformular auswählbar. Die Haltestellenzuordnung erfolgt automatisch.

## Oberfläche

- Hin- und Rückreise verwenden einen gemeinsamen Kalender mit konsistenter Mindestdatums- und Monatslogik.
- Der doppelte Abreisekalender und die defekte getrennte Rückreisebedienung entfallen.
- Die Mobilfunkanzeige ist kompakter: Betreiberwerte stehen direkt nebeneinander, ohne zusätzliche technische Inhalte in der Fahrtkarte.

## Validierung

- Vollständige Logik-, API- und Container-Suite
- Browser- und Mobile-Tests bei 390×844, 412×915, 1440×1000 und 1920×1080
- Leipzig Hbf → Görlitz Hbf: FlixBus-Liveverbindung mit Umstieg, korrektem Preis und Providerstatus
- Zufallsstichproben aus dem aktiven Flix-GTFS für FlixBus und FlixTrain
- Flexible Preissuche, Hin-/Rückkalender und kompakte Coverage-Darstellung
