from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_local_markdown_links_resolve():
    documents = [
        ROOT / "README.md",
        ROOT / "SECURITY.md",
        ROOT / "CREDITS.md",
        ROOT / "CONTRIBUTING.md",
        *sorted((ROOT / "docs").glob("*.md")),
        *sorted((ROOT / "docs" / "adr").glob("*.md")),
    ]
    missing = []
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for raw_target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                continue
            resolved = (document.parent / target).resolve()
            if not resolved.exists():
                missing.append(f"{document.relative_to(ROOT)} -> {raw_target}")
    assert missing == []


def test_published_provider_table_and_conditional_release_policy_are_complete():
    registry = json.loads(
        (ROOT / "config" / "provider_registry.v1.json").read_text(encoding="utf-8")
    )["providers"]
    published = (ROOT / "docs" / "DATA_SOURCES.md").read_text(encoding="utf-8")
    assert all(f"| `{item['id']}` |" in published for item in registry)
    assert all(item["decision"] == "approved" for item in registry if item["required"])

    yahoo = next(item for item in registry if item["id"] == "yahoo_finance")
    assert yahoo["decision"] == "disabled-default-terms-review-required"
    server = (ROOT / "foglight_server.py").read_text(encoding="utf-8")
    browser = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "FOGLIGHT_YAHOO_FINANCE_ENABLED" in server
    assert "yahoo_finance_enabled" in server and "YAHOO_FINANCE_ENABLED" in browser


def test_release_docs_ci_and_current_hero_match_gated_contracts():
    checklist = (ROOT / "docs" / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    evidence = (ROOT / "docs" / "RELEASE_EVIDENCE.md").read_text(encoding="utf-8")
    for value in (
        "build_windows.py --require-signature",
        "smoke_packaged_release.py",
        "smoke_packaged_offline.py",
        "--require-live",
    ):
        assert value in checklist
    assert "foglight-windows-unsigned-qa" in workflow
    assert "smoke_packaged_release.py" in workflow
    assert "Get-FileHash -Algorithm SHA256 $worldPath" in workflow
    assert "b853e8ab6412d655dbe2fe8719d7cfde24e266db347eeb694b4df0f627a2fdb8" in workflow
    assert "RawContentLength" not in workflow
    assert "100/100 (10/10)" in evidence
    assert "Production Artifact Evidence" in evidence
    assert "Open Production Artifact Evidence" not in evidence
    assert "NotSigned" in checklist and "signing identity" in checklist

    offline_smoke = (ROOT / "scripts" / "smoke_packaged_offline.py").read_text(
        encoding="utf-8"
    )
    for contract in (
        "read_bounded",
        "verify_loopback_listener(args.port)",
        "verify_listener_stopped(args.port)",
        "process.wait(timeout=10)",
    ):
        assert contract in offline_smoke

    native = (ROOT / "foglight_native.py").read_text(encoding="utf-8")
    release_smoke = (ROOT / "scripts" / "smoke_packaged_release.py").read_text(
        encoding="utf-8"
    )
    assert 'setdefault("FOGLIGHT_V2_ENABLED", "1")' in native
    assert 'setdefault("FOGLIGHT_OVERVIEW_ENABLED", "1")' in native
    assert "default_zero_configuration" in release_smoke
    assert "wait_for_live_overview" in release_smoke

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("hero.PNG", "standard.PNG", "command.PNG"):
        screenshot = ROOT / "docs" / "screenshots" / name
        body = screenshot.read_bytes()
        assert body.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(body) >= 100_000
        assert f"docs/screenshots/{name}" in readme
    spec = (ROOT / "foglight_native.spec").read_text(encoding="utf-8")
    version_resource = ROOT / "assets" / "foglight-version.txt"
    assert "version='assets\\\\foglight-version.txt'" in spec
    assert version_resource.is_file()
    package_version = json.loads(
        (ROOT / "package.json").read_text(encoding="utf-8")
    )["version"]
    assert (ROOT / "docs" / f"RELEASE_NOTES_v{package_version}.md").is_file()
    assert (
        f"StringStruct('ProductVersion', '{package_version}')"
        in version_resource.read_text(encoding="utf-8")
    )


def test_signed_release_workflow_is_protected_fail_closed_and_complete():
    workflow = (
        ROOT / ".github" / "workflows" / "release-windows.yml"
    ).read_text(encoding="utf-8")
    build_docs = (ROOT / "docs" / "BUILD_WINDOWS.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "if: github.ref == 'refs/heads/main'" in workflow
    assert "environment: release-signing" in workflow
    for secret in (
        "FOGLIGHT_SIGN_PFX_BASE64",
        "FOGLIGHT_SIGN_PFX_PASSWORD",
        "FOGLIGHT_TIMESTAMP_URL",
    ):
        assert f"secrets.{secret}" in workflow
        assert secret in build_docs and secret in checklist

    assert "NotBefore -le (Get-Date)" in workflow
    assert "NotAfter -gt (Get-Date)" in workflow
    assert 'codeSigningOid = "1.3.6.1.5.5.7.3.3"' in workflow
    assert "build_windows.py --require-signature" in workflow
    assert "smoke_packaged_release.py --exe" in workflow
    assert "--require-live" in workflow
    assert "smoke_packaged_offline.py --exe" in workflow
    assert 'Status -ne "Valid"' in workflow
    assert "SHA256SUMS.txt does not match" in workflow
    assert "if: always()" in workflow
    assert workflow.index("uses: actions/upload-artifact@") < workflow.index(
        "name: Remove certificate material"
    )

    action_uses = re.findall(r"uses: actions/[^@\s]+@([^\s]+)", workflow)
    assert action_uses
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in action_uses)
