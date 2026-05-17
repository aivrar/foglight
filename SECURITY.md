# Security Policy

Foglight is designed as a local desktop application. It starts a local HTTP
server for the WebView UI and does not use a hosted Foglight backend.

## Supported Versions

Until the first tagged release, only the `main` branch is considered active.

## Local Security Model

- The native launcher binds to `127.0.0.1`.
- State-changing endpoints require same-origin requests.
- The RSS proxy rejects localhost and private-network destinations.
- API keys are stored in `%LOCALAPPDATA%\Foglight\state\settings.json`.
- The settings API masks stored API keys before returning settings to the UI.
- Runtime logs and caches live under `%LOCALAPPDATA%\Foglight\`.

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
