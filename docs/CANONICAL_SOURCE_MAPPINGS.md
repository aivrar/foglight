# Canonical Source Mappings

This document is the field-level contract for Foglight's Phase 3 core
normalizers. All adapters are side-effect free: a bounded response body enters,
validated `Observation` objects and payload-safe drift diagnostics leave. The
raw body is hashed but is not copied into SQLite.

## Shared rules

- CAP severity, urgency, and certainty are copied only when the source exposes
  those exact CAP fields. Other providers remain `Unknown`.
- Source event time remains `null` when a feed only provides publication or
  ingestion time.
- Coordinates are accepted only from structured GeoJSON or GeoRSS fields.
- Unknown fields are tolerated. Missing required fields produce diagnostics
  containing field names and bounded record identity only, never payload values.
- SWPC `time_tag` and GDACS API date fields omit a suffix in their published
  products. Their product clocks are UTC; the adapters append `Z` before the
  canonical RFC 3339 validation step. No date is synthesized when the supplied
  value does not match the published shape.

## Provider mappings

| Provider | Stable identity | Kind and lifecycle | Time mapping | Geometry | Metrics and provenance |
|---|---|---|---|---|---|
| USGS earthquakes | Feature `id` | `earthquake`; `status=deleted` becomes cancelled, otherwise active | `properties.time` → event; `updated` → source update | Feature GeoJSON unchanged | magnitude, depth, tsunami flag, USGS significance; detail URL from `properties.url` |
| NWS alerts | `properties.id`, then feature `id` | `weather_alert`; CAP `messageType` maps Alert/Update/Cancel | onset, effective, expires, and sent retain their distinct meanings | Feature Polygon/Point/null unchanged | CAP severity/urgency/certainty copied exactly; instruction, affected area, and sender retain property provenance |
| NHC storms | Storm `id` | `tropical_cyclone`, active while present in `activeStorms` | `lastUpdate` → effective/source update; event time remains null; advisory issuance remains a labeled metric | documented numeric longitude/latitude | intensity (kn), pressure (hPa), movement bearing/speed, classification; public-advisory URL |
| NOAA tsunami | Atom/CAP entry `id` | `tsunami`; explicit “cancel” language becomes cancelled | effective → effective; updated/sent → source update; no earthquake event time inferred | GeoRSS point only | warning-center identity and a bulletin-series relation candidate; entry link |
| GDACS | event type + event ID | event type maps to canonical hazard kind; `iscurrent=false` becomes ended | from/to/date-modified preserve separate roles | API GeoJSON unchanged | alert level, episode, exposure, nested severity data, affected ISO-2 countries, report URL |
| NASA EONET V3 | Event `id` | category maps to a known kind, otherwise `natural_event`; `closed` becomes ended | latest geometry date → event/update; closed → expiry | latest dated source geometry; history count remains explicit | category IDs, complete bounded source-ID list, magnitude value/unit, first usable source URL |
| Smithsonian GVP | RSS GUID, then link/title | `volcano`; explicitly a weekly report, not a direct eruption observation | publication → source update; event time remains null | null unless a future structured field supplies it | `weekly_activity_report` semantic and Smithsonian link |
| NOAA SWPC | K-index `time_tag` | `space_weather`; explicitly an observation product, not an outlook | UTC `time_tag` → event/update | null | Kp, running A-index, station count, `observation` product semantic |
| ReliefWeb RSS | GUID, then link/title | `humanitarian_report`; active publication | publication → source update; event time remains null | null | publisher/creator and humanitarian-report semantic; original report link |
| NOAA Aviation Weather Center | ICAO/type/series/start composite | `aviation_hazard`; validity window is current/future/expired without translating numeric source severity into CAP severity | validity start/end remain distinct; start is event/effective and source update | SIGMET GeoJSON unchanged | hazard, series, altitude, movement, raw advisory, and official SIGMET link |
| FEMA OpenFEMA | declaration record ID, then disaster/designated-area composite | `disaster_declaration`; administrative context with no inferred emergency semantics | declaration date is effective, not event onset; incident begin/end and refresh stay labeled | source state/county identifiers only; no fabricated geometry | disaster/declaration/incident types, programs, place and incident boundaries; FEMA disaster link |
| NOAA NDBC | stable station ID; item GUID retained as source-record metric | `marine_observation`; current station measurement with unknown CAP semantics | GUID observation time → event/source update; feed-generation `pubDate` is not treated as a station update | GeoRSS point | source units preserved for wind, wave, pressure, temperature and other labeled station fields; query-relative distance is excluded from canonical summary |
| NOAA CO-OPS | stable station ID; station/time retained as source-record metric | `water_level`; latest station measurement with unknown CAP semantics | offset-less documented GMT row time → event/source update | metadata station point | meters relative to requested MLLW, QA and data flags, sigma; anomaly only when the same payload contains an explicit matching prediction |
| NASA/JPL CNEOS | peak-brightness GMT date/time | `fireball`; completed low-frequency observation with no inferred emergency classification | documented offset-less GMT date → event; no publication/update time is invented | point only when all latitude/longitude magnitude and direction fields are present | radiated energy in 10^10 J, estimated impact energy in kt, optional peak-brightness altitude in km and reported entry velocity in km/s; signature v1.2 and the documented/live selected field sets are validated before positional indexing |

## Verified upstream contracts

- [USGS GeoJSON summary format](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php)
- [NWS alerts/CAP service](https://www.weather.gov/documentation/services-web-alerts)
- [NHC current-storm JSON reference](https://www.nhc.noaa.gov/productexamples/NHC_Tropical_Cyclone_Status_JSON_File_Reference.pdf)
- [U.S. Tsunami Warning Center message definitions](https://www.tsunami.gov/?page=message_definitions)
- [GDACS API quick start](https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v2.pdf)
- [NASA EONET V3 API](https://eonet.gsfc.nasa.gov/docs/v3)
- [NOAA SWPC product directory](https://services.swpc.noaa.gov/products/)
- [ReliefWeb API authentication change](https://apidoc.reliefweb.int/parameters)
- [Aviation Weather Data API](https://connect.aviationweather.gov/data/api/)
- [OpenFEMA Disaster Declarations Summaries](https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries)
- [NDBC RSS access](https://www.ndbc.noaa.gov/faq/rss_access.shtml)
- [CO-OPS Data Retrieval API](https://api.tidesandcurrents.noaa.gov/api/prod/)
- [CO-OPS response fields](https://api.tidesandcurrents.noaa.gov/api/prod/responseHelp.html)
- [NASA/JPL Fireball Data API](https://ssd-api.jpl.nasa.gov/doc/fireball.html)
- [NASA/JPL SSD API fair-use policy](https://ssd-api.jpl.nasa.gov/doc/index.php)

ReliefWeb's JSON API requires a pre-approved appname as of November 2025.
Foglight therefore keeps the no-key RSS path for the out-of-box experience and
does not silently add a credential requirement.
