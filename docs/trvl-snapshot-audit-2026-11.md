# trvl snapshot review, 2026-11-13

Baseline: release v1.27.0, commit 4f6b5da14874b34260bdfa2c114fb9b4800223c6. Reviewed target: b20e2265a63a4d229abff7b20f5649f2e9f948dc, 38 commits later. Latest published stable release remains 1.27.0. This integration deliberately uses version 1.27.0+git.b20e226.

## Changes relevant to Traviorel

- AF/KLM recommendation mapping now prefers flight-product price, with summed connection prices as fallback, preserving currency. Upstream adds a 229.06 round-trip regression; Traviorel must retain that total.
- Wizz default API moves from 29.20.0 to 29.24.0.
- Shared tls-client 1.18.0, fhttp 0.7.3, chromedp 0.23.0 and cdproto 0.159.2; upstream ports browser actions/subscriptions. These are provider transport changes, not new hotel-price features.
- go.mod requires Go 1.27.3; using the old 1.26 builder would not meet that requirement. A verified official 1.27.3-bookworm multi-platform image is digest-pinned; automatic toolchain downloads are disabled for build/tests.
- Only internal/hotels/eco_probe_test.go changes under internal/hotels/: transient lookup refusals are classified in the probe. Hotel application models, CLI hotel arguments and hotel-price mapping have no direct upstream diff in this comparison. Shared provider transport changes still warrant hotel tests.
- LICENSE removes the commercial-contact footer; the required notice and PolyForm Noncommercial text remain. The exact source license continues to be copied into the full image; Lite excludes trvl.

## Verification approach

Build validates the fetched commit. AF/KLM, general flights, hotels, providers and ground upstream packages run with network disabled and no live credentials, before compilation. Existing binary version/CLI contracts remain. Traviorel regressions cover corrected flight total and preserve hotel nightly/stay distinctions. Provider live refusals are reported separately from deterministic test results.

Sources: [upstream comparison](https://github.com/MikkoParkkola/trvl/compare/v1.27.0...b20e2265a63a4d229abff7b20f5649f2e9f948dc), [AF/KLM fix](https://github.com/MikkoParkkola/trvl/pull/718), [Go update](https://github.com/MikkoParkkola/trvl/pull/747).

## Deterministic results

- Python 3.13: check.sh PASS and 172 pytest PASS, 0 failures, 0 skips.
- Full app, Lite and DB-API Docker builds completed from runtime commit d8bc3f20f68219197e45602208cfc0a0f7b7634c.
- All five selected upstream Go packages passed offline: AF/KLM, general flights, hotels, providers and ground. Upstream's optional live probes were not enabled; this is not proof of live-provider availability.
- First offline run had 12 provider fixture failures because fake-browser flows still resolve example.com through the real URL safety guard. A read-only build-context hosts fixture fixes that test dependency without disabling the guard or granting network access. It is mounted only for that RUN step, not copied to the runtime. The rejected direct-write attempt against BuildKit's read-only hosts file is not part of the final build.
- The exact snapshot binary version and CLI contracts are checked during compilation.

## Isolated live acceptance

The compiled snapshot reports the exact requested version/commit. Native MCP handshake, clock, correct bus/rail stop selection, opposite S-Bahn directions, separate co-located bus/rail groups, another city and optional destination passed. Lite reports trvl disabled and does not contain the build-only hosts mapping.

Default hotel adapter: 3 results with 3 verified stay totals. Forced trvl hotel fallback first exceeded 35s/20s. A fresh isolated 0.3.3 control returned 3 verified results in 26.8s; a fresh0.3.4 control then returned 3 verified results in 29.0s. No persistent upgrade-specific failure was reproduced. The initial timeout is retained as a failed live attempt, not rewritten as success. New CLI warnings include Google 429/cooldown and Booking's unavailable browser; other sources still delivered results. No cooldown was bypassed, no production browser/DNS/route was changed, and no paid provider was configured. These checks are point-in-time observations, not complete provider coverage or independent booking-price verification.
