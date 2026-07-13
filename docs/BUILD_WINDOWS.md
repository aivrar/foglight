# Build Windows EXE

Foglight's release artifact is a single Windows executable:

```text
dist\Foglight.exe
```

The exe bundles:

- Python runtime
- Foglight local HTTP server
- Static web UI
- pywebview desktop shell
- WebView/pythonnet bridge files collected by PyInstaller
- Foglight icon

## Developer Requirements

Release users do not install Python. These requirements are only for rebuilding
`Foglight.exe` from source.

- Windows 10/11
- Python 3.13+
- Build dependencies from `requirements-build.txt`

Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

## Build

```powershell
.\.venv\Scripts\python.exe .\build_windows.py
```

The build script:

1. Generates `assets/foglight-icon.png` and `assets/foglight.ico`.
2. Writes `foglight_native.spec`.
3. Runs PyInstaller with the WebView and pythonnet packages collected.
4. Authenticode-signs the executable when `FOGLIGHT_SIGN_CERT_SHA1` is set.
5. Produces `dist\Foglight.exe` and `dist\SHA256SUMS.txt`.

## Development And Production Code Signing

Set the SHA-1 thumbprint of a code-signing certificate already installed in
the Windows certificate store:

```powershell
$env:FOGLIGHT_SIGN_CERT_SHA1 = "YOUR_CERTIFICATE_THUMBPRINT"
$env:FOGLIGHT_TIMESTAMP_URL = "YOUR_RFC3161_TIMESTAMP_URL"
.\.venv\Scripts\python.exe .\build_windows.py
```

The build fails if signing was requested but `signtool.exe` is unavailable.
Without the environment variable, the build succeeds and clearly reports that
the executable is unsigned. An unsigned artifact is functional and may be
distributed when its status is disclosed, but Windows or antivirus products
may show additional reputation warnings.

Publishers that have a trusted certificate can make signing mandatory:

```powershell
$env:FOGLIGHT_SIGN_CERT_SHA1 = "YOUR_CERTIFICATE_THUMBPRINT"
$env:FOGLIGHT_TIMESTAMP_URL = "YOUR_RFC3161_TIMESTAMP_URL"
.\.venv\Scripts\python.exe .\build_windows.py --require-signature
```

This optional mode fails before packaging if the certificate configuration, timestamp
URL, or Windows SDK `signtool.exe` is missing. After signing it runs
`signtool verify /pa /v`; a signature that does not satisfy Windows' normal
Authenticode policy cannot pass the production build.

Unsigned PyInstaller one-file executables can trigger generic machine-learning
or heuristic antivirus detections. Do not tell users to disable endpoint
protection. Keep exclusions narrowly scoped during controlled local builds,
publish the SHA-256 manifest, submit false positives to the affected vendor,
and keep the source/build recipe available for independent verification.
Trusted timestamped signing can improve publisher provenance, but it is not a
Foglight runtime dependency and users never configure it.

### Protected GitHub release candidate

The optional manually dispatched `release-windows.yml` workflow repeats every source
gate, imports the signing identity only for the build, requires timestamped
signing, runs both packaged profile suites, rechecks Authenticode and the
checksum, and uploads a 30-day signed candidate artifact. It does not publish a
GitHub release.

Create a GitHub environment named `release-signing`, restrict its deployment
branches, and configure an approval rule where the repository plan supports
one. Add these environment secrets:

- `FOGLIGHT_SIGN_PFX_BASE64`: base64 encoding of the trusted PFX bytes.
- `FOGLIGHT_SIGN_PFX_PASSWORD`: PFX import password.
- `FOGLIGHT_TIMESTAMP_URL`: the certificate issuer's RFC 3161 endpoint.

The workflow rejects missing or malformed secrets, expired certificates,
certificates without a private key or code-signing EKU, ambiguous PFX files,
failed timestamps, invalid trust-policy verification, packaged scenario
failures, and checksum mismatches. Its cleanup step runs even after failure and
removes the imported certificate and temporary PFX from the hosted runner.

## Smoke Test

Headless server mode:

```powershell
$env:FOGLIGHT_NO_BROWSER = "1"
$env:FOGLIGHT_PORT = "19877"
$p = Start-Process -FilePath ".\dist\Foglight.exe" -WindowStyle Hidden -PassThru
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:19877/api/ping"
$conn = Get-NetTCPConnection -LocalPort 19877 -State Listen -ErrorAction SilentlyContinue
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force }
if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }
Remove-Item Env:FOGLIGHT_NO_BROWSER
Remove-Item Env:FOGLIGHT_PORT
```

Normal desktop launch:

```powershell
.\dist\Foglight.exe
```

Expected behavior:

- Native desktop window opens.
- Local server responds on `127.0.0.1`.
- Runtime logs appear under `%LOCALAPPDATA%\Foglight\logs\`.

The complete deterministic profile suite is:

```powershell
.\.venv\Scripts\python.exe .\scripts\smoke_packaged_release.py --exe .\dist\Foglight.exe
.\.venv\Scripts\python.exe .\scripts\smoke_packaged_offline.py --exe .\dist\Foglight.exe
```

Before publishing, also require live zero-key ingestion from the exact no-flags
executable. This is intentionally a release/manual gate rather than deterministic
CI because upstream availability is external:

```powershell
.\.venv\Scripts\python.exe .\scripts\smoke_packaged_release.py --exe .\dist\Foglight.exe --require-live
```

It covers a no-flags Overview launch, a clean Standard compatibility launch,
first launch with networking blocked,
legacy-profile upgrade, corrupt cache/history recovery, retained-history
provider outage, loopback/Host/token boundaries, and shutdown/restart cleanup.

If endpoint protection has quarantined an unsigned development PE, the same
scenario logic can be validated through the native source launcher without
weakening protection:

```powershell
.\.venv\Scripts\python.exe .\scripts\smoke_packaged_release.py --source-native
.\.venv\Scripts\python.exe .\scripts\smoke_packaged_offline.py --source-native
```

The output labels this as `source-native`. It validates the launcher and every
profile transition but does **not** prove PyInstaller extraction, the final PE,
its checksum, or Authenticode; the executable-mode runs remain mandatory.

## Notes

- `dist/` and `build/` are ignored by Git.
- Upload `dist\Foglight.exe` to GitHub Releases as `Foglight.exe`.
- Upload `dist\SHA256SUMS.txt` beside the executable.
- Do not commit local runtime state or API keys.
