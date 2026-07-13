# Contributing

Thanks for taking a look at Foglight. This project is a local-first Windows
desktop dashboard, so changes should preserve the simple user path: download one
exe, run it, and get a working live-data dashboard.

## Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt -r requirements-dev.txt
```

Run source checks:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
node --check .\web\app.js
.\.venv\Scripts\python.exe -m pip_audit -r .\requirements-build.txt
```

Run the local server:

```powershell
$env:FOGLIGHT_APP_DIR = (Get-Location).Path
$env:FOGLIGHT_CACHE_DIR = "$env:TEMP\foglight-cache"
$env:FOGLIGHT_STATE_DIR = "$env:TEMP\foglight-state"
$env:FOGLIGHT_LOG_DIR = "$env:TEMP\foglight-logs"
.\.venv\Scripts\python.exe .\foglight_server.py 9787
```

Then open `http://127.0.0.1:9787/`.

## Pull Request Expectations

- Keep the one-file Windows release path working.
- Keep runtime state out of the repository.
- Do not commit `dist/`, `build/`, local caches, logs, or API keys.
- Credit new public data sources in `CREDITS.md` and
  `docs/DATA_SOURCES.md`.
- Add or update docs when user-facing behavior changes.
- Add regression tests for bug fixes and security-boundary changes.
- Prefer focused changes over broad rewrites.

## Data Sources

When adding a source, document:

- Provider name and endpoint family.
- What panel or overlay uses it.
- Whether it needs an API key.
- Any relevant attribution, usage policy, or rate-limit note.

## Release Artifacts

The built executable belongs in GitHub Releases, not in the repository. Build it
with:

```powershell
.\.venv\Scripts\python.exe .\build_windows.py
```

## License

By contributing, you agree that your contribution is provided under the MIT
License.
