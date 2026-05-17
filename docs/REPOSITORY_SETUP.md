# Repository Setup

This directory is prepared for a new GitHub repository under `aivrar`.

Suggested repository name:

```text
foglight
```

Suggested repository description:

```text
Single-exe Windows desktop situation room for live global events: hazards, weather, earthquakes, conflict wires, markets, public web activity, and live news video. No install, Python, WSL, or Docker.
```

Suggested homepage URL:

```text
https://github.com/aivrar/foglight/releases/latest
```

Suggested topics:

```text
windows desktop-app portable live-dashboard situational-awareness osint threat-intelligence disaster-monitoring emergency-management weather earthquakes live-news rss webview2 pyinstaller python local-first no-install markets public-data
```

These stay within GitHub's topic limit and are meant to catch both desktop-app
users and people searching for OSINT, hazard monitoring, emergency awareness,
and live public-data dashboards.

## Before Creating The Repo

1. Add screenshots to `docs/screenshots/`.
2. Run the release checklist in `docs/RELEASE_CHECKLIST.md`.
3. Confirm `dist/Foglight.exe` is ignored by Git and will be uploaded as a
   release artifact instead of committed.
4. Keep the MIT `LICENSE` file in the repo root.

## Manual GitHub Creation

Create an empty GitHub repo named `foglight`, then run:

```powershell
git remote add origin https://github.com/aivrar/foglight.git
git add .
git commit -m "Initial Foglight desktop release"
git push -u origin main
gh repo edit aivrar/foglight --enable-wiki=false --enable-projects=false --enable-discussions=false --homepage "https://github.com/aivrar/foglight/releases/latest"
```

## GitHub CLI Option

Only run this when you are ready to create the remote:

```powershell
gh repo create aivrar/foglight `
  --public `
  --disable-wiki `
  --description "Single-exe Windows desktop situation room for live global events: hazards, weather, earthquakes, conflict wires, markets, public web activity, and live news video. No install, Python, WSL, or Docker." `
  --homepage "https://github.com/aivrar/foglight/releases/latest" `
  --source=. `
  --remote=origin `
  --push

gh repo edit aivrar/foglight `
  --enable-wiki=false `
  --enable-projects=false `
  --enable-discussions=false `
  --delete-branch-on-merge `
  --homepage "https://github.com/aivrar/foglight/releases/latest" `
  --add-topic windows,desktop-app,portable,live-dashboard,situational-awareness,osint,threat-intelligence,disaster-monitoring,emergency-management,weather,earthquakes,live-news,rss,webview2,pyinstaller,python,local-first,no-install,markets,public-data
```

Then tag and create the first release with the single exe:

```powershell
git tag v0.1.0
git push origin v0.1.0
gh release create v0.1.0 .\dist\Foglight.exe#Foglight.exe `
  --title "Foglight v0.1.0 - Windows Desktop Situation Room" `
  --notes "Single portable Windows exe. No Python, WSL, Docker, Git, or Node required for users."
```
