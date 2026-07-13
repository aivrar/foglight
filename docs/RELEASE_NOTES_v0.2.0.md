# Foglight v0.2.0

Foglight is now a zero-setup Windows dashboard for live public events. Download
one portable executable, launch it, and get a current global picture without an
account, hosted backend, installer, or required API key.

**[Download Foglight.exe](https://github.com/aivrar/foglight/releases/download/v0.2.0/Foglight.exe)**

## Three Ways To See The Same Picture

### Overview

Prioritized incidents with explainable evidence, category filters, source
freshness, change tracking, watch tools, and a bundled offline map.

![Foglight Overview](https://raw.githubusercontent.com/aivrar/foglight/v0.2.0/docs/screenshots/hero.PNG)

### Standard

The original high-density global dashboard with live source rails, map overlays,
tickers, public-data panels, market context, and live news video.

![Foglight Standard](https://raw.githubusercontent.com/aivrar/foglight/v0.2.0/docs/screenshots/standard.PNG)

### Command

A compact incident-first layout designed for wall displays and continuous
monitoring.

![Foglight Command](https://raw.githubusercontent.com/aivrar/foglight/v0.2.0/docs/screenshots/command.PNG)

## Highlights

- Fourteen canonical public-data providers work without accounts or API keys.
- Explainable incident priority, provenance, source health, related records, and
  immutable revision timelines.
- Local Watch Center with regions, thresholds, search, notifications, pins, and
  safe CSV/GeoJSON export.
- Bundled Natural Earth world map, cached incident history, and clearly labeled
  stale/offline states.
- Keyless aviation, FEMA declaration, NOAA marine/coastal, and NASA/JPL fireball
  context with conservative semantics.
- Bounded upstream concurrency, fast first-run scheduling, provider isolation,
  cache/database recovery, and clean native shutdown.
- Responsive and keyboard-accessible layouts with reduced-motion support and
  reviewed desktop/mobile visual baselines.

## Run It

1. Download `Foglight.exe` from this release.
2. Run it on Windows 10 or Windows 11.
3. Foglight opens in a native WebView2 window and stores its local state under
   `%LOCALAPPDATA%\Foglight\`.

Microsoft Edge WebView2 is normally already installed on supported Windows
systems. Live data depends on third-party public feeds; the application remains
usable with its bundled map and cached local history when a feed is unavailable.

## Verification

This release includes `SHA256SUMS.txt` next to the executable. The release notes
and checksum record the artifact's publisher-signature status; code signing is
not a user setup step and no user certificate is required.

- `Foglight.exe`: 19,050,146 bytes
- SHA-256: `ce8798df2ed9a18821492c773fc88aa90e58bd63e408e076b3e68ef0fd114b55`
- Windows product/file version: `0.2.0` / `0.2.0.0`
- Authenticode status: `NotSigned`

Full validation evidence is available in
[docs/RELEASE_EVIDENCE.md](https://github.com/aivrar/foglight/blob/v0.2.0/docs/RELEASE_EVIDENCE.md).
