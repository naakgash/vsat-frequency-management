# Vendored front-end assets

Specification section 19.4 rules out CDN-only dependencies, so every front-end asset is
committed here and served by nginx from `STATIC_ROOT`. No template may reference an
external host; `tests/ui/test_no_external_assets.py` enforces that.

| Asset | Version | Source |
|---|---|---|
| Bootstrap (CSS + bundled JS) | 5.3.3 | `https://registry.npmjs.org/bootstrap/-/bootstrap-5.3.3.tgz` |
| HTMX | 2.0.4 | `https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.4.tgz` |

Upstream licences are kept alongside each asset.

ECharts is not vendored yet; it arrives with the spectrum view in slice S9.

## Refreshing an asset

`make vendor` re-fetches these from the npm registry and unpacks the `dist/` files.
Bump the version there and in this table together, and commit the result — the assets
are part of the repository, not a build-time download.
