import json
from pathlib import Path

import foglight_server as server
from foglight_core.providers.canonical import CORE_CANONICAL_ADAPTERS
from scripts import check_live_sources

ROOT = Path(__file__).parents[1]


def test_conditional_sources_are_not_overview_dependencies():
    registry = json.loads(
        (ROOT / "config" / "provider_registry.v1.json").read_text(encoding="utf-8")
    )
    providers = {item["id"]: item for item in registry["providers"]}

    assert "gdelt" not in providers
    assert not any("gdelt" in provider_id for provider_id in providers)
    assert "open_meteo" not in CORE_CANONICAL_ADAPTERS
    assert providers["open_meteo"]["tier"] == 5
    assert providers["open_meteo"]["required"] is False
    assert providers["open_meteo"]["decision"] == (
        "disabled-default-free-api-noncommercial"
    )
    assert "open_meteo" in server.PROVIDER_REGISTRY.ids()
    assert "open_meteo" not in check_live_sources.URLS
    assert check_live_sources.SKIPPED["open_meteo"] == (
        "skipped-disabled-default-noncommercial"
    )


def test_no_runtime_path_uses_retiring_usgs_water_services():
    runtime_files = [
        ROOT / "foglight_server.py",
        *sorted((ROOT / "foglight_core").rglob("*.py")),
        *sorted((ROOT / "web").glob("*.js")),
    ]
    runtime = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)
    assert "waterservices.usgs.gov" not in runtime
    assert "api.waterdata.usgs.gov" not in runtime
