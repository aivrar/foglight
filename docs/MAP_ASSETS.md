# Bundled Map Assets

Foglight's core map has no runtime tile, API-key, account, or CDN dependency.
The reviewed Leaflet runtime and a small world boundary dataset ship inside the
application. Optional OpenStreetMap tiles are off by default and the bundled
base remains visible if those requests fail.

## Natural Earth world base

| Field | Value |
|---|---|
| Dataset | Natural Earth Admin 0 Countries, 1:110m cultural vectors |
| Upstream release | natural-earth-vector `v5.1.1` |
| Pinned source | `ne_110m_admin_0_countries.geojson` |
| Source SHA-256 | `6866c877d39cba9c357620878839b336d569f8c662d3cfab4cb1dbe2d39c977f` |
| Bundled output | `web/assets/natural-earth-110m-countries.v5.1.1.geojson` |
| Output SHA-256 | `b853e8ab6412d655dbe2fe8719d7cfde24e266db347eeb694b4df0f627a2fdb8` |
| Output size | 202,773 bytes; 177 features |
| Terms | Public domain |

Primary references: [Natural Earth 1:110m countries](https://www.naturalearthdata.com/downloads/110m-cultural-vectors/110m-admin-0-countries/),
[Natural Earth terms](https://www.naturalearthdata.com/about/terms-of-use/), and
the [pinned upstream repository release](https://github.com/nvkelso/natural-earth-vector/tree/v5.1.1).

The upstream dataset is already Natural Earth's small-scale 1:110m product.
Foglight's deterministic build does not invent new boundaries or apply an
additional topology simplifier. `scripts/build_map_assets.py` verifies the
pinned source checksum, accepts only Polygon/MultiPolygon WGS84 geometry,
quantizes coordinates to 0.001 degrees, strips unused attributes, retains the
country name/codes/continent, sorts features, and emits stable minified JSON.
This reduced output is 24.2% of the 838,726-byte source.

Rebuild or verify it with a separately downloaded pinned source:

```powershell
python scripts/build_map_assets.py path\to\ne_110m_admin_0_countries.geojson web\assets\natural-earth-110m-countries.v5.1.1.geojson
python scripts/build_map_assets.py path\to\ne_110m_admin_0_countries.geojson web\assets\natural-earth-110m-countries.v5.1.1.geojson --check
```

The checksum gate deliberately rejects a silently changed download.

## Leaflet runtime

Foglight vendors the unmodified Leaflet 1.9.4 distribution from the pinned
`leaflet@1.9.4` npm package. Leaflet is BSD-2-Clause licensed; its license is
included at `web/vendor/leaflet/LICENSE`. See the [official Leaflet download
page](https://leafletjs.com/download.html) and [1.9.4 release](https://github.com/Leaflet/Leaflet/releases/tag/v1.9.4).

| Vendored file | SHA-256 |
|---|---|
| `leaflet.js` | `db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a` |
| `leaflet.css` | `a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6` |
| `LICENSE` | `53e8dc25862014e4324741ca18fbe3611e11d42ef69f59f86ea8c5389647d4cb` |

The marker and layer-control images from that same distribution remain in
`web/vendor/leaflet/images/` so the upstream CSS is complete.

## Optional detailed tiles

The user may explicitly enable OpenStreetMap standard raster tiles for the
current session. Foglight does not prefetch, cache for offline redistribution,
or enable them automatically. Attribution stays visible. After repeated tile
errors the optional layer is removed, its toggle is reset, and the local
Natural Earth base and coordinate grid remain usable. The public tile service
has its own [tile usage policy](https://operations.osmfoundation.org/policies/tiles/).
