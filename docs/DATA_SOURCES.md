# Data Sources

Foglight is a local viewer and aggregator. It does not own the data it displays.
Each provider controls its own license, terms, attribution requirements, and
rate limits.

## No-Key Sources

| Source | Endpoint Family | Used By |
|---|---|---|
| USGS Earthquake Hazards | `earthquake.usgs.gov` GeoJSON | Earthquakes panel and map overlay |
| Smithsonian GVP | `volcano.si.edu` RSS | Weekly volcano feed |
| NWS | `api.weather.gov` | Severe weather alerts |
| NOAA NHC | `nhc.noaa.gov` | Tropical cyclones |
| NOAA SWPC | `services.swpc.noaa.gov` | Space weather |
| tsunami.gov | PTWC / NTWC Atom feeds | Tsunami notices |
| NOAA Aviation Weather Center | `aviationweather.gov/api/data` SIGMET GeoJSON | Default aviation-hazard incidents |
| NOAA NDBC | Nearby-observation GeoRSS | At most six deduplicated marine contexts, every five minutes |
| NOAA CO-OPS | Tides and Currents `date=latest` Data API | At most six deduplicated coastal MLLW stations, every five minutes |
| NASA/JPL CNEOS | SSD Fireball Data API v1.2 | Most recent 20 peak-brightness records, one request every six hours |
| NASA EONET | `eonet.gsfc.nasa.gov` | Natural event overlay |
| GDACS | `gdacs.org/gdacsapi` GeoJSON | Disaster alerts |
| FEMA OpenFEMA | `fema.gov/api/open/v1` | Official disaster-declaration context |
| ReliefWeb | `reliefweb.int` RSS | Humanitarian sitreps |
| UN / DW / France 24 RSS | Public conflict-oriented feeds | Conflict watch and hotspots |
| mempool.space | `mempool.space/api` | Bitcoin pulse |
| CoinPaprika | `api.coinpaprika.com` | Crypto tickers |
| Frankfurter | `frankfurter.app` | Forex rates |
| Yahoo Finance chart data | `query1.finance.yahoo.com` | Commodity futures ticker |
| GitHub | `api.github.com/events` | GitHub Pulse |
| SEC EDGAR | `sec.gov` Atom | SEC filings |
| Wikimedia | `stream.wikimedia.org` | Recent edit stream |
| Hacker News | Firebase HN API | HN trends |
| Reddit | `reddit.com/r/popular/.rss` | Reddit trends |
| Open Notify | `api.open-notify.org` | ISS position |
| ADS-B.lol | `api.adsb.lol` | Experimental community aircraft endpoint; off by default |
| Open-Meteo | `api.open-meteo.com` | Disabled-by-default click-to-inspect compatibility path; not used by Overview |
| RSS feeds | User-configured HTTP(S) RSS | News ticker |
| YouTube | Live channel embeds | Live TV panel |

## Optional-Key Sources

| Source | Key Name In App | Used By |
|---|---|---|
| NASA FIRMS | `nasa_firms` | MODIS/VIIRS fires |

## RSS Proxy Rules

Foglight lets the UI request configured RSS feeds through the local server. The
proxy rejects localhost, private-network destinations, credentials in URLs,
and nonstandard HTTP ports. Redirect destinations are revalidated, response
sizes are capped, and cache growth is bounded.

## Attribution

Credits and attribution notes live in `CREDITS.md`. Add new providers there
when new feeds are added.

The machine-readable source of truth is
`config/provider_registry.v1.json`. It records tier, cadence, authentication,
attribution, contact requirements, and the release decision for every current
adapter. `config/data_taxonomy.v1.json` records retention and UI lanes.

## Published Polling And Response Limits

This is the public release contract from the registry. Cadence is the earliest
normal repeat interval, not a promise that a provider will answer on schedule.
The scheduler permits one in-flight job per provider, uses at most four workers
overall, honors `Retry-After`, adds jitter, persists validators, and applies
exponential failure backoff plus a circuit break. Unless a row says otherwise,
the timeout is 15 seconds and the body cap is 2 MiB.

| Provider ID | Poll cadence | Body cap | Release label |
|---|---:|---:|---|
| `usgs_earthquakes` | 1 min | 2 MiB | required authoritative |
| `nws_alerts` | 2 min | 5 MiB | required authoritative |
| `nhc_storms` | 10 min | 2 MiB | required authoritative |
| `noaa_tsunami` | 5 min | 2 MiB | required authoritative |
| `noaa_aviation_weather` | 5 min | 2 MiB | required authoritative |
| `noaa_space_weather` | 10 min | 2 MiB | primary optional |
| `nasa_eonet` | 10 min | 2 MiB | required authoritative |
| `smithsonian_volcano` | 1 hour | 2 MiB | primary optional |
| `gdacs` | 5 min | 2 MiB | primary optional |
| `openfema_declarations` | 30 min | 2 MiB | primary administrative context |
| `ndbc_observations` | 5 min | 600 KiB | contextual; at most 6 URLs |
| `noaa_coops_water_levels` | 5 min | 256 KiB | contextual; at most 6 URLs |
| `nasa_jpl_fireballs` | 6 hours | 128 KiB | low-frequency observation |
| `reliefweb_rss` | 5 min | 2 MiB | primary optional |
| `conflict_rss` | 4 min | 2 MiB | supplemental media context |
| `defense_rss` | 10 min | 2 MiB | supplemental media context |
| `nasa_firms` | 15 min | 2 MiB | optional user key |
| `adsb_lol` | 30 sec | 2 MiB | experimental, off by default |
| `open_notify_iss` | 10 sec | 2 MiB | supplemental observation |
| `open_meteo` | 10 min | 2 MiB | conditional, disabled by default |
| `mempool_space` | 30 sec | 2 MiB | supplemental market signal |
| `coinpaprika` | 2 min | 2 MiB | supplemental market signal |
| `frankfurter` | 1 hour | 2 MiB | supplemental market signal |
| `yahoo_finance` | 5 min | 2 MiB | compatibility; disabled by default |
| `github_events` | 45 sec | 2 MiB | optional public activity |
| `sec_edgar` | 3 min | 2 MiB | optional official filings |
| `hacker_news` | 3 min | 2 MiB | optional community signal |
| `reddit_popular` | 3 min | 2 MiB | optional RSS signal |
| `wikimedia_recentchange` | 10 sec | 512 KiB/event | optional bounded stream |
| `rss_proxy` | 4 min | 2 MiB | optional user-configured feed |

The local incident API returns at most 200 items per request. The database is
bounded to 100,000 observations and 256 MiB, the response cache to 1,000
entries and 128 MiB, and individual cache/upstream entries to 10 MiB at the
hard boundary. Normal scheduled providers use the smaller caps above. Category
retention ranges from 1 to 365 days as published in
`config/data_taxonomy.v1.json`.

## Freshness Language And Limitations

- **Live** means the latest scheduled attempt succeeded; it does not mean the
  underlying real-world event occurred at request time.
- **Cached** means a provider validator or local retained result was reused.
- **Stale** means old data is shown because the newest attempt failed.
- **Offline** means Foglight is serving retained local history without a
  current provider success. Source age remains visible.
- **Error** and **partial** mean one or more providers failed independently;
  they do not invalidate successful sources.
- Forecast, warning, observation, declaration, media, community, and market
  signals remain visibly distinct. Foglight does not predict events, confirm
  media claims, replace official warnings, or infer disaster onset from an
  administrative declaration.

The opt-in `scripts/check_live_sources.py --confirm-live --normalize-core`
diagnostic also runs the bounded core normalizers and reports missing/unknown
field names without printing response bodies. It is intentionally not a CI
gate because upstream availability is outside Foglight's control.

## Conditional Sources

Open-Meteo and Yahoo Finance are not mandatory core sources. Their current V1
adapters remain compatibility paths, but both routes are disabled by default
and neither is an approved V2 source.
Open-Meteo's free API is non-commercial only; the route and map interaction are
off unless a developer explicitly sets `FOGLIGHT_OPEN_METEO_ENABLED=1` after
reviewing their intended use. Yahoo Finance likewise requires an explicit
developer `FOGLIGHT_YAHOO_FINANCE_ENABLED=1` after terms review. GDELT is not
registered. Foglight also does not
use retiring `waterservices.usgs.gov` endpoints; a future water integration
must target the modern USGS Water Data OGC API and re-evaluate its API-key
threshold.

## Map Transition

Both Standard and V2 use the bundled Natural Earth base map and local Leaflet
runtime. Hosted OpenStreetMap tiles are off by default, explicitly user-enabled,
and removed after repeated failures without hiding the offline base. Foglight
does not bulk-download or prefetch public tile services. Provenance and exact
checksums are recorded in [MAP_ASSETS.md](MAP_ASSETS.md).
