# Release Checklist

Use this before publishing a new GitHub Release.

## 1. Source Hygiene

```powershell
git status --short --ignored
```

Expected:

- Source/docs are visible as normal changes.
- `dist/` is ignored.
- `build/`, caches, logs, and `__pycache__/` are not committed.

## 2. Automated Checks

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements-build.txt -r .\requirements-dev.txt
npm ci
npx playwright install chromium
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
node --check .\web\app.js
npm run test:js
npm run test:browser
.\.venv\Scripts\python.exe -m pip_audit -r .\requirements-build.txt
.\.venv\Scripts\python.exe .\scripts\scan_secrets.py --json
npm audit --audit-level=high
```

## 3. Build

The mandatory portable build does not require a signing identity:

```powershell
.\.venv\Scripts\python.exe .\build_windows.py
```

Record the reported Authenticode status and never describe an unsigned artifact
as signed. For the optional protected signing path, confirm the
`release-signing` environment is
restricted to `main`, has an approval rule where supported, and contains
`FOGLIGHT_SIGN_PFX_BASE64`, `FOGLIGHT_SIGN_PFX_PASSWORD`, and
`FOGLIGHT_TIMESTAMP_URL`. Manually dispatch `release-windows.yml`; it must
repeat the complete source gates before it imports the certificate and its
`always()` cleanup must run after the artifact step.

Optional signed build:

```powershell
$env:FOGLIGHT_SIGN_CERT_SHA1 = "YOUR_CERTIFICATE_THUMBPRINT"
$env:FOGLIGHT_TIMESTAMP_URL = "YOUR_RFC3161_TIMESTAMP_URL"
.\.venv\Scripts\python.exe .\build_windows.py --require-signature
```

Expected output:

```text
dist\Foglight.exe
dist\SHA256SUMS.txt
```

## 4. Packaged Profile Smoke Tests

Run the deterministic clean/upgrade/corruption/restart and retained-offline
suites first:

```powershell
.\.venv\Scripts\python.exe .\scripts\smoke_packaged_release.py --exe .\dist\Foglight.exe
.\.venv\Scripts\python.exe .\scripts\smoke_packaged_offline.py --exe .\dist\Foglight.exe
```

Both must exit zero. The release candidate must also prove that the exact
no-flags executable—not a
source launcher—ingests live keyless data:

```powershell
.\.venv\Scripts\python.exe .\scripts\smoke_packaged_release.py `
  --exe .\dist\Foglight.exe --require-live
```

This gate requires Overview/V2 to be enabled automatically, at least two live
providers, and at least one rendered incident. Record external outages as such;
never replace this release evidence with a shell-only pass.

Then use this minimal manual listener check if desired:

```powershell
$env:FOGLIGHT_NO_BROWSER = "1"
$env:FOGLIGHT_PORT = "19877"
$p = Start-Process -FilePath ".\dist\Foglight.exe" -WindowStyle Hidden -PassThru
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:19877/api/ping"
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:19877/"
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:19877/api/settings"
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:19877/vendor/leaflet/leaflet.js"
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:19877/assets/natural-earth-110m-countries.v5.1.1.geojson"
$conn = Get-NetTCPConnection -LocalPort 19877 -State Listen -ErrorAction SilentlyContinue
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force }
if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
Remove-Item Env:FOGLIGHT_NO_BROWSER
Remove-Item Env:FOGLIGHT_PORT
```

## 5. Security And Artifact Checks

```powershell
Get-FileHash -Algorithm SHA256 .\dist\Foglight.exe
Get-Content .\dist\SHA256SUMS.txt
Get-AuthenticodeSignature .\dist\Foglight.exe
```

Required for every published artifact:

- SHA-256 output matches `SHA256SUMS.txt`.
- Authenticode status is recorded accurately as `Valid` or `NotSigned`.
- Local listener is `127.0.0.1`, never `0.0.0.0`.
- A POST to `/api/settings` without `X-Foglight-Token` returns 403.
- Every required provider is `approved` in `config/provider_registry.v1.json`.
- Conditional providers remain optional or have a documented approval/replacement.
- The default map does not depend on commercial hosted-tile rights.

If endpoint protection quarantines the artifact, preserve the vendor detection
record and submit the exact artifact hash as a false positive. Do not disable endpoint
protection or publish an artifact that cannot pass the packaged smoke suite.

## 6. Desktop Smoke Test

Run:

```powershell
.\dist\Foglight.exe
```

Check:

- Desktop window opens.
- Dashboard renders.
- Live TV panel shows tabs and a YouTube fallback link.
- Settings opens and closes.
- Map renders with attribution visible.
- Native log has no `WebView startup failed` entry.

Log path:

```text
%LOCALAPPDATA%\Foglight\logs\native.log
```

## 7. GitHub Release

Create a version tag:

```powershell
git tag v0.2.0
git push origin v0.2.0
```

Create the GitHub Release and upload the exe:

```powershell
gh release create v0.2.0 .\dist\Foglight.exe#Foglight.exe .\dist\SHA256SUMS.txt `
  --title "Foglight v0.2.0 - Zero-Setup Global Events Dashboard" `
  --notes-file .\docs\RELEASE_NOTES_v0.2.0.md
```

Suggested release title:

```text
Foglight v0.2.0 - Zero-Setup Global Events Dashboard
```

Suggested notes:

- Single portable Windows exe.
- Direct release download: `Foglight.exe`.
- No Python, WSL, Docker, Git, or Node required for users.
- Uses local WebView2 desktop window.
- Runtime state stored under `%LOCALAPPDATA%\Foglight\`.
- Live data depends on third-party public feeds.
