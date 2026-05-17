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

## 2. Static Checks

```powershell
python -m py_compile .\foglight_server.py .\foglight_native.py .\build_windows.py
node --check .\web\app.js
```

## 3. Build

```powershell
python -m pip install -r requirements-build.txt
python .\build_windows.py
```

Expected output:

```text
dist\Foglight.exe
```

## 4. Packaged Smoke Test

```powershell
$env:FOGLIGHT_NO_BROWSER = "1"
$env:FOGLIGHT_PORT = "19877"
$p = Start-Process -FilePath ".\dist\Foglight.exe" -WindowStyle Hidden -PassThru
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:19877/api/ping"
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:19877/"
Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:19877/api/settings"
Stop-Process -Id $p.Id -Force
Remove-Item Env:FOGLIGHT_NO_BROWSER
Remove-Item Env:FOGLIGHT_PORT
```

## 5. Desktop Smoke Test

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

## 6. Screenshot Set

Place screenshots under:

```text
docs/screenshots/
```

Recommended names:

- `hero.PNG`
- `dashboard.png`
- `live-tv.png`
- `settings.png`
- `map-overlays.png`

## 7. GitHub Release

Create a version tag:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

Create the GitHub Release and upload the exe:

```powershell
gh release create v0.1.0 .\dist\Foglight.exe#Foglight.exe `
  --title "Foglight v0.1.0 - Windows Desktop Situation Room" `
  --notes "Single portable Windows exe. No Python, WSL, Docker, Git, or Node required for users."
```

Suggested release title:

```text
Foglight v0.1.0 - Windows Desktop Situation Room
```

Suggested notes:

- Single portable Windows exe.
- Direct release download: `Foglight.exe`.
- No Python, WSL, Docker, Git, or Node required for users.
- Uses local WebView2 desktop window.
- Runtime state stored under `%LOCALAPPDATA%\Foglight\`.
- Live data depends on third-party public feeds.
