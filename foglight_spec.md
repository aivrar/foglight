# Foglight — Historical Spec Document

> **Status:** This file preserves the original product brief and includes
> aspirational integrations. It is not a statement of the current release.
> See `README.md`, `docs/FEATURES.md`, and `docs/DATA_SOURCES.md` for the
> implemented feature set and live provider list.

*A situation room for planet Earth. Live activity, all at once, in one launchable app. Free APIs only — no-key sources work out of the box, optional-key sources unlock additional panels when the user pastes their own free API key into settings.*

---

## 1. Concept

Open the app, see the planet's nervous system pulsing. Conflict events, earthquakes, severe weather, financial flows, on-chain transactions, news firehoses, edit streams. Information-dense, ambient, alive. Not a dashboard you check — a window you leave open.

---

## 2. Name

**Foglight.** Functional metaphor — the app cuts through the fog of confusing global events to show you what's actually happening right now.

---

## 3. Data sources — verified inventory

### 3.1 No-key, no-signup (always works)

| Source | Provides | Notes |
|---|---|---|
| **UN / DW / France 24 RSS** | Public conflict and world-news reporting | Supporting context with original-publisher links |
| **USGS Earthquakes** | Live seismic events worldwide | GeoJSON feeds at multiple time/magnitude thresholds |
| **NWS api.weather.gov** | US severe weather alerts, tornado warnings, flood warnings, hurricane advisories | Requires User-Agent header |
| **mempool.space** | Bitcoin mempool, blocks, fees, transactions, lightning network | Genuinely no limits |
| **Wikipedia EventStreams** | Live global edit firehose | SSE stream |
| **GitHub public events** | Every public commit, PR, release, issue | Rate limited but no auth required |
| **ISS position** (Open-Notify / Where The ISS At) | ISS coordinates, pass predictions | |
| **DIA / CoinPaprika / Binance public** | Crypto prices, market data | Multiple providers for redundancy |
| **SEC EDGAR** | Every US public company filing (10-K, 10-Q, 8-K, insider trades) | Requires User-Agent header |
| **ECB / Frankfurter** | Forex reference rates | Daily updates |
| **GTFS-realtime feeds** | Live transit positions for cities that publish openly (NYC MTA, BART, Berlin BVG, many others) | Per-city basis |
| **Hacker News API** | Top stories, comments, live edits | |
| **Reddit JSON endpoints** | Subreddit feeds | Rate limited but no key |
| **News RSS** | Reuters, BBC, AP, AlJazeera, regional papers | Universal format |

### 3.2 Optional-key unlocks (free signup, BYOK)

| Source | Provides | Unlocks |
|---|---|---|
| **AISStream.io** | Live global ship positions via WebSocket | Marine traffic panel |
| **NASA FIRMS** | Active wildfire detections (MODIS/VIIRS) | Wildfires panel |
| **OpenSky** | Flight tracker | Flights overhead panel (OAuth2 required) |
| **OpenWeatherMap** | Global severe weather alerts, radar tiles | Extends weather alerts beyond US |
| **FRED** | US economic indicators (rates, inflation, employment, etc.) | Macro panel |
| **Finnhub / Twelve Data / Alpha Vantage** | Stock prices, fundamentals, news | Financial markets panel |

For each optional source, the README walks the user through getting their free key in plain language.

### 3.3 Conditional compatibility sources

Open-Meteo click-to-inspect weather remains a legacy compatibility path only.
Its free endpoint is restricted to non-commercial use, so Foglight disables it
by default and does not use it in Overview. GDELT is not a current provider:
its hosted APIs have unspecified client quotas and are unnecessary for the
keyless incident view. Retiring `waterservices.usgs.gov` endpoints are not used;
any future USGS water integration must target the modern Water Data OGC API and
revisit its API-key threshold.

---

## 4. Out-of-box panels (no signup required)

Each panel description: what it shows, source, key data fields, visual treatment notes.

### 4.1 World Events Map ✅ *flagship panel*
- **Sources:** Canonical hazard/observation providers plus supporting public RSS
- **Shows:** Located official hazards and observations; news reports remain clearly labeled supporting context
- **Key fields:** Source geometry, event time, lifecycle, provenance, and explainable priority
- **Visual:** World map with category shapes/colors, deterministic clustering, and synchronized incident detail.

### 4.2 Earthquake Feed ✅
- **Source:** USGS GeoJSON feeds
- **Shows:** Live earthquakes worldwide, last hour / day / week
- **Key fields:** Magnitude, depth, location, time, tsunami flag
- **Visual:** Map overlay (shared with World Events Map) plus side panel with chronological list. Bigger quakes get bigger pins.

### 4.3 Severe Weather ✅
- **Source:** NWS (api.weather.gov)
- **Shows:** Active US watches/warnings (tornado, severe thunderstorm, flood, hurricane, winter storm).
- **Visual:** US polygons on map for active alerts, color-coded by severity. Sidebar lists active alerts chronologically.

### 4.4 Bitcoin Pulse ✅
- **Source:** mempool.space
- **Shows:** Mempool by fee band (live), block-by-block clearing, fee predictions, lightning network channel activity, large transactions
- **Visual:** Vertical fee-band stack that fills up between blocks and drops when a block is mined. Showstopper visual. Optional whale-tx ticker on the side.

### 4.5 News Firehose ✅
- **Sources:** RSS feeds from Reuters, BBC, AP, AlJazeera, plus user-configurable list
- **Shows:** Scrolling headline ticker
- **Visual:** Old-school news-ticker bands across the top or bottom of the screen. Multiple lanes (world / business / tech / regional).

### 4.6 Wikipedia Live Edits ✅
- **Source:** Wikipedia EventStreams
- **Shows:** Every edit happening across Wikipedia globally
- **Visual:** Vertical scrolling list with article title, language, edit size. Optional language filter. Hypnotic ambient panel.

### 4.7 GitHub Pulse ✅
- **Source:** GitHub public events API
- **Shows:** Live commits, releases, new repos
- **Visual:** Sparse activity stream. Optional filter by language. Companion to the Wikipedia panel.

### 4.8 ISS Tracker ✅
- **Source:** Open-Notify / Where The ISS At
- **Shows:** ISS current position + next pass over user's location
- **Visual:** Small ground-track widget on the corner of the world map.

### 4.9 Crypto Prices ✅
- **Sources:** DIA primary, CoinPaprika fallback, Binance public for high-volume pairs
- **Shows:** Major crypto prices, % change, volume
- **Visual:** Ticker band at top with major pairs. Sparkline per asset on hover.

### 4.10 Forex Strip ✅
- **Source:** ECB / Frankfurter
- **Shows:** Major currency pairs vs USD/EUR
- **Visual:** Compact strip, refresh once per day (ECB updates daily). Companion to crypto ticker.

### 4.11 SEC Filings Feed ✅
- **Source:** SEC EDGAR
- **Shows:** Every 8-K, 10-Q, 10-K, insider trade as filed
- **Visual:** Scrolling list with company ticker, filing type, time. Color flag for "material events" (8-Ks).

### 4.12 Hacker News + Reddit Trending ✅
- **Sources:** HN API, Reddit JSON
- **Shows:** Top items right now
- **Visual:** Compact two-column "what the internet is talking about" panel.

---

## 5. Optional-unlock panels (BYOK)

Hidden until user adds a key in settings. Settings panel clearly lists each key, what it unlocks, and a link to where to get it.

### 5.1 Marine Traffic 🔵
- **Unlocks with:** AISStream.io key
- **Shows:** Live global ship positions via WebSocket subscription, filterable by bounding box
- **Visual:** Ship icons on world map, colored by vessel type, oriented by heading

### 5.2 Wildfires 🔵
- **Unlocks with:** NASA FIRMS MAP_KEY
- **Shows:** Active fire detections (MODIS + VIIRS), near-real-time
- **Visual:** Flame icons on world map, intensity color-coded

### 5.3 Flights Overhead 🔵
- **Unlocks with:** OpenSky OAuth2 client credentials
- **Shows:** Aircraft state vectors in configurable bounding box
- **Visual:** Plane icons on map with heading, hover for callsign/altitude/speed

### 5.4 Global Severe Weather 🔵
- **Unlocks with:** OpenWeatherMap key
- **Shows:** Severe weather alerts outside the US, radar tiles globally
- **Visual:** Extends the existing Severe Weather panel beyond US borders

### 5.5 Macro Indicators 🔵
- **Unlocks with:** FRED key
- **Shows:** Fed funds rate, 10-year yield, CPI, unemployment, VIX, recession probability
- **Visual:** Strip of gauges/dials at the bottom of the screen — the "what regime are we in" panel

### 5.6 Stock Markets 🔵
- **Unlocks with:** Finnhub, Twelve Data, or Alpha Vantage key (user picks)
- **Shows:** Major indices, user's watchlist, earnings calendar, market news with sentiment
- **Visual:** Adds a stocks ticker band alongside the crypto ticker, optional treemap heatmap of S&P 500 on demand

---

## 6. Ambient audio layer

Off by default. Toggled in settings. When on, subtle audio cues fire on events:

| Event | Sound |
|---|---|
| Earthquake ≥ M4.0 | Low tick, pitch scaled to magnitude |
| Earthquake ≥ M6.0 | Deeper resonant tone |
| US tornado warning issued | Soft warning chime (not the actual EAS tone) |
| Hurricane advisory issued | Subdued horn |
| Major world-context update | Soft pulse |
| Bitcoin block mined | Quiet "thunk" |
| Breaking news (RSS keyword match) | Gentle ping |
| ISS overhead pass starting | Optional chime |

Per-event-type toggles. Master volume slider. The whole layer is muted by default so first-launch isn't startling.

**Critical:** sounds must be subtle and non-alarming. This is ambient, not panic-inducing. Imagine a coffee shop's gentle background, not a trading floor.

---

## 7. UI/UX considerations

*Concept-level, not prescribing tech.*

- **Layout:** The world map is the central element. Multiple panels arrange around it. Ticker bands at top and bottom. Side rails for chronological feeds (earthquakes, news, edits).
- **Density:** Information-dense by design. This is not a minimal SaaS app. Bloomberg-terminal density, with better aesthetics.
- **Theming:** Dark mode primary. Map uses a dark base layer (think Carto Dark Matter or similar). Color accents for event types — red/orange for severity, blue for neutral motion, yellow for alerts.
- **Map provider:** Free tile providers exist (OpenStreetMap, CartoDB, Stamen). No key needed.
- **Refresh indicators:** Each panel shows last-update time. Stale data fades or gets a visible marker so users always know what's live vs cached.
- **Settings panel:** Single screen where users paste optional keys. Each key entry shows: label, what it unlocks, link to signup page, status indicator (working / not connected / error).
- **First launch:** Brief explanation of what works out-of-box and what can be unlocked. Don't bury the BYOK feature.
- **Layout persistence:** User-arranged panel positions remembered between launches.
- **Offline behavior:** Show last-fetched data with stale indicator if network drops.

---

## 8. Out of scope (deliberately)

- Trade execution (anything that touches money)
- AI predictions or "buy signals"
- Real-time level-2 order book data (not free)
- Live emergency-services radio audio (not free, paywalled)
- Real-time road traffic (no good free option)
- User accounts or cloud sync — everything stays local

---

## 9. Open decisions — checklist

1. ☐ **Map provider** — OpenStreetMap, CartoDB Dark, Stamen, or other
2. ☐ **Default geographic centering** — fixed (world view) or user-location-based on first launch
3. ☐ **News RSS feed list** — start with which outlets?
4. ☐ **GTFS-realtime cities** — include any by default, or only the user's nearest?
5. ☐ **Settings storage** — single config file location?
6. ☐ **Audio asset sources** — synthesize or use pre-recorded?
7. ☐ **Update intervals** — refresh rate per panel (defaults vs user-configurable)
8. ☐ **Bitcoin Pulse vs broader on-chain** — Bitcoin only, or also surface Ethereum/Solana via free public RPCs?

---

## 10. Things I've added beyond the original ask

Per your "show me ideas before adding them" preference, here are the additions I've made beyond strict "concerning shit happening" — cut anything that doesn't fit:

- **Wikipedia and GitHub firehoses** — these are neutral activity rather than concerning events, but they reinforce the "Earth's nervous system" feel
- **Crypto and forex tickers** — extending into financial movement
- **SEC EDGAR filings** — niche but distinctive, very few apps surface this live
- **ISS tracker** — small flavor feature, very cheap to include
- **HN + Reddit trending** — adds the "internet pulse" dimension
- **Macro indicators (FRED) and Stock markets** — fully optional, only appear if user adds the relevant keys
- **Ambient audio layer** — your earlier idea, kept here with explicit "off by default" and "subtle, not alarming" constraints
- **Layout persistence and settings panel** — necessary scaffolding for the BYOK pattern

Things I left **out** that we discussed earlier:
- Live emergency-services radio (paywalled, no free option exists)
- Real-time road traffic (no free option at scale)
- Border wait times (from the earlier concept, not a fit for this app's identity)
