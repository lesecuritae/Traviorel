# Changelog
## Unveröffentlicht

- Aktuelle Verspätungen: Die DB-Schnittstelle gibt je Abschnitt jetzt Verspätung bei Abfahrt und Ankunft, Ausfall und Gleis mit aus. Bahnzeilen im MCP zeigen „aktuell +34 min am Ziel“ oder „ein Zug fällt aus“; das neue Werkzeug `live_delays` fragt eine Verbindung frisch ab und nennt je Zug Plan und erwartete Zeit. Echtzeitangaben gibt es nur kurz vor und während der Fahrt.
- Verspätungsindex in der Weboberfläche: Die Zuverlässigkeits- und Anschlussangaben nutzen jetzt den lokalen Index (sofort statt oft im Zeitlimit) mit einer groben Verteilung der Ankunftsverspätung (Schwellen 0 bis 60 Minuten). Züge, die der Index nicht kennt, fragen wie bisher den Datensatz ab. Der Index (`delay-index-v2.sqlite3`) baut sich beim ersten Bedarf neu auf.
- Flüge: Die Reihenfolge berücksichtigt jetzt Zwischenstopps und Reisezeit (Aufschlag 35 € je Zwischenstopp und 12 € je Stunde, einstellbar mit `FLIGHT_STOP_PENALTY_EUR` und `FLIGHT_HOUR_VALUE_EUR`), damit nicht ein 12-Stunden-Flug mit zwei Stopps als Bestes erscheint, weil er 80 € billiger ist. Der angezeigte Preis bleibt der echte.
- Preisverlauf: Traviorel schreibt bei jeder frischen Suche den günstigsten Preis je Anbieter, Strecke, Reisetag und Abrufdatum mit (lokale SQLite-Datei) und wertet ihn mit dem MCP-Werkzeug `price_history` aus (Verlauf einer Fahrt, Übersicht mit typischem Preis je Vorlauf). Bis zu 5 Strecken lassen sich mit `watch_route` täglich beobachten. Der Verlauf beginnt leer.
- MCP-Server unter `/mcp` (nur lesend) mit `search_ground`, `plan_flight_trip`, `price_calendar_search`, `mobile_coverage`, `travel_warnings`, `delay_history` und `find_station`. Eine Suche merkt sich ihre Verbindungen unter einer `ref`, mit der die Zusatzwerkzeuge dieselbe Verbindung abfragen. Abschaltbar mit `TRAVIOREL_MCP=0`.
- Verspätungsindex: Die historische Pünktlichkeit kommt aus einem lokalen Index, der im Hintergrund aus dem offenen Datensatz `piebro/deutsche-bahn-data` aufgebaut wird (etwa 2 Minuten je Monat). Die bisherige Abfrage je Zug lief bei neuen Zügen meist in ein Zeitlimit; der Index antwortet sofort und nennt je Zug und Halt den Anteil mit höchstens 5, 10 und 30 Minuten Verspätung, die mittlere Verspätung und die Ausfälle, dazu die Anschluss-Chance beim Umsteigen.
- Fehler behoben: Die Hotelsuche über Skiplagged lieferte für „Rom“ Hotels bei Detroit (der Dienst gleicht Städtenamen unscharf ab). Jetzt wird der Stadtname ins Englische gebracht („Rome“), und nur Hotels, deren Adresse die gesuchte Stadt nennt, werden übernommen; sonst greift wie bisher trvl.
- trvl ist jetzt optional: Mit `TRVL_ENABLED=0` läuft Traviorel ohne trvl. Bahn (eigene DB-Logik), Flix (Fahrplan und Preise direkt bei Flix), Flüge und Hotels (Skiplagged) und die Flughafen-Transfers (Transitous plus Flix) brauchen es nicht mehr. Es fehlen dann nur die Taxi-Schätzungen, die letzte trvl-Route und Anbieter, die es nur über trvl gibt (etwa Ryanair-Direktabfragen). Der Gesundheitsbericht meldet trvl dann als bewusst abgeschaltet statt als Fehler.
- Neues Image `traviorel-app-lite` (Bau mit `--build-arg WITH_TRVL=0`): enthält trvl nicht und damit auch nicht dessen Noncommercial-Lizenz.
- Flüge und Hotels über den offenen MCP-Dienst von Skiplagged (ohne Schlüssel, ohne trvl): Die Suche fragt zuerst Skiplagged, schlägt das fehl (Ratenbegrenzung, Ausfall), greift wie bisher trvl. Hotels kommen mit Nacht- und Gesamtpreis und sind echte Hotels statt Ferienwohnungen. Mit `FLIGHT_NATIVE=0` beziehungsweise `HOTEL_NATIVE=0` bleibt es beim alten Weg.
- Währung: Skiplagged liefert Dollar. Diese Preise (auch die über trvl) werden mit dem Tageskurs der EZB in Euro umgerechnet und tragen `original_price`/`original_currency`. Zuvor konnte ein Dollarpreis neben Euro-Preisen als „günstigster“ Flug erscheinen (86 $ statt 75,04 €).
- FlixBus/FlixTrain: Die Preise kommen jetzt direkt von der öffentlichen Flix-Suche (ohne Schlüssel, ohne trvl) und bleiben mit trvl als Rückfall abgesichert. Im Vergleich für Berlin–Hamburg lieferte die eigene Abfrage alle 34 Verbindungen von trvl mit identischen Preisen und 9 weitere. Mit `FLIX_NATIVE=0` bleibt es beim alten Weg.
- Hotelsuche: Treffer, die nur von Langzeitmiet-Anbietern stammen (Spotahome, Uniplaces, HousingAnywhere, Flatio, Wunderflats, Landing, Blueground) oder als Monatspreis ausgewiesen sind, werden aus den Ergebnissen entfernt. Ihr Preis war eine Monatsmiete und stand als Übernachtungspreis da (zum Beispiel 1600 € für eine Wohnung in Rom). In einer Stichprobe für Rom fielen 100 von 243 Treffern weg.
- Docker: trvl wird für die Zielarchitektur nativ gebaut statt in der QEMU-Emulation. Der arm64-Build braucht dadurch Sekunden statt einer Viertelstunde.

## 0.3.0 - 2026-08-29

- Aktuelle amtliche NINA-/BBK-Warnungen werden nach einer erfolgreichen Reise separat und ohne Einfluss auf Routing, Ranking oder Preise geladen.
- Traviorel ordnet Warnungen anhand ihrer amtlichen GeoJSON-Flächen den vorhandenen Koordinaten von Start, Ziel und wichtigen Zwischenhalten zu.
- Unspezifische deutschlandweite Warnflächen werden nicht als reisebezogene Meldung angezeigt.
- Die Oberfläche zeigt relevante Warnungen mit Symbol, Titel, Kurzbeschreibung, Region, betroffenen Halten und Quelle; bei keiner Warnung oder einem API-Ausfall bleibt sie unverändert.
- Bei Flugabschnitten werden trvl-IATA-Codes gezielt über den vorhandenen Haltestellenresolver geografisch aufgelöst, damit ortsbezogene Warnungen an deutschen Flughäfen ebenfalls erfasst werden.
- Flughafenwarnungen bleiben reine Ortsinformationen und treffen keine Aussage über die betriebliche Betroffenheit einzelner Bahn-, Bus- oder Flugverbindungen.
- Kartenlisten, Warngeometrien und Details nutzen den bestehenden SQLite-Komponentencache mit fünf Minuten TTL und begrenzter Parallelität.

## 0.2.8 - 2026-08-29

- Mehrteilige FlixTrain-Liveverbindungen lösen technische Stop-UUIDs deterministisch über den vorhandenen Flix-GTFS-Katalog auf.
- Betreiber und Modus werden für jedes Live-Leg aus der klassifizierten Flix-Verbindung übernommen; FlixTrain-Abschnitte erscheinen nicht mehr fälschlich als FlixBus.
- Direkte Verbindungen mit nur einem Leg sowie FlixTrain-Umstiegsverbindungen behalten eine vollständige, UI-kompatible Teilstreckenstruktur.
- Eine reale Regression Leipzig Hbf–Dortmund Hbf deckt Halle (Saale) Hbf und Berlin Hbf als Zwischenhalte, Stations-IDs, Zeiten und Betreiber ab.
- Die Routing-Cachegeneration wurde erhöht, damit bereits gespeicherte Live-Routen mit UUID-Haltepunkten nicht weiter ausgeliefert werden.

## 0.2.7 - 2026-08-24

- FlixTrain-/FlixBus-Stations-, Zeit- und Linienobjekte werden vor der Darstellung normalisiert; `[object Object]`, technische UUIDs und leere Teilstrecken erscheinen nicht mehr.
- Segmente zeigen Linie und Betreiber, verständliche Start-/Zielnamen oder einen Verkehrsmittelnamen. Nur vollständig informationslose Legs verwenden „Unbekannte Teilstrecke“.
- Coverage erhält bestätigte Start-/Zielkoordinaten unabhängig vom Provider und bevorzugt vollständige Segment-/Stopover-Geometrien gegenüber unvollständigen Anzeige-Legs.
- DB-, Transitous-, FlixTrain-, FlixBus- und Mischketten verwenden denselben providerneutralen Coverage-Datenfluss.
- Fehlende Abdeckungsdaten melden „Mobilfunkdaten für diese Strecke nicht verfügbar“; technische Analyzerfehler melden getrennt „Mobilfunkanalyse fehlgeschlagen“.
- Die Ergebnis-Cachegeneration wurde erhöht, damit ältere Routen ohne providerneutrale Coverage-Endpunkte nicht in 0.2.7 weiterverwendet werden.

## 0.2.6 - 2026-08-24

- Das Suchformular bietet direkt sichtbare, providerneutrale Auswahlen für Zug und Bus. Traviorel ordnet DB, Transitous, FlixTrain und FlixBus weiterhin intern zu.
- Zug-only, Bus-only und die kombinierte Suche filtern die tatsächlich sichtbaren Verbindungen; mindestens ein Verkehrsmittel bleibt ausgewählt.
- Die Split-Ticket-Prüfung bleibt als kleiner Haken direkt unter der Verkehrsmittelauswahl sichtbar und wird ohne ausgewählte Zugreise deaktiviert.
- Technische Providernamen wurden aus Suchfortschritt und Leerzuständen entfernt. Erfolgreiche Prüfungen ohne Treffer bleiben von technischen Abruffehlern getrennt.
- Flix meldet intern eindeutig „Keine Verbindung verfügbar“ oder „Flix konnte nicht geprüft werden“. Stationswahl und GTFS-Zuordnung bleiben automatisch.
- „Weitere Optionen“ wurde entfernt. Seltene Cache- und Flugeinstellungen stehen nach dem Suchbutton unter „Technische Einstellungen“.

## 0.2.5 - 2026-08-24

- Feste Stadt→Hauptbahnhof-Zuweisungen wurden aus dem Ortsresolver entfernt. Reine Städteingaben liefern immer eine Kandidatenliste und werden nie automatisch auf eine einzelne Station festgelegt.
- Schreibvarianten und Übersetzungen wie München/Muenchen/Munich oder Wien/Vienna bleiben reine Suchnormalisierung; sie erzeugen keine feste Stationsidentität.
- Haupt- und Fernverkehrsstationen werden anhand allgemeiner Stationsmerkmale und Provider-Verkehrsarten priorisiert. Flughäfen erscheinen nur bei erkennbarem Flughafen-Kontext.
- „Nur Hinfahrt“ deaktiviert und dimmt Rückreisedatum sowie Kalenderbutton. Beim Zurückschalten wird eine gültige positive Reisedauer wiederhergestellt; Einweg-Payloads senden stets Dauer `0`.
- Die Split-Ticket-Suche ist als kleiner, direkt sichtbarer Haken im Reiseformular aktivierbar und nicht mehr unter „Weitere Optionen“ versteckt.
- Providerbezeichnungen mit geklammerten Busstationszusätzen werden für Flix als Ortsabfrage normalisiert; die ausgewählte native Stations-ID bleibt unverändert erhalten.

## 0.2.3 - 2026-08-24

- Flix-Liveverbindungen mit Umstiegen werden als eigenständige Fahrten übernommen, wenn der direkte GTFS-Fahrplan keine durchgehende `trip_id` besitzt; Leipzig–Dresden–Görlitz bleibt als reale Regression abgedeckt.
- Flix-Stadtsuchen normalisieren allgemeine Bahnhofszusätze, statt sich auf einen ungeeigneten ersten Autocomplete-Treffer zu verlassen. Bestätigte Stations-Alias-IDs verhindern unnötige Zubringer oder falsche Zielzuordnungen.
- FlixTrain und FlixBus sind direkt im Hauptformular auswählbar. Manuelle Flix-Haltestellenfelder entfallen; die bestehende providerübergreifende Stationszuordnung arbeitet automatisch.
- Ein gemeinsamer Kalender steuert Hin- und Rückreise. Der doppelte Abreisekalender entfällt, die Rückreise nutzt dieselben Mindestdatums-, Monats- und Auswahllogiken.
- Die Betreiberanzeige für Mobilfunkabdeckung ist innerhalb der Fahrtkarte kompakter und bleibt ausschließlich auf tatsächlich berechnete OpenCellID-Betreiberwerte beschränkt.
- Routing-Caches verwenden eine neue Generation, damit frühere leere Flix-Ergebnisse nach dem Update nicht weiter ausgeliefert werden.

## 0.2.2 - 2026-08-24

- Provider-native Stations-IDs bleiben beim Zusammenführen als Alias-Menge erhalten; Flix löst ausgewählte Parent-, Child- und Geschwisterhaltestellen gemeinsam auf, damit Bus- und Zugfahrten desselben Stationskomplexes nicht verloren gehen.
- Stations- und Provider-Caches erhalten neue Generationen, sodass korrigierte Hauptstations- und Routingzuordnungen unmittelbar wirksam werden.
- OpenCellID verarbeitet lokale CSV-Bestände und berechtigte Bereichsabfragen mit getrennten MCC/MNC-Filtern, protokolliert Streckenpunkte, Zellanzahl, erkannte Betreiber, unbekannte Codes und Datenqualität und erfindet bei unzureichender Evidenz weiterhin keine Betreiberwerte.
- Der produktive Server verwendet einen lokalen, API-credit-freien OpenCellID-Snapshot für MCC 262; der Datenbestand bleibt aufgrund seiner Größe und Lizenz getrennt vom Docker-Image und Repository.

## 0.2.1 - 2026-08-24

- Technische Stations-, Provider- und Datenbank-IDs aus den Suchvorschlägen entfernt; angezeigt werden nur Stationsname und Ort/Land.
- Stationsranking priorisiert exakte Namen, Haupt-/Parent-Stationen und zentrale Bahnhöfe vor Ausgängen, Zugängen, Bahnsteigen und technischen Teilstationen; `Görlitz Hbf` ist live regressionsgeprüft.
- Mobilfunkbereich in Fahrtkarten deutlich kompakter gestaltet und Quellen-, Lizenz- sowie Methodentexte aus der Kartenansicht entfernt.
- Mobilfunkdarstellung auf einzelne Betreiberwerte vorbereitet; Schwellenwerte wie „mindestens 1/2/3 Netze“ werden nicht mehr in der Oberfläche ausgegeben. Der aktuelle anbieterneutrale BNetzA-Raster wird nicht fälschlich einzelnen Betreibern zugeschrieben.
- OpenCellID als optionale Betreiberquelle mit MCC/MNC-Zuordnung, Vollstreckenprüfung, Qualitätsgrenze und Versorgungslückenerkennung ergänzt; lokale CSV-Downloads und berechtigte API-Zugriffe sind konfigurierbar.
- Leere Flix-Suchen werden als „Keine Flix-Verbindung verfügbar“ behandelt; nur technische Fehler erscheinen als „Flix konnte nicht geprüft werden“.
- Providerergebnisse unterscheiden nun maschinenlesbar zwischen gefundener Verbindung, erfolgreicher Prüfung ohne Verbindung und technischem Abruffehler.

## 0.2.0 - 2026-08-24

- Flexible Preissuche für Bodenreisen über 3, 7 oder individuell 1 bis maximal 14 aufeinanderfolgende Reisetage.
- Tagespreise werden ausschließlich aus den bestehenden DB-, Transitous-, Flix- und TRVL-Bodenpfaden gewonnen; Fahrplan und Preisadapter bleiben unverändert.
- Der günstigste Tag wird sichtbar markiert, während fehlende Preise ausdrücklich als „Preis offen“ erscheinen und nicht geschätzt werden.
- Die Rückreise wird bei einer Tagesauswahl um denselben Abstand verschoben, sodass die gewählte Reisedauer erhalten bleibt.
- Mehrtagesabfragen nutzen den bestehenden Journey-/Provider-Cache und sind auf zwei parallele Tagesläufe begrenzt.
- Responsive Kalenderkarten und direkte Tagesauswahl wurden für Desktop- und Mobilansichten ergänzt.

## 0.1.1 - 2026-08-24

- Föderierter Stationskatalog aus vorhandenen DB-, Transitous- und Flix-GTFS-Quellen; keine redundante Stationsdatenbank.
- Start und Ziel werden über bestätigte, provider-native Stations-IDs statt ungesichertem Freitext an die verfügbaren Routingquellen übergeben.
- Exakte Stationsnamen und eindeutige internationale Aliase werden automatisch bestätigt; nur geografisch mehrdeutige Treffer wie Wien/Vienna und Vienna, Virginia erfordern eine sichtbare Nutzerauswahl.
- Stationsgruppen werden anhand realer Koordinaten zusammengeführt und bewahren getrennte DB-, Transitous- und Flix-IDs für dieselbe aktuelle Suche.
- Flughafen- und Bahnhofskontext bleiben strikt getrennt; Provider ohne passende ID werden bei einer bestätigten mehrdeutigen Auswahl übersprungen statt auf einen ähnlich klingenden Ort zu raten.
- Der bestehende SQLite/WAL-Komponentencache speichert Stationsauflösungen fünf Minuten und verhindert doppelte Providerabfragen.

## 0.1.0 - 2026-08-24

- Der Coverage Analyzer bewertet nach jeder Bodenreise mobiles Breitband (4G oder 5G) entlang des Streckenverlaufs für Fern-/Regionalbahn, FlixTrain, FlixBus und internationale Partnerverbindungen.
- `coverage/provider.py`, `mapper.py`, `analyzer.py` und `cache.py` kapseln die asynchron nachgeladene, nicht blockierende Analyse.
- Vorhandene Geometrien werden bevorzugt; Flix-GTFS-Shapes und koordinierte Zwischenhalte werden übernommen, sonst werden Haltepunkte über Transitous aufgelöst und mit gekennzeichneter Näherung interpoliert.
- Ein Route-Hash und der bestehende SQLite/WAL-Cache speichern erfolgreiche Ergebnisse sieben Tage; Fehler werden nicht gecacht.
- Quelle ist der offen lizenzierte CSV-Datensatz des Mobilfunk-Monitorings. Angezeigt wird die konservative Streckenquote mit mindestens einem, zwei oder drei Netzen; lokale Betreiberidentitäten werden nicht erfunden.
- Ein konfigurierbares Abfahrtsvorfenster (`SEARCH_DEPARTURE_TOLERANCE_MINUTES`, Standard 15 Minuten) berücksichtigt knappe Frühabfahrten, kennzeichnet sie sichtbar und sichert den internationalen LE-232-Fall ab.
- Ein gemeinsamer, kontextabhängiger Ortsresolver priorisiert exakte Stationsnamen und Unicode-Schreibweisen; internationale Stadtaliase greifen erst als Fallback und Flughafen-Aliase nur bei Flughafen-Kontext.
- Deutsche/englische GTFS-Ortszuordnungen und internationale Verbindungen bleiben regressionsgetestet.

## 0.0.6 - 2026-08-24

- Die nicht mehr nutzbare externe Unterkunftsschnittstelle wurde vollständig aus Laufzeitcode, Merge-Logik, Oberfläche, Docker-Konfiguration, Beispielumgebung, Dokumentation und Tests entfernt.
- Hotels laufen weiterhin über den isolierten trvl-Hotelpfad mit verifizierten Gesamtpreisen, schnellem Headline-Fallback und manuellem Google-Hotels-Gegencheck.

## 0.0.5 - 2026-08-24

- PR #5 wurde im vollständigen Planner-Pfad mit `max_results` 10, 24 und 48 validiert; Hotelanfragen bleiben auf das gültige Maximum 10 begrenzt und verursachen keine Pydantic-/HTTP-500-Fehler.
- PR #6 wurde gegen den echten Flix-GTFS-Feed validiert: München/Munich, Köln/Cologne und Wien/Vienna lösen dieselben korrekten Haltestellen auf.
- Frische GTFS-Daten werden lock-frei gelesen, während Refreshes weiterhin serialisiert und atomar installiert werden; parallele Suchen warten nicht mehr hinter einem unnötigen Feed-Lock.
- Die rein additive Bahn-Historienanreicherung besitzt ein hartes Gesamtbudget und einen isolierten Threadpool. Langsame oder abgebrochene Historien-Downloads blockieren weder GTFS/Cache/DB noch nachfolgende Suchen.
- Vier parallele reale Bodenreisen liefen in 22–31 Sekunden; unmittelbar folgende Suchen liefen in 21–23 Sekunden statt zuvor bis zu 131 Sekunden im gleichen Container.
- FlixBus-/FlixTrain-Zeiten, Livepreise und HTTPS-Direktlinks sowie DB-Verbindungen wurden in den Strecken München, Köln und Wien nach Berlin geprüft.
- Flug ohne Hotel und Flug mit realer Hotelanreicherung (`max_results=24`) liefen mit HTTP 200; ein fehlerhafter externer Hotelprovider blieb auf die Unterkunftssuche begrenzt.
- `curl_cffi` wurde von 0.16.0 auf 0.16.1 aktualisiert. Die direkten `db-vendo-client`-Abhängigkeiten `qs` und `uuid` werden im Image sicherheitsbedingt auf 6.15.3 bzw. 11.1.1 angehoben.
- TRVL `v1.21.4` wurde als weiterhin neuestes offizielles Release bestätigt und inklusive CLI-Verträge erneut gebaut und geprüft.

## 0.0.4 - 2026-08-21

- FlixBus- und FlixTrain-Fahrpläne werden aus dem strukturierten europäischen Transitous-GTFS mit vollständiger `calendar.txt`-/`calendar_dates.txt`-Auswertung gelesen.
- GTFS-Zeiten über 24 Uhr, Europe/Berlin-Ausgabe, Nachtfahrten, allgemeines Haltestellenmatching und strukturierte Agency-/Route-Type-Klassifikation sind abgedeckt.
- Feed-Updates erfolgen täglich mit bedingtem Download, vollständiger Validierung und atomarem Datenbanktausch; bei Updatefehlern bleibt der letzte gültige Feed aktiv.
- GTFS-Verbindungen erfinden keine Preise und werden nie als Deutschlandticket-abgedeckt markiert.
- Echte Preise aus der Flix-Such-API werden nur bei einer eindeutigen Übereinstimmung von Verkehrsmittel, Abfahrt und Ankunft an eine GTFS-Fahrt angefügt; sonst bleibt der Preis offen.
- Das starre 10er-Limit wurde durch standardmäßig 24 und maximal 48 Verbindungen ersetzt; die sichtbare Liste ist chronologisch.
- Kalender, mobile Karten und nicht-sticky Mobile-Navigation wurden überarbeitet.
- Ein requestisolierter Live-Loader zeigt echte Zustände von DB, Transitous, GTFS, FlixBus, FlixTrain und Ergebnisaufbereitung.
- Eine getrennte atomare History-Snapshot-Schicht kann tägliche, JSON-sichere Beobachtungen 30 Tage halten; beschädigte Daten werden verworfen und secret-verdächtige Felder abgelehnt.
- Ein fehlertoleranter Lifespan-Scheduler archiviert bereits berechnete History-Statistiken standardmäßig einmal täglich außerhalb von Nutzeranfragen und kann vollständig deaktiviert werden.
- Die Testumgebung ist als installierbares Python-Projekt mit separaten pytest-Abhängigkeiten reproduzierbar.

## 0.0.3 - 2026-08-14

- Direkte Docker-/Compose-Installationen benötigen keinen manuell gesetzten `DB_CFFI_TOKEN` mehr.
- Der interne Bridge-Token wird kryptographisch sicher erzeugt und im privaten Docker-Volume `traviorel-secrets` persistent gespeichert.
- Bestehende explizite `DB_CFFI_TOKEN`-Konfigurationen bleiben kompatibel und haben Vorrang.
- `curl_cffi` bleibt vollständig im App-Image enthalten.


## 0.0.2 - 2026-08-14

- Das Standard-Ergebnisbudget wurde auf zehn Verbindungen erweitert.
- Bei aktivierter Suche werden bis zu zwei tatsächlich verfügbare FlixTrain- und zwei FlixBus-Verbindungen berücksichtigt; die übrigen Plätze werden bevorzugt mit Bahnverbindungen gefüllt.
- FlixTrain und FlixBus werden getrennt klassifiziert und können einander nicht mehr durch die Reihenfolge der Providerantwort aus dem Ergebnisfenster verdrängen.
- Traviorel wertet den gesamten von trvl gelieferten Flix-Rohpool aus, bevor Zeitfenster, Flags, Deduplizierung, Ranking und sichtbare Auswahl angewendet werden.
- Konkrete Flix-Haltestellen werden über `station_id` aus aktuellen Flix-Daten aufgelöst und mit Name, Stadt, Adresse und Koordinaten erhalten.
- Die Oberfläche unterstützt eine automatische sowie eine verbindliche manuelle Auswahl konkret verfügbarer Flix-Halte.
- Generische Access-/Egress-Verbindungen verbinden den angefragten Bahnhof mit abweichenden tatsächlichen Flix-Halten; konkrete Halte und einzelne Segmente bleiben sichtbar.
- `departure_after`, ein Flix-Transferpuffer von 30 Minuten, Gesamtdauer und Kosten gelten für die vollständige Reisekette. Der Flughafenpuffer bleibt davon unabhängig bei standardmäßig 120 Minuten.
- Deutschlandticket-Zubringer werden nur bei tatsächlicher Abdeckung mit 0 EUR Zusatzkosten angesetzt. Unbekannte oder nicht belegte Zubringerpreise werden nicht als kostenlos ausgegeben.
- Unaufgelöste oder falsch zugeordnete Provider-Endpunkte und unplausible Fernumwege werden verworfen.
- Regressionstests für Flix-Auswahl, Haltestellenrouting, manuelle Stationswahl, Access/Egress, Zeitfenster und Ergebnisbudgets wurden erweitert.

## 0.0.1

- Erste öffentliche Version von Traviorel.
