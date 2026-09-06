# Coverage data

`mobile_broadband_2025_12.fwcov` is a compact, deterministic derivative of
`202601_MobilfunkMonitoring.csv`, published by the Bundesnetzagentur as
processable mobile-monitoring data for December 2025.

The source CSV is licensed under **Datenlizenz Deutschland – Namensnennung –
Version 2.0**. Required attribution: **© Bundesnetzagentur**. The cell grid is
based on **© GeoBasis-DE / BKG (2025)**.

Source download:
https://data.bundesnetzagentur.de/Bundesnetzagentur/GIGA/DE/MobilfunkMonitoring/2512/202601_MobilfunkMonitoring.zip

SHA-256 source ZIP:
`f5770a3b8a84d0345f8016e4aa26617f9dea277c75297a6a5501e50f40f0406a`

SHA-256 generated raster:
`25330fedbd820a4ee6064aaeb2c4dabf621f10ab6b9df4feaad5c0a8e978b0c6`

Rebuild:

```sh
python -m reisevergleich.coverage.build_dataset \
  202601_MobilfunkMonitoring.zip \
  reisevergleich/coverage/data/mobile_broadband_2025_12.fwcov
```

The public data identifies only the number of networks per technology, not the
operator identities for each grid cell. Traviorel consequently reports route
shares with at least one, two, or three broadband networks (the latter also
includes cells with four networks) and does not infer operator-specific values.
