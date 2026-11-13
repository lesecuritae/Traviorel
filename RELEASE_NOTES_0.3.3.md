# Traviorel 0.3.3

## Deutsch

- Neue MCP-Abfahrtstafel ohne festes Ziel, optional mit Fahrtziel; Richtungen sowie Plan-/Prognosezeit getrennt. Explizite Bus-, S-Bahn- und Nahverkehrszug-Auswahl gilt nur für die jeweilige Anfrage.
- Serverzeit statt geratenem Datum: „heute“, aktuelle Uhrzeit und relative Angaben wie „in einer Stunde“, einschließlich Datumswechsel. Ein klarer benachbarter Bus-/Bahnhalt wird ohne Auswahl für beide Verkehrsmittel getrennt abgefragt.
- Allgemeine Haltestellenauflösung: keine Bevorzugung eines unpassenden Hauptbahnhofs; nahe, unterschiedliche Haltestellen behalten ihre Identität. Mehrdeutige Orte verlangen eine Auswahl.

- DB-Orts-, Fahrplan- und Preisabfragen verwenden `curl_cffi 0.17.0` und standardmäßig Firefox 155. Das Update allein behob die Ablehnung mit den alten Profilen nicht; die neue Kombination wurde mit echten Verbindungen und Preisen geprüft.
- trvl wird von 1.24.0 auf 1.27.0 aktualisiert, einschließlich der bisherigen CLI-Vertragsprüfungen und des festen Commit-Pins. Der db-vendo-client-Pin ist auf v6.11.3 aktualisiert.
- Der Verspätungsindex-Test verwendet einen relativ zum Testdatum gültigen Monat.
- Paketversion, App-/MCP-Version und HTTP-User-Agents sind auf 0.3.3 abgestimmt. Der Packaging-Test prüft ihre Übereinstimmung.
- Aktuelle Python-Abhängigkeiten innerhalb der bestehenden Grenzen wurden geprüft. DuckDB 2.0 sowie separate qs-/uuid-Updates gehören nicht zu diesem Release. Siehe [Abhängigkeitsprüfung](docs/dependency-audit-2026-11.md).

## English

- New MCP departure board without a required destination, with optional journey-direction filtering. Explicit bus, S-Bahn and local-train selection applies only to the current request.
- Server clock for “today”, current time and relative requests such as “in an hour”, including date rollover. Clearly co-located bus/rail stops are queried separately for both when no mode was selected.
- General station resolution: unrelated central stations cannot outrank the requested place; distinct nearby stops retain their identities. Ambiguous places require selection.

- DB location, journey and fare requests use `curl_cffi 0.17.0` with Firefox 155 by default. Updating the package alone did not resolve rejection with the old profiles; the new combination was verified with actual journeys and prices.
- Update trvl from 1.24.0 to 1.27.0, preserving build-time CLI contracts and the exact commit pin. Update db-vendo-client to the v6.11.3 commit.
- Use a valid relative month in the delay-index regression fixture.
- Align package, app/MCP and HTTP user-agent versions at 0.3.3. The packaging test checks their consistency.
- Validate current Python dependencies within existing constraints. DuckDB 2.0 and separate qs/uuid upgrades are excluded. See the [dependency audit](docs/dependency-audit-2026-11.md).

## Validation

The complete runtime passed `bash scripts/check.sh`, 169 pytest tests on Python 3.13, and the backend self-tests. Disposable live MCP tests and the deployed MCP endpoint verified explicit bus selection, S-Bahn in both directions, separate nearby bus/rail boards, an optional destination and a second city. Cross-provider station IDs now require matching identities, not proximity alone; related-stop departures remain separate. These checks do not prove microphone or model-driven voice execution. Live provider responses demonstrate point-in-time availability, not guaranteed future access.

## Container tags

After the verified `v0.3.3` tag is published by the release workflow:

- `ghcr.io/lesecuritae/traviorel-app:0.3.3`
- `ghcr.io/lesecuritae/traviorel-app-lite:0.3.3`
- `ghcr.io/lesecuritae/traviorel-db-api:0.3.3`

This document alone does not confirm that these images have been published.
