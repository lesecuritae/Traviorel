# Dependency and DB compatibility audit, 2026-11-13

## DB regression

A bounded comparison used the same station query, journey parameters and network path. The transport was isolated from the running application and used disposable credentials.

| curl_cffi | Browser profile | Location lookup | Journey and fare refresh |
| --- | --- | --- | --- |
| 0.16.1 | Firefox 147 | 452 / OPS_BLOCKED | 452 / OPS_BLOCKED |
| 0.17.0 | Firefox 147 | 452 / OPS_BLOCKED | 452 / OPS_BLOCKED |
| 0.17.0 | Firefox 155 | 200 | Two journeys with prices; fare refresh returned 200 |

Updating the package alone does not update an explicitly configured profile pool. The default now selects Firefox 155, supported by the pinned 0.17.0 release. The rebuilt full application image was checked again without a profile override and returned the same successful result. These are point-in-time availability checks, not a guarantee of upstream access. Authentication, allowed hosts, redirect validation, session reuse and request bounds are unchanged.

## Version check

Official PyPI metadata, npm registry metadata and upstream Git tags were checked on the audit date. “Tested” is the Python 3.13 dependency resolution used for the complete regression suite; the application's existing version constraints are retained except for the curl_cffi pin.

| Dependency | Previous running app | Tested / retained | Latest stable checked |
| --- | --- | --- | --- |
| curl_cffi | 0.16.1 | 0.17.0 | 0.17.0 |
| FastAPI | 0.142.2 | 0.162.5 | 0.162.5 |
| HTTPX | 0.28.1 | 0.28.1 | 0.28.1 |
| Pydantic | 2.13.5 | 2.14.3 | 2.14.3 |
| Uvicorn | 0.54.0 | 0.54.0 | 0.54.0 |
| DuckDB | 1.5.6 | 1.5.6 | 2.0.0 |
| MCP | 2.2.0 | 2.3.0 | 2.3.0 |
| trvl | 1.24.0 | 1.27.0 | 1.27.0 |
| db-vendo-client | c228bff in updated backend | c228bff / v6.11.3 | c228bff / v6.11.3 |
| qs (explicit backend override) | 6.15.3 pin | 6.15.3 pin retained | 6.17.1 |
| uuid (explicit backend override) | 11.1.1 pin | 11.1.1 pin retained | 14.0.3; latest 11.x is 11.1.1 |

DuckDB 2.0 remains outside the existing `<2` contract. The separate qs update and uuid major upgrade were identified, not silently included in the DB transport fix. This version inventory is not a complete vulnerability audit or a lockfile for transitive dependencies.

## Validation

- `bash scripts/check.sh`: passed under Python 3.13, with current allowed dependencies.
- `python -m pytest -q`: 169 passed, no failures or skips, including the subsequent MCP time, transport-selection and station-identity regressions.
- Before the fix, the new regression module had four failures and three passes. The final module has eight passing tests, including transparent reporting of an upstream block without replay.
- Targeted browser-profile and security regressions: 35 passed.
- The full application Docker build passed its trvl version and seven CLI/help contracts, with trvl pinned to `4f6b5da14874b34260bdfa2c114fb9b4800223c6`.
- JavaScript syntax checks passed on the host. Tests run without network access after the official DuckDB httpfs extension was installed into the disposable test image.

An initial Python 3.14 host run stops in the existing static-name checker on the interpreter-generated `__conditional_annotations__` name. This audit does not claim a green Python 3.14 suite; the production and CI runtime tested here is Python 3.13.

Sources: [curl_cffi release](https://github.com/lexiforest/curl_cffi/releases/tag/v0.17.0), [trvl release](https://github.com/MikkoParkkola/trvl/releases/tag/v1.27.0), [db-vendo-client release](https://github.com/public-transport/db-vendo-client/releases/tag/v6.11.3), [PyPI](https://pypi.org/), [npm registry](https://registry.npmjs.org/).
