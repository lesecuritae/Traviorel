# Traviorel

![Traviorel – selbst gehosteter deterministischer Reisevergleich](docs/assets/readme-hero.png)

[← Sprachauswahl](README.md) · [English](README.en.md)

**Traviorel ist ein eigenständiger, selbst gehosteter und deterministischer Reisevergleich mit Fokus auf Reisen aus Deutschland.**

Bahn, Deutschlandticket, Split-Tickets, FlixTrain, FlixBus, Flüge, Flughafenzubringer, Transfers und Unterkünfte landen in einer gemeinsamen Planung. Traviorel schaut dabei nicht nur, ob irgendwo ein günstiger Preis auftaucht. Die einzelnen Teile müssen zeitlich und logisch zusammenpassen.

Aktuelle Version: **0.3.0**

Traviorel verkauft und bucht selbst nichts. Wenn ein Provider einen brauchbaren Angebotslink liefert, kann er direkt aus dem Ergebnis geöffnet werden. Wo kein direkter Link vorhanden ist, gibt es an passenden Stellen einen manuellen Gegencheck, etwa bei der Deutschen Bahn, Google Flights, Google Hotels oder Google Maps. Preis und Verfügbarkeit werden beim eigentlichen Anbieter noch einmal geprüft.

## Warum Traviorel?

Angefangen hat das Ganze mit einem ziemlich einfachen Wunsch: Ich wollte wieder schnell nach Bahnverbindungen suchen und bei Bedarf **Split-Ticketing direkt mitprüfen lassen**.

[BetterBahn](https://github.com/BetterBahn/betterbahn) war dafür lange eine interessante Lösung. Die Grundidee gefällt mir bis heute. Bei meinen Abfragen funktionierte das Ganze zuletzt allerdings mehr schlecht als recht. Gerade beim Split-Ticketing bringt mir die beste Idee wenig, wenn Verbindung oder Preis nicht zuverlässig ankommen.

Also habe ich irgendwann aufgehört darauf zu warten, dass es wieder so funktioniert, wie ich es brauche.

**Danke an BetterBahn für die Motivation, es selbst neu zu bauen.**

Traviorel verwendet keinen BetterBahn-Code und ist kein Fork. Die Bahnlogik, das Split-Ticketing und der eigentliche Reisevergleich wurden für Traviorel neu aufgebaut.

Dabei blieb es nicht bei einer besseren Split-Ticket-Suche. Für Reisen aus Deutschland wollte ich nicht erst bei der DB suchen, danach BetterBahn öffnen, anschließend FlixTrain und FlixBus prüfen und für eine längere Reise noch Flug, Zubringer und Unterkunft einzeln zusammensuchen. Eine Anfrage sollte möglichst viel davon in einem Durchlauf erledigen.

Aus dem ursprünglichen Split-Ticket-Problem wurde deshalb ein deutlich größerer Reisevergleich.

## Für Reisen aus Deutschland gebaut

Der Schwerpunkt liegt bewusst auf Reisen, die in Deutschland beginnen oder hier einen wesentlichen Teil der Reisekette haben. Deshalb sind Deutsche Bahn, Deutschlandticket, deutsche Flughäfen sowie FlixTrain und FlixBus keine nachträglich angehängten Extras.

Eine Reise kann zum Beispiel mit dem Deutschlandticket oder einem DB-Ticket zum Flughafen beginnen, anschließend aus einem Flug bestehen und am Ziel mit einem Transfer und einer Unterkunft weitergehen. Traviorel behandelt diese Teile als zusammenhängende Reise.

Der Zubringer muss vor dem Flug ankommen. Ein eingestellter Flughafenpuffer muss wirklich vorhanden sein. Eine Suche ab 06:00 Uhr darf keine Verbindung um Mitternacht als passende Empfehlung ausgeben. Wenn BER gesucht wird, darf nicht Frankfurt oder Köln/Bonn auftauchen, nur weil dort ebenfalls ein Terminal 1 oder 2 existiert.

Das klingt selbstverständlich. Sobald mehrere Datenquellen zusammenkommen, ist es das nicht mehr.

Genau deshalb verarbeitet Traviorel Verbindungen anhand klarer, nachvollziehbarer Regeln.

## Split-Ticketing ist direkt integriert

Split-Ticketing gehört fest zur normalen Suche. Es ist kein zweites Werkzeug, in das eine zuvor gefundene Verbindung kopiert werden muss.

Wenn die Option aktiviert ist, prüft Traviorel direkt mit, ob eine Bahnverbindung durch mehrere getrennte Tickets günstiger werden kann als mit einem durchgehenden DB-Ticket. Direktpreis und Splitpreis bleiben dabei getrennt. Ein günstiger Splitpreis wird nicht einfach als Preis eines normalen DB-Tickets ausgegeben.

Damit kann ich für diesen Anwendungsfall komplett auf den zusätzlichen BetterBahn-Schritt verzichten.

Traviorel unterscheidet unter anderem zwischen einem tatsächlichen DB-Direktpreis, einem Split-Ticket-Preis, Verbundtarifen, kostenpflichtigen Teilstrecken und einer vollständig vom Deutschlandticket abgedeckten Verbindung. Unbekannte oder nicht sauber zuordenbare Preise bleiben unbekannt, statt geschätzt zu werden.

## Deutschlandticket gleich mitgedacht

Bei der Anfrage kann angegeben werden, ob ein Deutschlandticket vorhanden ist. Traviorel berücksichtigt das direkt bei der Planung.

Eine reine Nahverkehrsverbindung, die vollständig mit dem D-Ticket gefahren werden kann, wird dann mit **0 Euro zusätzlichen Ticketkosten** behandelt. Ein möglicherweise vom Provider gelieferter Verbundpreis wird nicht so dargestellt, als müsste er trotz vorhandenem Deutschlandticket noch einmal bezahlt werden.

Kostenpflichtige Alternativen bleiben trotzdem sichtbar. Ein ICE oder eine andere bezahlte Verbindung kann schließlich deutlich schneller sein oder einen wesentlich besseren Anschluss an einen Flug bieten.

Zusätzlich gibt es einen eigenen **D-Ticket-only-Modus**. Dort werden nur Bodenverbindungen berücksichtigt, die ohne ICE, IC, Flix oder sonstige Zusatzfahrkarte vollständig mit dem Deutschlandticket nutzbar sind. Mehrere normale Umstiege bleiben natürlich möglich.

## Die Bahn-Abfragen

Die komplette DB-Logik läuft unabhängig von trvl.

Traviorel verwendet dafür unter anderem [db-vendo-client](https://github.com/public-transport/db-vendo-client), eigene DB- und DBnav-Verarbeitung sowie `curl_cffi`. Letzteres hilft bei HTTP-Abfragen, bei denen einfache Clients schnell an Bot-Schutz oder anderen Gegenmaßnahmen scheitern.

Ein Ergebnis wird danach nicht blind übernommen. Traviorel prüft Zeitfenster, Preissemantik, Halte, Umstiege und bei kompletten Reisen die weitere Chronologie.

Ein Provider kann ausfallen oder einen Timeout produzieren, ohne automatisch die gesamte Suche mitzureißen.

## FlixTrain und FlixBus

FlixTrain und FlixBus können direkt in denselben Vergleich einbezogen werden. Damit steht eine DB-Verbindung nicht nur gegen ein anderes DB-Ticket, sondern auch gegen alternative Fernverkehrsangebote.

Die Fahrplanverbindungen stammen aus dem europäischen Flix-GTFS bei Transitous. Traviorel wertet Servicekalender und Ausnahmetage selbst aus, unterstützt Nachtzeiten über 24 Uhr und trennt Bus und Zug über strukturierte Agency- und Route-Type-Daten. Echte Preise aus der Flix-Such-API werden nur bei einer eindeutigen zeitlichen Übereinstimmung angefügt; andernfalls bleibt der Preis ausdrücklich offen.

Traviorel kann Flix außerdem in gemischten Reiseketten verwenden. Ein Beispiel wäre eine mit dem Deutschlandticket abgedeckte Nahverkehrsstrecke, ein bezahlter Flix-Abschnitt und anschließend wieder eine D-Ticket-Strecke.

Bei abweichenden tatsächlichen Flix-Halten kann Traviorel passende Zu- und Abbringer einbeziehen. Die Oberfläche bietet standardmäßig eine automatische Haltestellenwahl; alternativ kann ein aktuell vom Provider gelieferter konkreter Halt verbindlich ausgewählt werden.

Auch hier werden die zusätzlichen Kosten nur dort angesetzt, wo tatsächlich ein weiteres Ticket gebraucht wird.

## Aktuelle Warnungen entlang der Reise

Nach einer erfolgreichen Suche lädt Traviorel aktuelle amtliche Warnungen von NINA/BBK separat nach. Berücksichtigt werden Start, Ziel, koordinierte wichtige Zwischenhalte und Flughäfen der angezeigten Reise. Eine Warnung erscheint nur, wenn ihre amtliche Geometrie einen dieser Punkte tatsächlich umfasst; unspezifische deutschlandweite Meldungen werden nicht als Reisehinweis übernommen. Flughafenwarnungen sind reine Ortsinformationen und bedeuten nicht automatisch, dass eine dort verkehrende Bahn-, Bus- oder Flugverbindung betroffen ist.

Die Warnungen verändern weder Verbindung, Ranking noch Preis und lösen keine automatische Umplanung aus. Listen, Geometrien und Details werden fünf Minuten gecacht. Ist der Dienst nicht erreichbar oder liegt keine passende Warnung vor, bleibt die Reiseausgabe unverändert.

## trvl ist optional

Bahn, Flix, Flüge, Hotels und Flughafen-Transfers laufen über eigene Direktwege (DB-Logik, Flix, Skiplagged, Transitous). Mit `TRVL_ENABLED=0` oder dem Image `traviorel-app-lite` läuft Traviorel ganz ohne trvl. Dann fehlen nur Taxi-Schätzungen und Anbieter, die es nur über trvl gibt. Solange trvl im Image ist, ist es zusätzlich ein Rückfall, wenn ein Direktweg ausfällt.

## Warum trvl drin ist

Bei der Suche nach brauchbaren Datenquellen bin ich auf [trvl](https://github.com/MikkoParkkola/trvl) gestoßen.

trvl ist keine fertige Reisevergleichs-Webseite. Es ist ein lokales Werkzeug und eine umfangreiche Provider-Schicht für Reisedaten. Genau diese Provider waren für Traviorel interessant.

Traviorel nutzt trvl deshalb als **Teilbasis**, vor allem für ausgewählte Flug- und Unterkunftsdaten. Es ist aber kein trvl-Frontend.

Die Weboberfläche, DB-Logik, Split-Ticketing, Deutschlandticket-Auswertung, Preisprüfung, Zeitfenster, Flughafenprüfung und die Zusammenstellung der eigentlichen Reisekette stammen aus Traviorel.

trvl liefert an ausgewählten Stellen Daten. Traviorel entscheidet anschließend nach festen Regeln, was davon tatsächlich zusammenpasst.

## Flüge

Die Flugabfragen laufen bewusst providerweise und mit eigenen Zeitlimits. Ein langsamer oder kaputter Provider soll nicht die komplette Reiseplanung blockieren.

In Traviorel 0.2.0 werden zuerst **Skiplagged, Ryanair, Vueling und easyJet** abgefragt. Reichen die Ergebnisse nicht aus, folgen **Transavia, Norwegian, Air France/KLM und Wizz Air**.

Die trvl-Sammelabfrage wird dafür nicht einfach unbeschränkt durchgereicht. Traviorel fragt die benötigten Provider isoliert ab, führt brauchbare Ergebnisse zusammen und verwirft chronologisch unplausible Flüge.

Google Flights ist zusätzlich als manueller Gegencheck eingebaut. Es handelt sich in Traviorel 0.2.0 dabei **nicht um einen eigenen automatischen Google-Flights-Provider**, sondern um einen passenden Suchlink für die betreffende Strecke und die gewählten Reisedaten.

## Transfers und öffentlicher Verkehr

Eine Flugreise endet nicht am Flughafen. Deshalb gehören Zubringer und Transfers zur Reisekette.

[Transitous](https://github.com/public-transport/transitous) dient als zusätzliche Quelle für öffentlichen Verkehr und bestimmte Transfers. Je nach Strecke können außerdem DB- oder Flix-Verbindungen einbezogen werden.

Wenn keine automatisch verwertbare Transferoption vorliegt, kann Traviorel einen manuellen Google-Maps-Link anbieten, statt irgendeine Verbindung zu erfinden.

## Unterkünfte

Traviorel kann auch nach einer zur Reise passenden Unterkunft suchen. Check-in und Check-out richten sich dabei nach den berechneten Reisedaten und der gewünschten Aufenthaltsdauer.

Die allgemeine Unterkunftssuche läuft über trvl. In den dort gelieferten Hotel- und Zimmerdaten können je nach Quelle Angebote beziehungsweise Provider wie **Booking.com, Expedia, Hotels.com, Agoda, Trip.com, Kayak, Trivago** oder Direktangebote von Hotels auftauchen. Traviorel behauptet dabei bewusst nicht, jeden dieser Anbieter selbst direkt abzufragen. Es verarbeitet, was die verwendete trvl-Hotelsuche tatsächlich liefert.

**Booking.com ist in der zugrunde liegenden Hotelsuche technisch vorgesehen, in der Praxis aber derzeit keine zuverlässige Quelle.** Der WAF- und Bot-Schutz kann automatisierte Abfragen blockieren. Ein solcher Fehler wird deshalb als Providerproblem behandelt und soll nicht die gesamte Unterkunftssuche oder Reiseplanung abbrechen.

Hotel, Hostel, Apartment und Resort sind getrennte Unterkunftsarten. Eine normale Hotelsuche beginnt standardmäßig bei drei Sternen. Ein Hostel wird nicht allein wegen einer Sterneangabe als Hotel behandelt.

Auch beim Preis wird nicht geraten. Als Gesamtpreis erscheint nur ein Betrag, dessen Bezug auf den vollständigen Aufenthalt verifiziert werden konnte. Nachtpreise oder nicht eindeutig zugeordnete Headline-Preise bleiben entsprechend gekennzeichnet.

Google Hotels steht zusätzlich als manueller Gegencheck zur Verfügung.

## Traviorel plant, der Anbieter bucht

Traviorel ist **keine Buchungsplattform**.

Es werden keine Tickets gekauft, keine Hotelzimmer reserviert und keine Zahlungsdaten verarbeitet. Traviorel sucht, vergleicht und baut daraus eine komplette Reiseplanung.

Wenn ein Provider einen konkreten Angebotslink liefert, zeigt die Oberfläche **„Angebot prüfen/buchen“**. Wenn nur ein sinnvoller Suchlink verfügbar ist, erscheint **„Weitere Angebote öffnen“**.

Je nach Reiseteil führt das beispielsweise zur Deutschen Bahn, zum jeweiligen Reiseanbieter oder zu einem manuellen Gegencheck bei Google Flights, Google Hotels oder Google Maps.

Die eigentliche Buchung erfolgt immer beim Anbieter. Extern gelieferte Preise können sich bis zum Öffnen der Buchungsseite ändern. Traviorel garantiert deshalb keinen Endpreis und führt selbst keine Buchung aus.

## Für Tarnkappe.info entwickelt

Traviorel wurde ursprünglich für [Tarnkappe.info](https://tarnkappe.info/) entwickelt.

Der Ausgangspunkt war keine Demo und kein theoretischer Reiseplaner. Ich brauchte selbst eine Lösung, mit der sich reale Reisen schneller zusammensetzen lassen, inklusive DB-Preisen, Split-Ticketing und den Alternativen daneben.

Aus diesem internen Werkzeug wurde nach und nach das öffentliche Projekt Traviorel. Wenn es schon gebaut ist und funktioniert, kann es schließlich auch anderen nützen.

## Technik im Überblick

```text
Browser UI -> FastAPI -> Traviorel-Orchestrierung
                         |-> DB-Backend (db-vendo-client, DBnav, curl_cffi)
                         |-> Split-Ticket- und D-Ticket-Logik
                         |-> Transitous
                         |-> FlixTrain / FlixBus
                         |-> isolierte trvl-Flugprovider
                         |-> trvl-Hotels
                         `-> SQLite-Cache
```

Provider besitzen getrennte harte Zeitlimits. Ein Timeout wird als Providerfehler behandelt und blockiert nicht automatisch die übrige Reise.

## Historische Zuverlässigkeit von Bahnverbindungen

Traviorel kann gefundene Bahn-Legs additiv mit 90 Tagen historischer Beobachtungen aus
[`piebro/deutsche-bahn-data`](https://huggingface.co/datasets/piebro/deutsche-bahn-data)
anreichern. Die normale Suche, Preise und das Ranking bleiben davon unabhängig.

Die Idee zur historischen Zuverlässigkeitsbewertung entstand unter anderem durch
**Delay**. In unseren Tests erwies sich dessen Ansatz allerdings als wenig zuverlässig,
weil die Abfragen regelmäßig mit Fehlern abbrachen. Traviorel setzt die Funktion deshalb
eigenständig um – einschließlich lokalem Cache und funktionierender Fallback-Logik.

Die benötigten historischen Zugdaten werden gezielt aus `piebro/deutsche-bahn-data`
gefiltert und lokal gespeichert. Bereits vorhandene Zug-/Monatsdaten müssen bei späteren
Abfragen nicht erneut geladen werden. Schlägt ein Remote-Abruf fehl, ist Hugging Face
langsam oder läuft die History-Abfrage in ein Zeitlimit, bleibt die eigentliche
Reiseverbindung trotzdem erhalten. Traviorel fällt dann sauber auf eine Anzeige ohne
Zuverlässigkeitswert zurück, statt die gesamte Suche mit einem 502-Fehler scheitern zu
lassen.

Der Prozentwert einer Direktfahrt bedeutet den empirisch beobachteten Anteil konkreter
Fahrtinstanzen, die **nicht explizit ausgefallen sind und ihr Ziel mit höchstens zehn
Minuten Verspätung erreicht haben**. Fehlende Beobachtungen gelten niemals als Ausfall.

Bei einem Umstieg ist der Wert der empirische Anteil beobachteter Fahrten des zuführenden
Zuges, deren Ankunftsverspätung nicht größer als die planmäßige Umsteigezeit war;
explizite Ausfälle zählen als verpasster Anschluss. Bei mehreren Anschlüssen wird der
Gesamtwert als ausgewiesene Unabhängigkeitsschätzung aus den Einzelwahrscheinlichkeiten
gebildet. Einzelwerte und Stichprobengröße bleiben in den Details sichtbar.

Die Auswertung bevorzugt Zugnummer, Zugtyp, konkreten Streckenabschnitt, Wochentag und
Vier-Stunden-Zeitfenster. Bei zu kleiner Teilstichprobe fällt sie innerhalb derselben
konkreten Zug-/Streckenhistorie auf Wochentag, Zeitfenster und schließlich alle
Beobachtungen zurück. Ohne belastbare Zugnummer und EVA-IDs wird kein Wert erfunden.

DuckDB dient ausschließlich als eingebettete In-Memory-Parquet-Engine. Dauerhaft werden
nur gefilterte Zug-/Monats-Parquets unter `/var/lib/reisevergleich/history` gespeichert;
Metadaten und fertige Statistiken liegen in der bestehenden `cache.sqlite3`. Es wird
keine zweite Datenbank und keine vollständige Datensatzkopie angelegt. Mit
`HISTORY_ENABLED=false` ist die Schicht vollständig deaktiviert.

Davon getrennt kann Traviorel tägliche, JSON-sichere Beobachtungssnapshots unter dem
Unterverzeichnis `snapshots` ablegen. Pro Kennung und Kalendertag existiert höchstens
eine atomar ersetzte Datei; standardmäßig bleiben genau 30 Kalendertage erhalten.
Beschädigte Dateien werden beim Lesen entfernt, und Felder für Tokens, Passwörter,
Cookies, Autorisierungsdaten oder API-Schlüssel werden vor dem Schreiben abgelehnt.
Die Live-Suche schreibt keine Snapshots und wird dadurch weder verzögert noch in
Verbindungen, Preisen oder Ranking beeinflusst. Stattdessen archiviert ein interner
Hintergrundtask ausschließlich bereits fertig berechnete History-Statistiken. Er startet
nach standardmäßig 60 Sekunden und läuft danach einmal täglich. Fehler werden isoliert
protokolliert; der nächste Zyklus läuft weiter. Der Task kann mit
`HISTORY_SNAPSHOT_SCHEDULER_ENABLED=false` deaktiviert werden. Intervall, Startverzögerung
und Aufbewahrung sind über `HISTORY_SNAPSHOT_INTERVAL_SECONDS`,
`HISTORY_SNAPSHOT_INITIAL_DELAY_SECONDS` und `HISTORY_SNAPSHOT_RETENTION_DAYS`
konfigurierbar.

## Installation mit Docker

Voraussetzungen sind Docker, das Docker-Compose-Plugin, Git und OpenSSL. Der offizielle Installationsweg verwendet die fertig veröffentlichten GHCR-Images und baut nichts lokal:

```bash
git clone https://github.com/lesecuritae/Traviorel.git
cd Traviorel



docker compose config -q
docker compose pull
docker compose up -d --no-build
```

Danach prüfen:

```bash
docker compose ps
curl http://127.0.0.1:8791/api/health
```

Die Oberfläche ist unter <http://127.0.0.1:8791> erreichbar.

`DB_CFFI_TOKEN` ist optional; Traviorel erzeugt und persistiert standardmäßig selbst einen sicheren internen Token. Der Token ist ausschließlich ein lokal zufällig erzeugtes internes Secret zwischen `traviorel-app` und `traviorel-db-api`. Er ist kein Benutzerpasswort und kein Traviorel-Login.

Die fertigen Images sind:

```text
ghcr.io/lesecuritae/traviorel-app:latest
ghcr.io/lesecuritae/traviorel-db-api:latest
```

Python, Go, Node.js, trvl und db-vendo-client müssen für die normale Installation nicht lokal gebaut werden. `install.sh` bleibt als optionale Komfortlösung verfügbar, ist aber keine Voraussetzung:

```bash
./install.sh
```

### LAN-Zugriff

Standardmäßig lauscht Traviorel mit `TRAVIOREL_BIND_HOST=127.0.0.1` nur auf dem Docker-Host. Für Zugriff aus dem eigenen LAN in `.env` setzen:

```env
TRAVIOREL_BIND_HOST=0.0.0.0
```

Danach neu anwenden:

```bash
docker compose up -d --no-build
```

Die Oberfläche ist dann unter `http://SERVER-IP:8791` erreichbar. Mit `0.0.0.0` lauscht der Dienst im erreichbaren Netzwerk; Firewall und gegebenenfalls Reverse Proxy müssen entsprechend berücksichtigt werden.

### Betrieb

```bash
# Status
docker compose ps

# Logs
docker compose logs -f app db-api

# Neustart
docker compose restart

# Stoppen
docker compose down

# Starten
docker compose up -d --no-build

# Healthcheck
curl http://127.0.0.1:8791/api/health
```

### Aktualisieren

```bash
git pull
docker compose pull
docker compose up -d --no-build
```

Eine Neukompilierung ist nicht erforderlich.

### Persistenz

Traviorel speichert den persistenten Zustand im Docker-Volume `traviorel-state`. Es ist im App-Container unter `/var/lib/reisevergleich` eingebunden. `docker compose down` erhält das Volume und seine Daten. **Warnung: `docker compose down -v` löscht das persistente Volume und damit die gespeicherten Traviorel-Daten.**

## Entwicklung und lokaler Build

Dieser Weg ist nur für Entwicklung gedacht und für normale Nutzer nicht erforderlich:

```bash
docker compose build
docker compose up -d
```

trvl ist über `TRVL_REF` gepinnt; Traviorel 0.3.0 verwendet standardmäßig `v1.21.4`.

## Eindeutige Stationsauswahl

Bei Bahn- und Busreisen sucht Traviorel Start und Ziel parallel in den vorhandenen DB-Stationsdaten, im internationalen Transitous-Verzeichnis und im bereits geladenen Flix-GTFS. Treffer desselben Halts werden über ihre Koordinaten gruppiert; die Auswahl speichert die nativen IDs je Provider im aktuellen Suchrequest. Dadurch müssen DB, Transitous und Flix einen gewählten Halt nicht erneut aus ähnlich klingendem Freitext erraten. TRVL erhält denselben bestätigten Namen und bleibt für seine Providerpfade kompatibel.

Exakte Stationsnamen und eindeutige Aliase werden automatisch bestätigt. Nur bei echter geografischer Mehrdeutigkeit erscheint eine Auswahl mit Region/Land, beispielsweise für Wien in Österreich gegenüber Vienna in Virginia. Ohne diese Bestätigung beginnt keine Bodenreisesuche. Die Auflösung liegt fünf Minuten im bestehenden SQLite/WAL-Komponentencache; es wird keine zweite Stationsdatenbank gepflegt.

## Flexible Preissuche

Für Bodenreisen kann Traviorel ab dem gewählten Abreisetag aktuelle Verbindungen und Preise über 3, 7 oder individuell bis maximal 14 Tage vergleichen. Jeder Tag läuft durch dieselben DB-, Transitous-, Flix- und TRVL-Bodenadapter wie eine normale Suche. Der günstigste belegte Tagespreis wird markiert; wenn ein Provider nur Fahrplandaten liefert, zeigt die Oberfläche ausdrücklich „Preis offen“. Ein ausgewählter Tag wird anschließend über die normale Detailreise geladen. Die bestehende Reisedauer bleibt dabei erhalten. Der Journey-/Provider-Cache wird wiederverwendet und der Kalender führt höchstens zwei Tagesabfragen gleichzeitig aus.

## Mobilfunkabdeckung entlang der Fahrt

Bei Bodenreisen lädt Traviorel nach der eigentlichen Reiseantwort automatisch eine separate Abdeckungsanalyse je sichtbarer Verbindung. Das gilt verkehrsmittelneutral für DB-Fernverkehr, Regionalbahn, FlixTrain, FlixBus und internationale Partnerverbindungen. Betreiberwerte für Telekom, Vodafone, Telefónica/O2 und – sofern belegt – 1&1 werden aus OpenCellID-Funkzellen entlang der vollständigen Streckengeometrie berechnet. Die Zuordnung erfolgt intern über die aktuellen deutschen MCC/MNC-Blöcke. Reichen die räumlichen OpenCellID-Daten nicht aus, zeigt Traviorel „Betreiberdaten nicht verfügbar“ und schätzt keine Werte. Der anbieterneutrale BNetzA-Raster bleibt nur als interne Zusatzberechnung erhalten. Erfolgreiche Auswertungen werden anhand eines Route-Hashs sieben Tage im bestehenden SQLite-Cache gespeichert.

OpenCellID-Zellstandorte sind eine crowdsourcete Datenbasis und keine Garantie für Empfang oder Datenrate im Fahrzeug. Vorhandene Geometrien und Flix-GTFS-Shapes werden bevorzugt; danach folgen koordinierte Zwischenhalte und erst zuletzt eine gerade Näherung. Fehler der Abdeckungsanalyse verändern oder verzögern die Reiseergebnisse nicht. Für lokale Datenbankdownloads kann `OPENCELLID_CSV_PATH`, für berechtigte API-Zugriffe `OPENCELLID_API_KEY` gesetzt werden.

Für den API-credit-freien Offline-Betrieb erwartet `OPENCELLID_CSV_PATH` eine UTF-8-CSV mit Kopfzeile. Benötigt werden `radio`, `mcc`, `net` (oder `mnc`), `area`/`lac`/`tac`, `cell`, `lon` und `lat`; `range` ist optional. Der Datenbestand wird wegen Größe und Lizenz nicht in das Image eingebettet. Unbekannte MCC/MNC-Paare werden verworfen.

Die Reisesuche berücksichtigt standardmäßig Verbindungen bis zu 15 Minuten vor der gewünschten Abfahrtszeit. Eine solche Abfahrt wird im Ergebnis ausdrücklich als „x Minuten vor gewünschter Zeit“ markiert. Das Fenster ist zentral über `SEARCH_DEPARTURE_TOLERANCE_MINUTES` konfigurierbar; die Nutzereingabe selbst bleibt unverändert.

Die Ortsauflösung prüft bei DB, Transitous und Flix immer zuerst den unveränderten Stationsnamen. Erst bei einem leeren Exakttreffer folgen kontrollierte internationale Varianten wie München/Muenchen/Munich, Köln/Cologne oder Wien/Vienna. Flughafen-Aliase werden ausschließlich bei erkennbarem Flughafen-Kontext (etwa „Airport“, „Flughafen“ oder IATA-Code) eingesetzt; dieselbe Trennung bleibt in den TRVL-Airport-Transfers erhalten.

## Qualitätssicherung

```bash
bash scripts/check.sh
bash scripts/container-check.sh
```

Die Prüfkette umfasst unter anderem Python-, Node- und Shell-Syntax, API- und UI-Verträge, harte Zeitfenster, Flughafenidentität, Preissemantik, Deutschlandticket, Split-Tickets, Provider-Isolation, Transfers, Hotels und Flugchronologie.

Live-Tests benötigen erreichbare externe Provider und werden getrennt von den reproduzierbaren Regressionstests ausgeführt.

## Roadmap

### Coming soon: eigene Provider statt trvl

Der nächste größere Schritt ist bereits geplant: **trvl soll schrittweise vollständig aus Traviorel verschwinden.**

Flug-, Unterkunfts- und weitere Provider, die heute noch über trvl angebunden sind, sollen künftig direkt von Traviorel abgefragt werden. Die vorhandene Traviorel-Logik für Provider-Isolation, Zeitlimits, Fallbacks, Plausibilitätsprüfungen, Preisvergleich und Caching bleibt dabei erhalten und wird direkt mit eigenen Provider-Modulen verbunden.

Ein wichtiger Baustein dafür ist `curl_cffi`, das Traviorel bereits für schwierige HTTP-Abfragen einsetzt. Damit lassen sich Browser-TLS- und HTTP-Fingerprints wesentlich genauer nachbilden als mit klassischen Python-HTTP-Clients. Das schafft bei manchen Anbietern zusätzliche Möglichkeiten für stabile Direktabfragen und providerbezogene Fallbacks.

Das Ziel ist ein Traviorel-Stack ohne externes trvl-Binary:

* direkte Traviorel-Provider für Flüge
* direkte Traviorel-Provider für Unterkünfte und weitere Reisebausteine
* eigene Normalisierung und Fehlerbehandlung
* eigene Cache- und Fallback-Strategien
* keine trvl-Build-Abhängigkeit mehr

Dabei geht es nicht darum, Schutzmechanismen um jeden Preis zu umgehen. Wenn ein Anbieter keine zuverlässig nutzbare Schnittstelle bietet, soll Traviorel das weiterhin transparent anzeigen und andere Provider weiterlaufen lassen.

**Coming soon.**

## Bekannte Grenzen

Traviorel kann nur so aktuelle Daten liefern, wie die jeweiligen externen Provider sie bereitstellen. Anbieter können Schnittstellen ändern, Anfragen blockieren, Preise verändern oder zeitweise gar keine verwertbaren Ergebnisse liefern.

Booking.com ist wegen des WAF-/Bot-Schutzes derzeit ein besonders offensichtliches Beispiel dafür.

Traviorel versucht solche Ausfälle sichtbar zu machen und andere Provider weiterlaufen zu lassen. Es erfindet fehlende Preise oder Verbindungen nicht als Ersatz.

Die Anwendung ist ein Reiseplaner und Preisvergleich, keine Buchungsmaschine. Vor dem Kauf müssen Preis, Tarifbedingungen und Verfügbarkeit immer beim eigentlichen Anbieter geprüft werden.

## Verwendete Projekte

Traviorel wäre ohne bestehende Open-Source-Projekte und externe Dienste nicht in dieser Form möglich.

- [BetterBahn/betterbahn](https://github.com/BetterBahn/betterbahn) – ursprüngliche Motivation für integriertes Split-Ticketing; Traviorel verwendet keinen BetterBahn-Code.
- [MikkoParkkola/trvl](https://github.com/MikkoParkkola/trvl) – Teilbasis für ausgewählte Flug-, Hotel- und weitere Providerfunktionen.
- [public-transport/db-vendo-client](https://github.com/public-transport/db-vendo-client) – technische Grundlage des DB-Backends.
- [public-transport/transitous](https://github.com/public-transport/transitous) – Routingdaten für öffentlichen Verkehr und Transfers.
Weitere Abhängigkeiten und die jeweils geltenden Lizenzbedingungen stehen in [THIRD_PARTY.md](THIRD_PARTY.md).

Traviorel ist unabhängig von Deutsche Bahn, BetterBahn, Flix, trvl, Transitous und den übrigen abgefragten oder verlinkten Reiseanbietern. Marken und Namen gehören ihren jeweiligen Rechteinhabern.

## Lizenz

Der von diesem Repository stammende Traviorel-Code steht unter der [MIT License](LICENSE).

Drittanbieter behalten ihre eigenen Lizenzen. Besonders wichtig ist `trvl v1.21.4`: Diese Abhängigkeit steht unter der **PolyForm Noncommercial License 1.0.0**. Die MIT-Lizenz von Traviorel hebt die nichtkommerzielle Einschränkung von trvl nicht auf. Wer den kompletten Standard-Stack kommerziell einsetzen möchte, muss die Lizenzbedingungen von trvl separat klären.

Details stehen in [THIRD_PARTY.md](THIRD_PARTY.md).

## Unterstützung

Traviorel wird unabhängig entwickelt und bleibt frei nutzbar. Für die Weiterentwicklung, den Betrieb der Infrastruktur und neue Funktionen sind Spenden notwendig, da Serverkosten und Entwicklungsaufwand dauerhaft anfallen.

Wenn dir das Projekt hilft, unterstützt du damit die weitere Entwicklung.

**[Traviorel unterstützen](https://tarnkappe.info/spenden/)**
