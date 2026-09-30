# Traviorel 0.3.1

Diese Version bündelt die seit 0.3.0 entwickelte Reiseplanung und die geprüften Sicherheitskorrekturen.

## Neu und verbessert

- Aktuelle DB-Verspätungen, ein lokaler Verspätungsindex, Preisverläufe und zusätzliche MCP-Werkzeuge. Open WebUI kann den Streamable-HTTP-Endpunkt `/mcp` direkt einbinden; der Server liefert lesbaren Text und strukturierte Daten.
- Direkte Flix-Preise, Flughafen-Transfers über Transitous sowie Flug- und Hotelsuche über Skiplagged mit den vorhandenen Rückfallwegen.
- Optionales `traviorel-app-lite` ohne trvl. Das Standardimage verwendet weiterhin das fest gepinnte trvl v1.21.4.
- Bessere Prüfung von Reiseeingaben und HTTP-Weiterleitungen, begrenzte GTFS-Downloads und dbnav-Abfragen sowie kein gemeinsames Caching fehlgeschlagener Ergebnisse.
- Der dbnav-Grenzwert gilt bei Split-Suchen auch während der Preisaktualisierung.

## Docker-Images

GitHub Actions veröffentlicht für `linux/amd64` und `linux/arm64`:

- `ghcr.io/lesecuritae/traviorel-app:0.3.1`
- `ghcr.io/lesecuritae/traviorel-app-lite:0.3.1`
- `ghcr.io/lesecuritae/traviorel-db-api:0.3.1`

Die Installation mit `compose.yml` verwendet weiterhin die `latest`-Tags. Nach dem Release holt `docker compose pull && docker compose up -d --no-build` die aktualisierten Images.

## Prüfung

Die Python-Regressionssuite bestand mit 88 Tests; der isolierte db-api-Selbsttest war ebenfalls erfolgreich. Der Docker-Workflow baut die drei Images für beide Architekturen aus dem Release-Tag.
