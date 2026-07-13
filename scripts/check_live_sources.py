#!/usr/bin/env python3
"""Opt-in diagnostic for upstream reachability; never a required CI gate."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # Direct execution sets sys.path[0] to scripts/. Keep the documented
    # command usable without requiring an editable package installation.
    sys.path.insert(0, str(ROOT))

URLS = {
    "usgs_earthquakes": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    "nws_alerts": "https://api.weather.gov/alerts/active?status=actual",
    "nhc_storms": "https://www.nhc.noaa.gov/CurrentStorms.json",
    "noaa_tsunami": "https://www.tsunami.gov/events/xml/PHEBAtom.xml",
    "noaa_aviation_weather": "https://aviationweather.gov/api/data/airsigmet?format=geojson",
    "noaa_space_weather": "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    "nasa_eonet": "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=1",
    "smithsonian_volcano": "https://volcano.si.edu/news/WeeklyVolcanoRSS.xml",
    "gdacs": "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
    "openfema_declarations": "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?$orderby=declarationDate%20desc&$top=100",
    "nasa_jpl_fireballs": "https://ssd-api.jpl.nasa.gov/fireball.api?limit=20",
    "reliefweb_rss": "https://reliefweb.int/updates/rss.xml",
    "conflict_rss": "https://news.un.org/feed/subscribe/en/news/topic/peace-and-security/feed/rss.xml",
    "defense_rss": "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=1",
    "adsb_lol": "https://api.adsb.lol/v2/lat/35/lon/139/dist/10",
    "open_notify_iss": "http://api.open-notify.org/iss-now.json",
    "mempool_space": "https://mempool.space/api/v1/fees/recommended",
    "coinpaprika": "https://api.coinpaprika.com/v1/tickers?quotes=USD&limit=1",
    "frankfurter": "https://api.frankfurter.app/latest?from=USD",
    "yahoo_finance": "https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF?interval=1d&range=5d",
    "github_events": "https://api.github.com/events?per_page=1",
    "sec_edgar": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&count=1&output=atom",
    "hacker_news": "https://hacker-news.firebaseio.com/v0/topstories.json",
    "reddit_popular": "https://www.reddit.com/r/popular/.rss",
    "wikimedia_recentchange": "https://stream.wikimedia.org/v2/stream/recentchange",
}
SKIPPED = {
    "nasa_firms": "skipped-user-key-required",
    "rss_proxy": "skipped-user-configured",
    "ndbc_observations": "skipped-local-context-required",
    "noaa_coops_water_levels": "skipped-local-context-required",
    "open_meteo": "skipped-disabled-default-noncommercial",
}


def check(
    item: tuple[str, str], timeout: float, *, normalize_core: bool = False,
    max_bytes: int = 2 * 1024 * 1024,
) -> dict[str, object]:
    provider_id, url = item
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Foglight/1 (+https://github.com/aivrar/foglight)"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if provider_id == "wikimedia_recentchange":
                body = response.readline(min(max_bytes, 64 * 1024) + 1)
            else:
                body = response.read(max_bytes + 1 if normalize_core else 1024)
            status = response.status
            content_type = response.headers.get_content_type()
        result = {"id": provider_id, "ok": 200 <= status < 400, "status": status,
                  "content_type": content_type,
                  "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
        if normalize_core:
            from foglight_core.providers.canonical import CORE_CANONICAL_ADAPTERS

            adapter = CORE_CANONICAL_ADAPTERS.get(provider_id)
            if adapter is not None:
                if len(body) > max_bytes:
                    result.update(ok=False, normalization_error="body-cap-exceeded")
                else:
                    ingested_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    try:
                        normalized = adapter.normalize(body, ingested_at=ingested_at)
                    except Exception as error:
                        result.update(
                            ok=False,
                            normalization_error=type(error).__name__,
                        )
                    else:
                        result["observations"] = len(normalized.observations)
                        result["drift"] = [
                            {"code": item.code, "record_id": item.record_id,
                             "fields": list(item.fields)}
                            for item in normalized.diagnostics
                        ][:100]
        return result
    except urllib.error.HTTPError as error:
        return {"id": provider_id, "ok": False, "status": error.code,
                "error": type(error).__name__,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
    except (OSError, urllib.error.URLError) as error:
        return {"id": provider_id, "ok": False, "error": type(error).__name__,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Make live requests to Foglight upstreams and report reachability."
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="required acknowledgement that this command contacts third parties",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument(
        "--normalize-core",
        action="store_true",
        help="also run bounded Phase 3 normalizers and report payload-safe drift",
    )
    args = parser.parse_args()
    if not args.confirm_live:
        parser.error("--confirm-live is required; this diagnostic contacts third parties")
    registry = json.loads(
        (ROOT / "config" / "provider_registry.v1.json").read_text(encoding="utf-8")
    )
    entries = {provider["id"]: provider for provider in registry["providers"]}
    registered = set(entries)
    diagnostic_ids = set(URLS) | set(SKIPPED)
    if registered != diagnostic_ids:
        raise RuntimeError(
            "live diagnostic and provider registry differ: "
            f"missing={sorted(registered - diagnostic_ids)}, "
            f"unknown={sorted(diagnostic_ids - registered)}"
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda item: check(
                    item,
                    args.timeout,
                    normalize_core=args.normalize_core,
                    max_bytes=int(
                        entries[item[0]].get("body_cap_bytes", 2 * 1024 * 1024)
                    ),
                ),
                URLS.items(),
            )
        )
    results.extend(
        {"id": provider_id, "ok": None, "status": status}
        for provider_id, status in SKIPPED.items()
    )
    print(json.dumps({"schema_version": 1, "required_ci_gate": False,
                      "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "sources": sorted(results, key=lambda item: item["id"])}, indent=2))


if __name__ == "__main__":
    main()
