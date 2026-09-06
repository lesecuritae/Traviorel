# Traviorel

![Traviorel – self-hosted deterministic travel comparison](docs/assets/readme-hero.png)

[← Language selection](README.md) · [Deutsch](README.de.md)

**Traviorel is an independent, self-hosted, deterministic travel comparison service focused on journeys originating in Germany.**

Rail, the Deutschlandticket, split tickets, FlixTrain, FlixBus, flights, airport feeders, transfers, and accommodation are combined into a single itinerary. Traviorel does not merely look for a low price somewhere. Every part of the journey must fit together both chronologically and logically.

Current version: **0.3.0**

Traviorel does not sell or book anything itself. If a provider supplies a usable offer link, it can be opened directly from the result. Where no direct link is available, Traviorel offers a suitable manual cross-check, for example with Deutsche Bahn, Google Flights, Google Hotels, or Google Maps. Price and availability must always be verified with the actual provider.

## Why Traviorel?

The project began with a simple request: I wanted to search for rail connections quickly again and, when needed, **have split ticketing checked as part of the same search**.

[BetterBahn](https://github.com/BetterBahn/betterbahn) was an interesting solution for a long time, and I still like its basic idea. In my recent searches, however, it worked increasingly poorly. Especially with split ticketing, even the best idea is of little use if the connection or price does not arrive reliably.

At some point, I stopped waiting for it to work the way I needed again.

**Thanks to BetterBahn for the motivation to build it again independently.**

Traviorel uses no BetterBahn code and is not a fork. Its rail logic, split ticketing, and journey comparison were built anew for Traviorel.

The project did not stop at improving split-ticket searches. For journeys from Germany, I did not want to search DB first, then open BetterBahn, check FlixTrain and FlixBus afterward, and finally assemble flights, feeders, and accommodation separately for a longer trip. One request should handle as much of that as possible in a single run.

The original split-ticket problem therefore grew into a much broader travel comparison service.

## Built for journeys from Germany

Traviorel deliberately focuses on journeys that begin in Germany or include a substantial part of their itinerary here. Deutsche Bahn, the Deutschlandticket, German airports, FlixTrain, and FlixBus are therefore core parts of the system rather than later additions.

A journey may, for example, begin with a Deutschlandticket or DB ticket to the airport, continue with a flight, and finish with a transfer and accommodation at the destination. Traviorel treats these parts as one connected journey.

The feeder must arrive before the flight. A configured airport buffer must actually exist. A search starting at 06:00 must not recommend a midnight connection. If BER is requested, Frankfurt or Cologne/Bonn must not appear merely because those airports also have a Terminal 1 or 2.

That sounds obvious. Once several data sources are combined, it no longer is.

This is why Traviorel processes connections using clear and traceable rules.

## Split ticketing is built in

Split ticketing is part of the normal search. It is not a second tool into which a previously found connection has to be copied.

When enabled, Traviorel checks whether several separate rail tickets could make a connection cheaper than a through DB ticket. The direct fare and split fare remain separate. A low split fare is never presented as the price of a normal DB ticket.

For this use case, the additional BetterBahn step is no longer necessary.

Traviorel distinguishes, among other things, between a real DB direct fare, a split-ticket fare, local transport association fares, paid journey sections, and a connection fully covered by the Deutschlandticket. Unknown prices or prices that cannot be assigned reliably remain unknown instead of being estimated.

## Deutschlandticket included in planning

A request can specify whether a Deutschlandticket is available. Traviorel accounts for it directly during planning.

A local or regional connection fully covered by the D-Ticket is treated as having **EUR 0 in additional ticket costs**. A local association fare returned by a provider is not displayed as if it still had to be paid despite an existing Deutschlandticket.

Paid alternatives remain visible. An ICE or another paid connection can be substantially faster or provide a much better connection to a flight.

There is also a dedicated **D-Ticket-only mode**. It only considers ground connections that can be used entirely with the Deutschlandticket, without ICE, IC, Flix, or any additional ticket. Multiple ordinary transfers remain possible.

## Rail requests

The complete DB logic runs independently of trvl.

Traviorel uses, among other components, [db-vendo-client](https://github.com/public-transport/db-vendo-client), its own DB and DBnav processing, and `curl_cffi`. The latter helps with HTTP requests where basic clients quickly fail because of bot protection or other countermeasures.

Results are not accepted blindly. Traviorel validates time windows, fare semantics, stops, transfers, and—for complete journeys—the subsequent chronology.

A provider may fail or time out without automatically taking down the entire search.

## FlixTrain and FlixBus

FlixTrain and FlixBus can be included directly in the same comparison. A DB connection is therefore compared not only with another DB ticket but also with alternative long-distance services.

Timetable connections come from Transitous' European Flix GTFS feed. Traviorel evaluates service calendars and exceptions itself, supports overnight times beyond 24:00, and distinguishes buses from trains using structured agency and route-type data. Real prices from the Flix search API are attached only when times match unambiguously; otherwise the price remains explicitly unknown.

Traviorel can also use Flix in mixed journey chains. One example is a local section covered by the Deutschlandticket, followed by a paid Flix section and another D-Ticket section.

When the actual Flix stop differs from the requested station, Traviorel can include suitable access and egress legs. The interface defaults to automatic stop selection; alternatively, a concrete stop currently returned by the provider can be selected and enforced.

Additional costs are assigned only where another ticket is actually required.

## Current warnings along the journey

After a successful search, Traviorel separately loads current official NINA/BBK warnings. It checks the origin, destination, coordinated important intermediate stops, and airports of the displayed journey. A warning is shown only when its official geometry actually contains one of those points; unspecific nationwide notices are not treated as journey warnings. Airport warnings are location information only and do not automatically mean that a train, bus, or flight serving that airport is affected.

Warnings never alter connections, ranking, or prices and cannot trigger automatic replanning. Lists, geometries, and details are cached for five minutes. If the service is unavailable or no relevant warning exists, the journey output remains unchanged.

## Why trvl is included

While looking for usable data sources, I found [trvl](https://github.com/MikkoParkkola/trvl).

trvl is not a finished travel comparison website. It is a local tool and an extensive provider layer for travel data. Those providers were precisely what made it interesting for Traviorel.

Traviorel therefore uses trvl as a **partial foundation**, especially for selected flight and accommodation data. It is not a trvl frontend.

The web interface, DB logic, split ticketing, Deutschlandticket evaluation, fare validation, time windows, airport validation, and assembly of the actual itinerary are Traviorel implementations.

trvl supplies data in selected places. Traviorel then applies deterministic rules to decide which results actually fit together.

## Flights

Flight providers are queried separately and have their own time limits. A slow or broken provider must not block the entire journey plan.

Traviorel 0.2.0 first queries **Skiplagged, Ryanair, Vueling, and easyJet**. If those results are insufficient, it continues with **Transavia, Norwegian, Air France/KLM, and Wizz Air**.

The aggregated trvl request is not passed through without limits. Traviorel queries the required providers in isolation, combines usable results, and rejects chronologically implausible flights.

Google Flights is also included as a manual cross-check. In Traviorel 0.2.0 it is **not a separate automated Google Flights provider**, but a suitable search link for the requested route and travel dates.

## Transfers and public transport

A flight journey does not end at the airport. Feeders and transfers are therefore part of the itinerary.

[Transitous](https://github.com/public-transport/transitous) is an additional source for public transport and certain transfers. Depending on the route, DB or Flix connections may also be included.

If no automatically usable transfer is available, Traviorel can provide a manual Google Maps link instead of inventing a connection.

## Accommodation

Traviorel can search for accommodation matching the journey. Check-in and check-out are derived from the calculated travel dates and requested stay duration.

The general accommodation search uses trvl. Depending on its sources, the hotel and room data may contain offers or providers such as **Booking.com, Expedia, Hotels.com, Agoda, Trip.com, Kayak, Trivago**, or direct hotel offers. Traviorel deliberately does not claim to query every one of these providers directly. It processes what the trvl hotel search actually returns.

**Booking.com is technically supported by the underlying hotel search, but is currently not a reliable source in practice.** Its WAF and bot protection may block automated requests. Such a failure is treated as a provider issue and must not terminate the entire accommodation search or journey plan.

Hotels, hostels, apartments, and resorts are distinct accommodation types. A normal hotel search starts at three stars by default. A hostel is not treated as a hotel merely because it has a star rating.

Prices are not guessed either. A total is shown only when it has been verified to cover the complete stay. Nightly prices or ambiguous headline prices remain labelled accordingly.

Google Hotels is additionally available as a manual cross-check.

## Traviorel plans, the provider books

Traviorel is **not a booking platform**.

It does not buy tickets, reserve hotel rooms, or process payment information. Traviorel searches, compares, and assembles a complete journey plan.

When a provider supplies a concrete offer link, the interface displays **“Check/book offer”**. If only a useful search link is available, it displays **“Open more offers”**.

Depending on the journey component, this may lead to Deutsche Bahn, the relevant travel provider, or a manual cross-check with Google Flights, Google Hotels, or Google Maps.

The actual booking always takes place with the provider. Externally supplied prices may change before the booking page is opened. Traviorel therefore does not guarantee a final price and never performs bookings itself.

## Developed for Tarnkappe.info

Traviorel was originally developed for [Tarnkappe.info](https://tarnkappe.info/).

It did not begin as a demo or theoretical journey planner. I needed a solution for assembling real journeys more quickly, including DB fares, split ticketing, and the alternatives alongside them.

That internal tool gradually became the public Traviorel project. Once it is built and works, it may as well be useful to others.

## Technical overview

```text
Browser UI -> FastAPI -> Traviorel orchestration
                         |-> DB backend (db-vendo-client, DBnav, curl_cffi)
                         |-> split-ticket and D-Ticket logic
                         |-> Transitous
                         |-> FlixTrain / FlixBus
                         |-> isolated trvl flight providers
                         |-> trvl hotels
                         `-> SQLite cache
```

Providers have separate hard time limits. A timeout is treated as a provider failure and does not automatically block the other journey components.

## Historical reliability of rail connections

Traviorel can additively enrich rail legs with 90 days of historical observations from
[`piebro/deutsche-bahn-data`](https://huggingface.co/datasets/piebro/deutsche-bahn-data).
The normal search, prices, and ranking remain independent of this enrichment.

The idea for historical reliability scoring was inspired in part by **Delay**. In our
tests, its approach proved unreliable because requests repeatedly failed. Traviorel
therefore implements the feature independently, including a local cache and a working
fallback path.

The required historical train data is filtered directly from
`piebro/deutsche-bahn-data` and stored locally. Train/month data that is already cached
does not have to be fetched again for subsequent searches. Historical enrichment is
isolated from the actual journey search. If Hugging Face is slow, unavailable, or the
history request times out, the journey itself remains available. Traviorel then cleanly
falls back to displaying the connection without a reliability value instead of failing
the entire search with a 502 error.

For a direct journey, the percentage is the empirically observed share of concrete run
instances that **were not explicitly cancelled and reached their destination no more
than ten minutes late**. Missing observations are never treated as cancellations.

For a transfer, the value is the empirical share of observed runs of the incoming train
whose arrival delay was no greater than the scheduled transfer time; explicit
cancellations count as missed connections. For multiple transfers, the overall value is
calculated as a clearly identified independence estimate based on the individual
probabilities. Individual values and sample sizes remain visible in the details.

The evaluation prioritises the train number, train type, concrete route segment, weekday,
and four-hour time window. If a subset is too small, it falls back within the same
concrete train/route history to weekday, time window, and finally all observations. No
value is invented without a reliable train number and EVA IDs.

DuckDB is used exclusively as an embedded in-memory Parquet engine. Only filtered
train/month Parquet files are persisted under `/var/lib/reisevergleich/history`;
metadata and completed statistics remain in the existing `cache.sqlite3`. Traviorel
creates neither a second database nor a full dataset mirror. Setting
`HISTORY_ENABLED=false` disables the layer completely.

Separately, Traviorel can store daily, JSON-safe observation snapshots in the
`snapshots` subdirectory. At most one atomically replaced file exists per identifier
and calendar day, and exactly 30 calendar days are retained by default. Corrupt files
are removed while reading; fields for tokens, passwords, cookies, authorization data,
or API keys are rejected before writing. The live search never writes snapshots and is
therefore neither delayed nor allowed to change connections, prices, or ranking. An
internal background task archives only already-computed history statistics instead. It
starts after 60 seconds by default and then runs once per day. Failures are logged in
isolation and the next cycle continues. Set `HISTORY_SNAPSHOT_SCHEDULER_ENABLED=false`
to disable it. Interval, initial delay, and retention are controlled by
`HISTORY_SNAPSHOT_INTERVAL_SECONDS`, `HISTORY_SNAPSHOT_INITIAL_DELAY_SECONDS`, and
`HISTORY_SNAPSHOT_RETENTION_DAYS`.

## Installation with Docker

Docker, the Docker Compose plugin, Git, and OpenSSL are required. The official installation path uses the published GHCR images and builds nothing locally:

```bash
git clone https://github.com/lesecuritae/Traviorel.git
cd Traviorel



docker compose config -q
docker compose pull
docker compose up -d --no-build
```

Then verify the installation:

```bash
docker compose ps
curl http://127.0.0.1:8791/api/health
```

The interface is available at <http://127.0.0.1:8791>.

`DB_CFFI_TOKEN` is optional; Traviorel securely generates and persists an internal token by default. The token is only a locally generated internal secret between `traviorel-app` and `traviorel-db-api`. It is not a user password or Traviorel login.

The published images are:

```text
ghcr.io/lesecuritae/traviorel-app:latest
ghcr.io/lesecuritae/traviorel-db-api:latest
```

Python, Go, Node.js, trvl, and db-vendo-client do not need to be built locally for a normal installation. `install.sh` remains available as an optional convenience, but it is not required:

```bash
./install.sh
```

### LAN access

By default, Traviorel listens only on the Docker host with `TRAVIOREL_BIND_HOST=127.0.0.1`. To allow access from the local network, set this in `.env`:

```env
TRAVIOREL_BIND_HOST=0.0.0.0
```

Then apply the change:

```bash
docker compose up -d --no-build
```

The interface is then available at `http://SERVER-IP:8791`. Binding to `0.0.0.0` makes the service listen on reachable networks; configure the firewall and any reverse proxy accordingly.

### Operation

```bash
# Status
docker compose ps

# Logs
docker compose logs -f app db-api

# Restart
docker compose restart

# Stop
docker compose down

# Start
docker compose up -d --no-build

# Health check
curl http://127.0.0.1:8791/api/health
```

### Update

```bash
git pull
docker compose pull
docker compose up -d --no-build
```

No recompilation is required.

### Persistence

Traviorel stores persistent state in the Docker volume `traviorel-state`, mounted at `/var/lib/reisevergleich` in the app container. `docker compose down` preserves the volume and its data. **Warning: `docker compose down -v` deletes the persistent volume and all stored Traviorel data.**

## Development and local build

This path is intended only for development and is not required for normal users:

```bash
docker compose build
docker compose up -d
```

trvl is pinned through `TRVL_REF`; Traviorel 0.3.0 uses `v1.21.4` by default.

## Unambiguous station selection

For rail and bus journeys Traviorel searches the existing DB station data, the international Transitous directory and the already loaded Flix GTFS in parallel. Results for the same stop are grouped by real coordinates, while the current search request retains each provider's native ID. DB, Transitous and Flix therefore no longer have to guess a selected stop from a similar free-text name; TRVL receives the same confirmed name for compatible provider paths.

Exact station names and unambiguous aliases are confirmed automatically. A region/country choice is shown only for genuine geographic ambiguity, such as Vienna in Austria versus Vienna in Virginia. A ground search cannot start before that ambiguity is resolved. Resolutions use the existing SQLite/WAL component cache for five minutes; no duplicate station database is maintained.

## Flexible price search

For ground journeys Traviorel can compare current connections and prices across 3, 7, or a custom period of up to 14 days starting at the selected departure date. Every day uses the same DB, Transitous, Flix and TRVL ground adapters as a regular search. The lowest evidenced daily price is highlighted; when a provider supplies timetable data only, the UI explicitly displays “Price unavailable”. Selecting a day loads its regular detailed journey while preserving the trip duration. Existing journey/provider caches are reused and no more than two day searches run concurrently.

## Mobile coverage along a journey

For ground journeys, Traviorel loads a separate coverage analysis for each visible connection after the journey response has rendered. Operator values for Telekom, Vodafone, Telefónica/O2 and, where evidenced, 1&1 are calculated from OpenCellID cells along the complete route geometry. Internal assignment uses current German MCC/MNC blocks. If spatial OpenCellID evidence is insufficient, Traviorel displays “Operator data unavailable” and does not estimate values. The operator-neutral BNetzA grid remains an internal supplementary calculation. Successful analyses are cached for seven days under a stable route hash.

OpenCellID cell locations are crowdsourced evidence, not a guarantee of reception or throughput inside a vehicle. Existing geometry and Flix GTFS shapes take precedence, followed by coordinated intermediate stops and finally a clearly labelled straight-line approximation. Coverage-analysis failures never change or delay journey results. Set `OPENCELLID_CSV_PATH` for a local database download or `OPENCELLID_API_KEY` for an authorised API account.

For API-credit-free offline operation, `OPENCELLID_CSV_PATH` expects a UTF-8 CSV with a header. Required fields are `radio`, `mcc`, `net` (or `mnc`), `area`/`lac`/`tac`, `cell`, `lon`, and `lat`; `range` is optional. The dataset is not embedded in the image because of its size and licence. Unknown MCC/MNC pairs are discarded.

Journey searches include connections departing up to 15 minutes before the requested time by default. Such departures are explicitly labelled as “x minutes before the requested time”. The window is configured centrally through `SEARCH_DEPARTURE_TOLERANCE_MINUTES`; the user's input itself remains unchanged.

Location resolution for DB, Transitous and Flix always tries the unchanged station name first. Controlled international spellings such as München/Muenchen/Munich, Köln/Cologne or Wien/Vienna are used only after an empty exact lookup. Airport aliases require explicit airport context (for example “Airport”, “Flughafen” or an IATA code); TRVL airport transfers retain the same separation.

## Quality assurance

```bash
bash scripts/check.sh
bash scripts/container-check.sh
```

The checks cover, among other things, Python, Node, and shell syntax; API and UI
contracts; hard time windows; airport identity; fare semantics; the Deutschlandticket;
split tickets; provider isolation; transfers; hotels; and flight chronology.

Live tests require reachable external providers and run separately from reproducible
regression tests.

## Roadmap

### Coming soon: native providers instead of trvl

The next major step is already planned: **trvl will gradually be removed from Traviorel entirely.**

Flight, accommodation, and other providers that are still connected through trvl today are planned to be queried directly by Traviorel. The existing Traviorel logic for provider isolation, timeouts, fallbacks, plausibility checks, price comparison, and caching will remain and connect directly to native provider modules.

An important building block for this is `curl_cffi`, which Traviorel already uses for difficult HTTP requests. It can reproduce browser TLS and HTTP fingerprints much more closely than conventional Python HTTP clients, giving Traviorel additional options for stable direct requests and provider-specific fallback paths where appropriate.

The goal is a Traviorel stack without an external trvl binary:

* native Traviorel flight providers
* native Traviorel providers for accommodation and additional travel components
* Traviorel-owned normalization and error handling
* Traviorel-owned caching and fallback strategies
* no remaining trvl build dependency

The goal is not to bypass protection mechanisms at any cost. If a provider does not expose an interface that can be used reliably, Traviorel should continue to report that transparently while allowing other providers to keep working.

**Coming soon.**

## Known limitations

Traviorel can only be as current as its external providers. Providers may change
interfaces, block requests, alter prices, or temporarily return no usable results.

Booking.com is currently an especially visible example because of its WAF and bot
protection.

Traviorel attempts to make such failures visible while allowing other providers to
continue. It does not invent missing prices or connections as substitutes.

The application is a journey planner and price comparison service, not a booking engine.
Always verify prices, fare conditions, and availability with the actual provider before
purchasing.

## Projects used

Traviorel would not be possible in this form without existing open-source projects and
external services.

- [BetterBahn/betterbahn](https://github.com/BetterBahn/betterbahn) – original motivation
  for integrated split ticketing; Traviorel uses no BetterBahn code.
- [MikkoParkkola/trvl](https://github.com/MikkoParkkola/trvl) – partial foundation for
  selected flight, hotel, and other provider functionality.
- [public-transport/db-vendo-client](https://github.com/public-transport/db-vendo-client)
  – technical foundation of the DB backend.
- [public-transport/transitous](https://github.com/public-transport/transitous) – public
  transport routing data and transfers.
Additional dependencies and their applicable licence terms are documented in
[THIRD_PARTY.md](THIRD_PARTY.md).

Traviorel is independent of Deutsche Bahn, BetterBahn, Flix, trvl, Transitous,
and all other queried or linked travel providers. Their respective owners retain all
trademarks and names.

## Licence

Traviorel code originating from this repository is available under the
[MIT License](LICENSE).

Third parties retain their own licences. Of particular importance is `trvl v1.21.4`,
which is licensed under the **PolyForm Noncommercial License 1.0.0**. Traviorel's MIT
licence does not override trvl's non-commercial restriction. Anyone wishing to use the
complete default stack commercially must review trvl's licence terms separately.

See [THIRD_PARTY.md](THIRD_PARTY.md) for details.

## Support

Traviorel is developed independently and remains free to use. Donations are necessary for continued development, infrastructure operation, and new features because server costs and development work are ongoing.

If the project helps you, your support helps fund its continued development.

**[Support Traviorel](https://tarnkappe.info/spenden/)**
