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

## Requirements

- Windows 10/11
- Python 3.13+
- Build dependencies from `requirements-build.txt`

Install dependencies:

```powershell
python -m pip install -r requirements-build.txt
```

## Build

```powershell
python .\build_windows.py
```

The build script:

1. Generates `assets/foglight-icon.png` and `assets/foglight.ico`.
2. Writes `foglight_native.spec`.
3. Runs PyInstaller with the WebView and pythonnet packages collected.
4. Produces `dist\Foglight.exe`.

## Smoke Test

Headless server mode:

```powershell
$env:FOGLIGHT_NO_BROWSER = "1"
$env:FOGLIGHT_PORT = "19877"
$p = Start-Process -FilePath ".\dist\Foglight.exe" -WindowStyle Hidden -PassThru
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:19877/api/ping"
Stop-Process -Id $p.Id -Force
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

## Notes

- `dist/` and `build/` are ignored by Git.
- Upload `dist\Foglight.exe` to GitHub Releases as `Foglight.exe`.
- Do not commit local runtime state or API keys.
