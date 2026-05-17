# Features

Foglight is a desktop situation room for live public signals. It is designed to
be dense, local, and immediately useful after launching one Windows executable.

## Desktop App

- Single `Foglight.exe` release artifact.
- Native Windows desktop window through WebView2.
- Bundled Python runtime and local server.
- No installer, admin rights, WSL, Docker, Git, Node, or system Python needed
  for normal users.
- Runtime state under `%LOCALAPPDATA%\Foglight\`.
- Headless packaged mode for smoke testing with `FOGLIGHT_NO_BROWSER=1`.

## Dashboard Layout

- Top global status bar.
- Theater buttons for major regions.
- Large global map as the central visual surface.
- Left and right panel rails for live feeds and detail panes.
- Bottom strip for market and internet pulse panels.
- Settings pane for API keys, panels, RSS feeds, audio, and app shutdown.

## Map Layers

| Layer | Source | Notes |
|---|---|---|
| Base map | CARTO / OpenStreetMap | Dark map tiles and labels |
| Earthquakes | USGS | Recent quakes with magnitude styling |
| Weather alerts | NWS | US active alert polygons |
| Conflict hotspots | GDELT | Geographic conflict-oriented signals |
| Tropical cyclones | NOAA NHC | Active storm data |
| Natural events | NASA EONET | Open events such as wildfires and storms |
| Major disasters | GDACS | Red/orange alert awareness |
| ISS | Open Notify | Current ISS position |
| Flights | ADS-B.lol / optional OpenSky | Aircraft near map regions |
| Fires | Optional NASA FIRMS key | MODIS/VIIRS detections |
| Ships | Optional AISstream key | Live vessel stream |

## Live Data Panels

| Panel | What It Shows |
|---|---|
| Earthquakes | Recent USGS earthquake feed |
| Severe Weather | NWS active alerts |
| Conflict Watch | GDELT, UN, DW, France 24, and defense RSS stream |
| Major Hazards | Cyclones, volcanoes, significant quakes, GDACS alerts |
| Humanitarian Sitreps | ReliefWeb updates |
| Bitcoin Pulse | Fees, mempool, recent blocks, difficulty adjustment |
| Wikipedia Edits | Recent public Wikimedia EventStreams activity |
| GitHub Pulse | Public repo events |
| SEC EDGAR | Current filings |
| HN + Reddit | Top internet discussion items |
| Live TV | YouTube live news embeds and fallback links |

## Settings

- Show/hide optional panels.
- Save market/watchlist symbols.
- Add or remove RSS feeds.
- Save optional API keys locally.
- Choose default Live TV channel.
- Toggle ambient audio cues.
- Clear pins and keys.
- Shut down the local server and app.

## Optional API Unlocks

| API | Unlocks |
|---|---|
| AISstream | Ship positions |
| NASA FIRMS | Fire detections |
| OpenSky | Authenticated aircraft data |
| OpenWeatherMap | Global weather extension |
| FRED | Macro indicators |
| Finnhub | Market news, earnings, indices |

## Privacy And Local Behavior

- No hosted Foglight backend.
- Local server binds to `127.0.0.1`.
- API keys stay on disk in the local state directory.
- The settings endpoint masks key values before returning them to the UI.
- RSS proxy blocks localhost and private-network destinations.

## Release Experience

The intended public release flow is simple:

1. User downloads `Foglight.exe` from GitHub Releases.
2. User runs the exe.
3. Foglight opens as a Windows desktop app.
4. Optional keys can be pasted in Settings.
5. Runtime state remains local to the user's Windows profile.
