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

- Feature-gated incident Overview with explainable priority, category filters,
  source freshness, change summaries, and a non-map incident index.
- Accessible incident drawer with current facts, reported metrics, related
  incidents, source health, safe evidence links, and semantic provenance labels.
- Immutable 1-hour, 6-hour, 24-hour, and 7-day revision timelines with
  non-destructive preview, deterministic copy summaries, and printable
  provenance briefings.
- Overview, Standard, and Command display modes with a persisted local choice.
- Offline incident map with deterministic clustering, category synchronization,
  keyboard cluster activation, map/list selection, and coordinate-based pins.
- Top global status bar.
- Theater buttons for major regions.
- Large global map as the central visual surface.
- Left and right panel rails for live feeds and detail panes.
- Bottom strip for market and internet pulse panels.
- Settings pane for API keys, panels, RSS feeds, audio, and app shutdown.

## Map Layers

| Layer | Source | Notes |
|---|---|---|
| Base map | Bundled Natural Earth 1:110m | Offline country boundaries and coordinate grid |
| Optional detail | OpenStreetMap contributors | User-enabled tiles with visible attribution and failure fallback |
| Earthquakes | USGS | Recent quakes with magnitude styling |
| Weather alerts | NWS | US active alert polygons |
| Conflict hotspots | Public conflict RSS aggregation | Geographic conflict-oriented signals |
| Tropical cyclones | NOAA NHC | Active storm data |
| Natural events | NASA EONET | Open events such as wildfires and storms |
| Major disasters | GDACS | Red/orange alert awareness |
| ISS | Open Notify | Current ISS position |
| Aviation hazards | NOAA Aviation Weather Center | Default worldwide SIGMET advisory incidents |
| Disaster declarations | FEMA OpenFEMA | Official administrative context, not event onset |
| Marine context | NOAA NDBC and CO-OPS | Bounded nearby observations and station water levels |
| Space observations | NASA/JPL CNEOS | Low-frequency fireball energy records with optional peak-brightness location |
| Flights | ADS-B.lol | Experimental aircraft endpoint; off by default |
| Fires | Optional NASA FIRMS key | MODIS/VIIRS detections |

## Live Data Panels

| Panel | What It Shows |
|---|---|
| Earthquakes | Recent USGS earthquake feed |
| Severe Weather | NWS active alerts |
| Conflict Watch | UN, DW, France 24, and defense RSS stream |
| Major Hazards | Cyclones, volcanoes, tsunami notices, significant quakes, GDACS alerts |
| Humanitarian Sitreps | ReliefWeb updates |
| Bitcoin Pulse | Fees, mempool, recent blocks, difficulty adjustment |
| Markets | Keyless crypto/forex; commodity compatibility is disabled pending explicit terms review |
| Wikipedia Edits | Recent public Wikimedia EventStreams activity |
| GitHub Pulse | Public repo events |
| SEC EDGAR | Current filings |
| HN + Reddit | Top internet discussion items |
| Live TV | YouTube live news embeds and fallback links |

## Settings

- Remember the selected display mode while the internal Overview flag is on.
- Show/hide optional panels.
- Save watchlist keywords and map annotations.
- Add or remove RSS feeds.
- Save the optional NASA FIRMS key locally.
- Choose default Live TV channel.
- Toggle ambient audio cues.
- Clear pins and keys.
- Shut down the local server and app.

## Optional API Unlocks

| API | Unlocks |
|---|---|
| NASA FIRMS | Fire detections |

## Privacy And Local Behavior

- No hosted Foglight backend.
- Local server binds to `127.0.0.1`.
- State changes require an ephemeral per-launch token.
- Host validation blocks DNS-rebinding origins.
- API keys stay on disk in the local state directory.
- The settings endpoint masks key values before returning them to the UI.
- RSS proxy blocks local/private targets, revalidates redirects, and caps bodies.

## Release Experience

The intended public release flow is simple:

1. User downloads `Foglight.exe` from GitHub Releases.
2. User runs the exe.
3. Foglight opens as a Windows desktop app.
4. Optional keys can be pasted in Settings.
5. Runtime state remains local to the user's Windows profile.
