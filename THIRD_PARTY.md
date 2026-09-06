# Drittanbieter

## Bundesnetzagentur Mobilfunk-Monitoring

Traviorel verwendet den ausdrücklich weiterverarbeitbaren CSV-Datensatz `202601_MobilfunkMonitoring.csv` (Datenstand Dezember 2025), nicht die anbieterspezifischen Web-Kacheln. Daraus wird intern ein kompakter 100-m-Rasterindex abgeleitet. Die Daten beschreiben prognostizierte Outdoor-Versorgung und nennen je Technologie die Zahl versorgender Netze, aber keine lokale Betreiberidentität. Diese Werte werden deshalb nicht einzelnen Betreibern zugeschrieben.

- Datensatz: https://data.bundesnetzagentur.de/Bundesnetzagentur/GIGA/DE/MobilfunkMonitoring/2512/202601_MobilfunkMonitoring.zip
- Lizenz: Datenlizenz Deutschland – Namensnennung – Version 2.0
- Quellenvermerk: © Bundesnetzagentur; Rasterbasis © GeoBasis-DE / BKG (2025)
- Methodik: https://gigabitgrundbuch.bund.de/GIGA/DE/MobilfunkMonitoring/start.html
- Rechtliche Hinweise: https://gigabitgrundbuch.bund.de/GIGA/DE/Impressum/start.html
- Datenursprung: Netzbetreiberangaben mit Plausibilitätsprüfung durch die Bundesnetzagentur

## OpenCellID

Optionale betreiberbezogene Streckenauswertungen verwenden OpenCellID-Zellstandorte. Traviorel ordnet ausschließlich bekannte deutsche MCC/MNC-Kombinationen den öffentlichen Netzen zu und zeigt bei unzureichender räumlicher Evidenz keine Werte. Eine lokale OpenCellID-CSV kann über `OPENCELLID_CSV_PATH`, der zugangsbeschränkte Bereichsdienst über `OPENCELLID_API_KEY` konfiguriert werden. API-Nutzung und Downloads unterliegen den OpenCellID-Zugangsbedingungen.

- Projekt und API: https://opencellid.org/
- Datenformat: https://wiki.opencellid.org/wiki/Database_format
- Lizenz des Datenbankdownloads: Creative Commons Attribution-ShareAlike 4.0 International
- Betreiberzuordnung: MCC/MNC-Zuteilungsliste der Bundesnetzagentur, Stand 24.02.2026

Traviorel lädt die beiden großen Transportbibliotheken beim Container-Build aus ihren jeweiligen Upstream-Repositories. Die folgenden Lizenzbedingungen gelten unabhängig von der Traviorel-Projektlizenz.

## trvl

- Projekt: [MikkoParkkola/trvl](https://github.com/MikkoParkkola/trvl)
- Build-Pin: Tag `v1.21.4`
- Lizenz: **PolyForm Noncommercial License 1.0.0** (<https://polyformproject.org/licenses/noncommercial/1.0.0/>)
- Required Notice: `Copyright (c) 2026 Mikko Parkkola (https://github.com/MikkoParkkola/trvl)`
- Die Lizenz erlaubt nur die dort definierten nichtkommerziellen Zwecke. Für kommerzielle Nutzung ist eine separate Lizenz des trvl-Rechteinhabers erforderlich.
- Die Originallizenz wird beim Container-Build nach `/usr/share/licenses/trvl/LICENSE` kopiert.

## db-vendo-client

- Projekt: [public-transport/db-vendo-client](https://github.com/public-transport/db-vendo-client)
- Build-Pin: Commit `5afe12a80e02cf111f38f3fa30ae87aaa532d1d6`
- Lizenz: **ISC License**
- Die Originallizenz wird beim Container-Build nach `/usr/share/licenses/db-vendo-client/LICENSE.md` kopiert.

## Historische Deutsche-Bahn-Daten

Traviorel verwendet optional das Hugging-Face-Dataset
[`piebro/deutsche-bahn-data`](https://huggingface.co/datasets/piebro/deutsche-bahn-data).
Die Daten basieren auf der offiziellen DB-Timetables-API und stehen laut Dataset-Angabe
unter [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

Quelle und Bearbeitung: `piebro/deutsche-bahn-data`; Traviorel filtert die monatlichen
Parquet-Dateien auf benötigte Zugläufe und berechnet daraus eigene aggregierte
Verspätungs- und Zuverlässigkeitswerte. Traviorel übernimmt keinen Quellcode des
Dataset-Repositories.

## Weitere verwendete Projekte und Dienste

[Transitous](https://github.com/public-transport/transitous) wird als externe Routingquelle für öffentlichen Verkehr und Transfers verwendet. [BetterBahn](https://github.com/BetterBahn/betterbahn) wird in der README als ursprüngliche Motivation für das integrierte Split-Ticketing genannt; Traviorel übernimmt keinen BetterBahn-Code.

**Delay** gab die Anregung, historische Verspätungsdaten bei der Bewertung konkreter
Verbindungen einzubeziehen. Delay ist keine Datenquelle oder Abhängigkeit von Traviorel.
Die historischen Daten stammen aus `piebro/deutsche-bahn-data`; Filterung, Berechnung und
lokaler Zug-/Monatscache sind eine eigenständige Traviorel-Implementierung.

## Direkte Python-Abhängigkeiten

Die direkt in `tool/requirements.txt` deklarierten Python-Pakete behalten ihre jeweiligen Upstream-Lizenzen. Dazu gehören FastAPI, HTTPX, Pydantic, Uvicorn, `curl_cffi` und DuckDB. Die tatsächlich installierten Versionen ergeben sich aus den dort definierten Versionsgrenzen; `curl_cffi` ist auf `0.16.1` gepinnt.

Diese Datei ist eine Übersicht und ersetzt keine Originallizenz. Beim Verteilen gebauter Images müssen die darin enthaltenen Drittanbieter-Lizenzdateien und Hinweise erhalten bleiben.
