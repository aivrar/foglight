# Credits

Foglight depends on public data, open-source libraries, and platform tools. This
file tracks credit clearly so the repository does not hide where value comes
from.

## Core Platform

| Project or Service | Role |
|---|---|
| Python | Bundled runtime for the local server and native launcher |
| PyInstaller | Builds the one-file Windows executable |
| pywebview | Native desktop WebView shell |
| pythonnet / clr-loader | Windows WebView bridge support used by pywebview |
| Microsoft Edge WebView2 Runtime | Embedded desktop browser runtime |
| Pillow | Generates the application icon during the build |

## Frontend And Map Stack

| Project or Service | Role |
|---|---|
| Leaflet | Interactive map framework |
| OpenStreetMap contributors | Base map data attribution |
| CARTO | Dark map tile styling used by the dashboard |
| YouTube | Live channel embeds and external live-stream fallback links |

## Public Live Data Sources

| Source | Used For |
|---|---|
| USGS Earthquake Hazards Program | Earthquake GeoJSON feed |
| USGS Volcano Hazards / Smithsonian GVP | Volcano status and weekly volcano reports |
| National Weather Service | Active US weather alerts |
| NOAA National Hurricane Center | Tropical cyclone feed |
| NOAA Space Weather Prediction Center | Planetary K-index / space weather |
| tsunami.gov | Pacific and National Tsunami Warning Center Atom feeds |
| NASA EONET | Open natural event feed |
| NASA FIRMS | Optional MODIS/VIIRS fire detections |
| GDACS | Global disaster alert RSS |
| ReliefWeb | Humanitarian update RSS |
| GDELT Project | Conflict and world event signals |
| UN News | Peace and security RSS |
| DW | World news RSS |
| France 24 | News RSS |
| BBC | World news RSS |
| NPR | World news RSS |
| Al Jazeera | News RSS |
| CISA | Cybersecurity advisory RSS |
| Defense News | Defense RSS |
| Stars and Stripes | Defense/news RSS when available |
| War on the Rocks | Defense analysis RSS |
| mempool.space | Bitcoin fees, mempool, blocks, difficulty |
| CoinPaprika | Crypto market tickers |
| Frankfurter | Foreign exchange rates |
| Stooq | Commodities and market CSV data |
| GitHub Events API | Public developer activity |
| SEC EDGAR | Current filing Atom feed |
| Wikimedia EventStreams | Recent Wikipedia edit stream |
| Hacker News Firebase API | Top story metadata |
| Reddit JSON | Popular Reddit items |
| Open Notify | ISS location |
| ADS-B.lol | Community aircraft data |
| AISstream | Optional ship position stream |
| OpenSky Network | Optional authenticated aircraft data |
| OpenWeatherMap | Optional global weather data |
| FRED | Optional macroeconomic indicators |
| Finnhub | Optional market news and watchlist extensions |

## Build And QA Assistance

OpenAI Codex is listed as a code and documentation co-contributor for the
Windows desktop packaging, release preparation, repository documentation, and
QA smoke-test workflow. The app itself remains local-first and does not require
a hosted AI service to run.

## Terms

Each third-party library, public API, RSS feed, video platform, and map tile
provider is governed by its own license, attribution requirement, usage policy,
and rate limit. Foglight does not grant rights to third-party data or content.
