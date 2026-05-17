# Data Sources

Foglight is a local viewer and aggregator. It does not own the data it displays.
Each provider controls its own license, terms, attribution requirements, and
rate limits.

## No-Key Sources

| Source | Endpoint Family | Used By |
|---|---|---|
| USGS Earthquake Hazards | `earthquake.usgs.gov` GeoJSON | Earthquakes panel and map overlay |
| USGS Volcano Hazards | `volcanoes.usgs.gov` | Volcano signals |
| Smithsonian GVP | `volcano.si.edu` RSS | Weekly volcano feed |
| NWS | `api.weather.gov` | Severe weather alerts |
| NOAA NHC | `nhc.noaa.gov` | Tropical cyclones |
| NOAA SWPC | `services.swpc.noaa.gov` | Space weather |
| tsunami.gov | PTWC / NTWC Atom feeds | Tsunami notices |
| NASA EONET | `eonet.gsfc.nasa.gov` | Natural event overlay |
| GDACS | `gdacs.org` RSS | Disaster alerts |
| ReliefWeb | `reliefweb.int` RSS | Humanitarian sitreps |
| GDELT | `api.gdeltproject.org` | Conflict watch and hotspots |
| mempool.space | `mempool.space/api` | Bitcoin pulse |
| CoinPaprika | `api.coinpaprika.com` | Crypto tickers |
| Frankfurter | `frankfurter.app` | Forex rates |
| Stooq | `stooq.com` CSV | Commodities and watchlist data |
| GitHub | `api.github.com/events` | GitHub Pulse |
| SEC EDGAR | `sec.gov` Atom | SEC filings |
| Wikimedia | `stream.wikimedia.org` | Recent edit stream |
| Hacker News | Firebase HN API | HN trends |
| Reddit | `reddit.com/r/popular.json` | Reddit trends |
| Open Notify | `api.open-notify.org` | ISS position |
| ADS-B.lol | `api.adsb.lol` | Community aircraft data |
| RSS feeds | User-configured HTTP(S) RSS | News ticker |
| YouTube | Live channel embeds | Live TV panel |

## Optional-Key Sources

| Source | Key Name In App | Used By |
|---|---|---|
| AISstream | `aisstream` | Ship positions |
| NASA FIRMS | `nasa_firms` | MODIS/VIIRS fires |
| OpenSky Network | `opensky_id`, `opensky_secret` | Authenticated aircraft data |
| OpenWeatherMap | `openweathermap` | Global weather extension |
| FRED | `fred` | Macro indicators |
| Finnhub | `finnhub` | Market news, indices, earnings |

## RSS Proxy Rules

Foglight lets the UI request configured RSS feeds through the local server. The
proxy intentionally rejects localhost and private-network destinations to avoid
turning the desktop app into a local network scanner.

## Attribution

Credits and attribution notes live in `CREDITS.md`. Add new providers there
when new feeds are added.
