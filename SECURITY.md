# Security Policy

Foglight is designed as a local desktop application. It starts a local HTTP
server for the WebView UI and does not use a hosted Foglight backend.

## Supported Versions

Until the first tagged release, only the `main` branch is considered active.

## Local Security Model

- The native launcher binds to `127.0.0.1`.
- The direct Python server also binds to `127.0.0.1` unconditionally.
- Requests must use an allowed loopback `Host` value.
- State-changing endpoints require same-origin context plus an ephemeral,
  per-launch session token.
- The RSS proxy rejects localhost/private-network destinations and nonstandard
  ports, and revalidates every redirect destination.
- Upstream response sizes and disk-cache growth are bounded.
- API keys are stored in `%LOCALAPPDATA%\Foglight\state\settings.json`.
- The settings API masks stored API keys before returning settings to the UI.
- Cache filenames are SHA-256 digests; key-bearing request URLs are redacted
  from cache names, logs, and browser-visible errors.
- Browser responses include a restrictive Content Security Policy. Leaflet is
  vendored locally at a reviewed version and the default Natural Earth map is
  bundled and checksummed; the core map makes no runtime CDN request.
- User-configured RSS connects directly to the exact public addresses returned
  by its validated DNS lookup, bypasses environment proxies at that boundary,
  preserves TLS hostname verification, and revalidates redirects.
- Wire and decompressed response sizes, static files, settings/config reads,
  optional stream events, SQLite retention, cache metadata, and active logs are
  bounded.
- Runtime logs and caches live under `%LOCALAPPDATA%\Foglight\`.

Foglight is an information display, not a cybersecurity product. Its localhost
and network-input hardening protects a desktop app that consumes public feeds;
it does not scan systems, inspect private networks, exploit targets, or provide
security monitoring.

The local server is not intended for LAN use. `FOGLIGHT_ALLOWED_HOSTS` can add
development-only Host values, but it does not broaden the loopback listener.

## Reporting A Vulnerability

Open a private security advisory on GitHub if the repository has advisories
enabled. If not, open an issue with enough detail to reproduce the problem, but
do not paste real API keys, credentials, or private data.

Useful details:

- Windows version
- Foglight version or commit
- Exact steps to reproduce
- Whether the packaged exe or direct Python server was used
- Relevant log lines from `%LOCALAPPDATA%\Foglight\logs\native.log`

## Third-Party Data

Foglight displays and caches public data from many providers. Those providers
set their own terms, attribution requirements, and rate limits. See
`CREDITS.md` and `docs/DATA_SOURCES.md`.
