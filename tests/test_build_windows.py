from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import build_windows


def test_version_resource_comes_from_package_version(tmp_path, monkeypatch):
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"version": "12.34.56"}), encoding="utf-8")
    version_file = tmp_path / "foglight-version.txt"
    monkeypatch.setattr(build_windows, "PACKAGE_PATH", package)
    monkeypatch.setattr(build_windows, "VERSION_PATH", version_file)

    assert build_windows.make_version_info() == version_file
    content = version_file.read_text(encoding="utf-8")
    assert "filevers=(12, 34, 56, 0)" in content
    assert "prodvers=(12, 34, 56, 0)" in content
    assert "StringStruct('ProductVersion', '12.34.56')" in content
    assert "StringStruct('OriginalFilename', 'Foglight.exe')" in content


@pytest.mark.parametrize("version", ["1.2", "1.2.beta", "1.2.3.4.5", "1.2.65536"])
def test_version_resource_rejects_invalid_windows_versions(
    version, tmp_path, monkeypatch
):
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"version": version}), encoding="utf-8")
    monkeypatch.setattr(build_windows, "PACKAGE_PATH", package)
    monkeypatch.setattr(build_windows, "VERSION_PATH", tmp_path / "version.txt")
    with pytest.raises(RuntimeError, match="version|components"):
        build_windows.make_version_info()


def test_checksum_is_streamed_and_matches_artifact(tmp_path):
    artifact = tmp_path / "Foglight.exe"
    artifact.write_bytes((b"foglight" * 200_000) + b"end")
    manifest = build_windows.write_checksum(artifact)
    expected = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert manifest.read_text(encoding="ascii") == f"{expected}  Foglight.exe\n"


def test_signing_is_optional_but_strict_when_required(tmp_path, monkeypatch):
    artifact = tmp_path / "Foglight.exe"
    artifact.write_bytes(b"exe")
    monkeypatch.delenv("FOGLIGHT_SIGN_CERT_SHA1", raising=False)
    assert build_windows.sign_executable(artifact) is False
    with pytest.raises(RuntimeError, match="signing is required"):
        build_windows.sign_executable(artifact, required=True)

    monkeypatch.setenv("FOGLIGHT_SIGN_CERT_SHA1", "not-a-thumbprint")
    with pytest.raises(RuntimeError, match="40-digit"):
        build_windows.sign_executable(artifact)


def test_signing_uses_sha256_timestamp_and_policy_verification(
    tmp_path, monkeypatch
):
    artifact = tmp_path / "Foglight.exe"
    artifact.write_bytes(b"exe")
    thumbprint = "A" * 40
    monkeypatch.setenv("FOGLIGHT_SIGN_CERT_SHA1", thumbprint)
    monkeypatch.setenv("FOGLIGHT_TIMESTAMP_URL", "https://timestamp.example.test")
    monkeypatch.setattr(build_windows, "find_signtool", lambda: "signtool.exe")
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))

    monkeypatch.setattr(build_windows.subprocess, "run", run)
    assert build_windows.sign_executable(artifact, required=True) is True
    assert commands[0][0][:6] == [
        "signtool.exe", "sign", "/sha1", thumbprint, "/fd", "SHA256"
    ]
    assert "/tr" in commands[0][0] and "/td" in commands[0][0]
    assert commands[1][0] == [
        "signtool.exe", "verify", "/pa", "/v", str(Path(artifact))
    ]
    assert all(command[1]["check"] is True for command in commands)
