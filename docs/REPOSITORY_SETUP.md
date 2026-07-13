# Repository Setup

The repository is published at `aivrar/foglight`. This document records the
recommended GitHub configuration for maintaining it safely.

Suggested repository name:

```text
foglight
```

Suggested repository description:

```text
Portable Windows dashboard for live hazards, severe weather, earthquakes, humanitarian updates, public signals, and news. One EXE, zero setup, no required API keys.
```

Suggested homepage URL:

```text
https://github.com/aivrar/foglight/releases/latest
```

Suggested topics:

```text
windows desktop-app portable no-install zero-configuration no-api-key local-first public-data live-dashboard data-visualization geospatial situational-awareness disaster-monitoring natural-hazards emergency-management weather severe-weather earthquakes humanitarian live-news
```

These use GitHub's 20-topic limit for the product's actual purpose and audience:
portable Windows software, zero-configuration public data, hazard and weather
awareness, humanitarian updates, geospatial visualization, and live dashboards.

## Repository Controls

After the CI workflow is present on the default branch:

1. Enable Dependabot alerts and security updates.
2. Protect `main` and require the `verify-windows-release` CI job.
3. Require pull requests and block force-pushes/deletion on `main`.
4. Enable private vulnerability reporting.
5. Keep release assets immutable after publication where available.

## Before A Release

1. Add screenshots to `docs/screenshots/`.
2. Run the release checklist in `docs/RELEASE_CHECKLIST.md`.
3. Confirm `dist/Foglight.exe` is ignored by Git and will be uploaded as a
   release artifact instead of committed.
4. Keep the MIT `LICENSE` file in the repo root.

## Historical Manual Creation

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
  --description "Portable Windows dashboard for live hazards, severe weather, earthquakes, humanitarian updates, public signals, and news. One EXE, zero setup, no required API keys." `
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
  --add-topic windows,desktop-app,portable,no-install,zero-configuration,no-api-key,local-first,public-data,live-dashboard,data-visualization,geospatial,situational-awareness,disaster-monitoring,natural-hazards,emergency-management,weather,severe-weather,earthquakes,humanitarian,live-news
```

Then tag and create the current release with the single exe:

```powershell
git tag v0.2.0
git push origin v0.2.0
gh release create v0.2.0 .\dist\Foglight.exe#Foglight.exe .\dist\SHA256SUMS.txt `
  --title "Foglight v0.2.0 - Zero-Setup Global Events Dashboard" `
  --notes-file .\docs\RELEASE_NOTES_v0.2.0.md
```
