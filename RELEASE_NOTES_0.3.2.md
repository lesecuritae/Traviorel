# Traviorel 0.3.2

Diese Patchversion aktualisiert die trvl-Abhängigkeit des Standardimages von v1.21.4 auf [v1.24.0](https://github.com/MikkoParkkola/trvl/releases/tag/v1.24.0), fest gepinnt auf Commit `4f2b0d099e51931584826d4feecb14a2cfab5f66`.

## Was sich durch das Update ändert

- trvl verwendet einen neueren Wizz-Air-API-Stand und aktualisierte Go-Abhängigkeiten.
- trvl hat zusätzlich Open-Jaw-Reisen, Sitzplatzlinks und neue MCP-Protokollfunktionen erhalten. Traviorel ruft diese neuen Funktionen derzeit nicht auf; sein eigener MCP-Endpunkt `/mcp` bleibt unverändert.
- Die von Traviorel genutzten CLI-Befehle `flights`, `hotels`, `ground`, `airport-transfer` und `route` behalten ihre benötigten Flags. Die JSON-Modelltypen für Flug-, Hotel- und Bodenverbindungen sind zwischen beiden trvl-Versionen unverändert.

Das optionale Image `traviorel-app-lite` enthält weiterhin kein trvl.

## Docker-Images

- `ghcr.io/lesecuritae/traviorel-app:0.3.2`
- `ghcr.io/lesecuritae/traviorel-app-lite:0.3.2`
- `ghcr.io/lesecuritae/traviorel-db-api:0.3.2`

Die Images werden für `linux/amd64` und `linux/arm64` gebaut. Bestehende Installationen mit `latest` können nach Veröffentlichung mit `docker compose pull && docker compose up -d --no-build` aktualisiert werden.
