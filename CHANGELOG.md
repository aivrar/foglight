# Changelog

All notable source-level changes for Foglight should be tracked here.

## Unreleased

- Converted the app into a native Windows desktop package using a single
  PyInstaller executable.
- Added `foglight_native.py` to start the bundled server and open the dashboard
  inside a WebView2 desktop window.
- Added deterministic icon generation and Windows `.ico` bundling.
- Kept `FOGLIGHT_NO_BROWSER=1` for automated packaged smoke tests.
- Updated app copy and documentation away from the earlier WSL/Linux runtime.
- Added repository documentation, source credits, GitHub templates, and release
  checklist docs.
- Added MIT License and GitHub Release instructions for `Foglight.exe`.

## 0.1.0

- Initial local-first dashboard prototype with live event panels, map overlays,
  RSS proxying, settings, and local cache/state directories.
