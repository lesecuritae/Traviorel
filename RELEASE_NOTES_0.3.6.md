# Traviorel 0.3.6

## Deutsch

- MCP: Das Mitternachts-Suchfenster für `live_delays` blieb beim Abfahrtstag statt (bei einer Abfahrt zwischen 00:00 und 00:19) auf den Vortag zu wechseln, wie es die Preisfensterprüfung der normalen Suche verlangt. Dadurch meldete `live_delays` genau die Fälle, die der vorherige Fix beheben sollte, weiterhin als „nicht gefunden". Das Fenster bleibt jetzt auf dem Abfahrtstag und kappt die Startzeit auf 00:00, statt auf den Vortag zu wechseln.
- `/api/diagnostics` gab weiterhin den Cache-Dateipfad und rohe trvl-Fehlertexte preis, obwohl diese Felder ausdrücklich als intern gekennzeichnet sind; beide werden jetzt auf Status-/OK-Felder reduziert.
- `scripts/container-check.sh` meldete nie, wenn pytest im Produktionsimage fehlt: die Dateischleife brach vorher mit `ModuleNotFoundError` an der ersten pytest-Testdatei ab. Die Prüfung läuft jetzt zuerst, pytest-Testdateien werden bei fehlendem pytest gezielt übersprungen.
- Dreifach duplizierte Eingabefehler-Zuordnung in den MCP-Werkzeugen nutzt jetzt einen gemeinsamen Helfer.

- db-api: Split-Preisanalyse akzeptierte unverifizierte Tarife (den `journey.price`-Fallback ohne Fahrscheinliste, nicht klassifizierte Fahrscheine) als nutzbaren Preis — genau die Tarife, die die normale Suche selbst verbirgt.
- Eine Anfrage mit `first_class: true` bekam nie einen Preis, da erste-Klasse-Fahrscheine in der Preisermittlung grundsätzlich verworfen wurden, unabhängig von der angefragten Klasse.
- Abfahrtstafeln mit Zielangabe cachten jede Kandidatenverbindung für die Teilanalyse, obwohl nur das erste Teilstück gelesen und das Analyse-Token nie eingelöst wird. Das füllte den geteilten 200-Einträge-Cache und ließ echte `/split`-Tokens vorzeitig verfallen.
- Eine langsame Teilanalyse verwarf bei Zeitüberschreitung alle bereits gefundenen Ersparnisse, statt die fertigen Teilergebnisse zu behalten.
- Eine erfolgreiche Suche hob eine DBnav-Sperre sofort wieder auf, selbst wenn eine eingebettete Preisaktualisierung in derselben Anfrage die Sperre gerade erst wegen `OPS_BLOCKED` gesetzt hatte.
- Die Stationssuche (`/locations`, bei jedem Tastendruck) umging die DBnav-Drosselung und Schutzschaltung vollständig, anders als jeder andere DBnav-Aufruf.
- `OPS_BLOCKED` wurde nur bei HTTP-2xx-Antworten der Bridge erkannt; eine Blockademeldung mit anderem Statuscode löste die Schutzschaltung nicht aus.
- Eine 404-Antwort („Station von diesem Profil nicht gefunden") brach `/departures` komplett ab, statt wie bei der normalen Suche das andere Profil zu versuchen.
- Der bisher nie aufgerufene `REISEVERGLEICH_SELF_TEST`-Selbsttest (eigenständige Prüfungen mit simulierten DB-/DBnav-Clients, kein echtes Netzwerk) läuft jetzt automatisch in der CI, mit einer neuen Prüfung für jeden der obigen Punkte.

## English

- MCP: the midnight search window for `live_delays` stayed on the departure day instead of (for a departure between 00:00 and 00:19) moving to the previous day, which is what the normal search's price-window check requires. As a result, `live_delays` kept answering "not found" for exactly the cases the previous fix claimed to solve. The window now stays on the departure's own day and clamps the start time to 00:00 instead of switching days.
- `/api/diagnostics` still leaked the cache's filesystem path and trvl's raw error text, even though both are explicitly documented as internal; both are now reduced to status/ok flags.
- `scripts/container-check.sh` could never report that pytest is missing from the production image: the per-file script loop crashed with `ModuleNotFoundError` on the first pytest-only test file before reaching that check. The check now runs first, and pytest-only files are skipped by name when pytest is absent.
- Reused a single helper for the three places that duplicated the same tool-input-error mapping.

- db-api: split-ticket analysis accepted unverified fares (the `journey.price` fallback with no ticket list, unclassified tickets) as a usable price - the same fares the normal search itself refuses to show.
- A `first_class: true` request never got a price, because first-class tickets were unconditionally dropped during price selection regardless of the requested class.
- Departure boards with a destination cached every candidate journey for split analysis even though only the first leg is read and the analysis token is never redeemed, filling the shared 200-entry cache and evicting real `/split` tokens early.
- A slow split analysis discarded every already-found saving on a timeout instead of keeping the parts that had finished.
- A successful search cleared a DBnav block immediately, even when a nested price refresh in the same request had just set that block via `OPS_BLOCKED`.
- Station autocomplete (`/locations`, fires on every keystroke) bypassed the DBnav concurrency limit and circuit breaker entirely, unlike every other DBnav call.
- `OPS_BLOCKED` was only detected on a 2xx bridge response; a block reported with another status code never tripped the circuit breaker.
- A 404 ("station not found by this profile") aborted `/departures` entirely instead of trying the other profile, unlike the normal search.
- The previously never-invoked `REISEVERGLEICH_SELF_TEST` harness (self-contained checks against mocked DB/DBnav clients, no real network) now runs automatically in CI, with a new assertion for each issue above.

## Validation

`REISEVERGLEICH_SELF_TEST=1 node db-api/server.mjs`, built from the real Dockerfile (so `vendor/` db-vendo-client is present), passed including the new assertions. `bash scripts/check.sh && python -m pytest -q` passed (224 tests). One new assertion was spot-verified to actually fail when its fix is reverted (the DBnav circuit-breaker-reset case), to confirm the regression coverage is meaningful and not vacuous.

No live Deutsche Bahn or DBnav traffic was exercised for this release; all db-api fixes are verified against mocked upstream clients. One nit found during review - `withTimeout` does not actually cancel the underlying DBnav request, and the concurrency gate counts per logical operation rather than per real upstream request - is intentionally left open: a correct fix needs abort-signal plumbing through the vendored db-vendo-client library, which cannot be safely verified without a live DBnav endpoint.
