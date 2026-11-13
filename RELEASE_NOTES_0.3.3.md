# Traviorel 0.3.3

## Deutsch

- DB-Orts-, Fahrplan- und Preisabfragen verwenden `curl_cffi 0.17.0` und standardmäßig Firefox 155. Das Update allein behob die Ablehnung mit den alten Profilen nicht; die neue Kombination wurde mit echten Verbindungen und Preisen geprüft.
- trvl wird von 1.24.0 auf 1.27.0 aktualisiert, einschließlich der bisherigen CLI-Vertragsprüfungen und des festen Commit-Pins. Der db-vendo-client-Pin ist auf v6.11.3 aktualisiert.
- Der Verspätungsindex-Test verwendet einen relativ zum Testdatum gültigen Monat.
- Paketversion, App-/MCP-Version und HTTP-User-Agents sind auf 0.3.3 abgestimmt. Der Packaging-Test prüft ihre Übereinstimmung.
- Aktuelle Python-Abhängigkeiten innerhalb der bestehenden Grenzen wurden geprüft. DuckDB 2.0 sowie separate qs-/uuid-Updates gehören nicht zu diesem Release. Siehe [Abhängigkeitsprüfung](docs/dependency-audit-2026-11.md).

## English

- DB location, journey and fare requests use `curl_cffi 0.17.0` with Firefox 155 by default. Updating the package alone did not resolve rejection with the old profiles; the new combination was verified with actual journeys and prices.
- Update trvl from 1.24.0 to 1.27.0, preserving build-time CLI contracts and the exact commit pin. Update db-vendo-client to the v6.11.3 commit.
- Use a valid relative month in the delay-index regression fixture.
- Align package, app/MCP and HTTP user-agent versions at 0.3.3. The packaging test checks their consistency.
- Validate current Python dependencies within existing constraints. DuckDB 2.0 and separate qs/uuid upgrades are excluded. See the [dependency audit](docs/dependency-audit-2026-11.md).

## Validation

The preceding functional fix passed `bash scripts/check.sh` and 96 pytest tests on Python 3.13. Release metadata changes must pass the same checks before publication. Live provider responses demonstrate point-in-time availability, not guaranteed future access.

## Container tags

After the verified `v0.3.3` tag is published by the release workflow:

- `ghcr.io/lesecuritae/traviorel-app:0.3.3`
- `ghcr.io/lesecuritae/traviorel-app-lite:0.3.3`
- `ghcr.io/lesecuritae/traviorel-db-api:0.3.3`

This document alone does not confirm that these images have been published.
