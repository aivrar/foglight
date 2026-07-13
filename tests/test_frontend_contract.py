import collections
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_html_ids_match_literal_javascript_references():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    javascript = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "web").glob("*.js"))
    )
    ids = re.findall(r'\bid=["\']([^"\']+)["\']', html)
    references = set(re.findall(r"\$\(\s*['\"]([^'\"]+)['\"]\s*\)", javascript))
    references.update(
        re.findall(r"getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", javascript)
    )

    assert not [item for item, count in collections.Counter(ids).items() if count > 1]
    assert references - set(ids) == {
        "optional-cta",
        "briefing-print",
        "briefing-close",
        "incident-briefing-print",
        "incident-briefing-close",
        "incident-timeline-preview",
        "incident-timeline-scrubber",
    }


def test_csp_compatible_markup_and_local_leaflet_assets():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    javascript = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "web").glob("*.js"))
    )

    assert not re.search(r"\son[a-z]+\s*=", html, re.IGNORECASE)
    assert 'href="/vendor/leaflet/leaflet.css"' in html
    assert 'src="/vendor/leaflet/leaflet.js"' in html
    assert "unpkg.com" not in html
    assert "cartocdn.com" not in html
    assert "safeHttpUrl(f.properties.url)" in javascript
    assert "escapeHtml(t.symbol || t.id)" in javascript


def test_source_server_is_unconditionally_loopback_only():
    source = (ROOT / "foglight_server.py").read_text(encoding="utf-8")
    assert 'bind_host = "127.0.0.1"' in source
    assert "FOGLIGHT_BIND_HOST" not in source
