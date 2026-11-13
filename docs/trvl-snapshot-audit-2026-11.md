# trvl snapshot review, 2026-11-13

Baseline: releasev1.27.0, commit4f6b5da14874b34260bdfa2c114fb9b4800223c6. Reviewed target: b20e2265a63a4d229abff7b20f5649f2e9f948dc, 38 commits later. Latest published stable release remains1.27.0. This integration deliberately uses version1.27.0+git.b20e226.

## Changes relevant to Traviorel

- AF/KLM recommendation mapping now prefers flight-product price, with summed connection prices as fallback, preserving currency. Upstream adds a229.06 round-trip regression; Traviorel must retain that total.
- Wizz default API moves from29.20.0 to29.24.0.
- Shared tls-client1.18.0, fhttp0.7.3, chromedp0.23.0 and cdproto0.159.2; upstream ports browser actions/subscriptions. These are provider transport changes, not new hotel-price features.
- go.mod requires Go1.27.3; using the old1.26 builder would not meet that requirement. A verified official1.27.3-bookworm multi-platform image is digest-pinned; automatic toolchain downloads are disabled for build/tests.
- Only internal/hotels/eco_probe_test.go changes under internal/hotels/: transient lookup refusals are classified in the probe. Hotel application models, CLI hotel arguments and hotel-price mapping have no direct upstream diff in this comparison. Shared provider transport changes still warrant hotel tests.
- LICENSE removes the commercial-contact footer; the required notice and PolyForm Noncommercial text remain. The exact source license continues to be copied into the full image; Lite excludes trvl.

## Verification approach

Build validates the fetched commit. AF/KLM, general flights, hotels, providers and ground upstream packages run with network disabled and no live credentials, before compilation. Existing binary version/CLI contracts remain. Traviorel regressions cover corrected flight total and preserve hotel nightly/stay distinctions. Provider live refusals are reported separately from deterministic test results.

Sources: [upstream comparison](https://github.com/MikkoParkkola/trvl/compare/v1.27.0...b20e2265a63a4d229abff7b20f5649f2e9f948dc), [AF/KLM fix](https://github.com/MikkoParkkola/trvl/pull/718), [Go update](https://github.com/MikkoParkkola/trvl/pull/747).
