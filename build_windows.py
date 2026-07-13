#!/usr/bin/env python3
"""Build the single-file Windows Foglight executable."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
ASSET_DIR = ROOT / "assets"
ICON_PATH = ASSET_DIR / "foglight.ico"
PNG_ICON_PATH = ASSET_DIR / "foglight-icon.png"
VERSION_PATH = ASSET_DIR / "foglight-version.txt"
SPEC_PATH = ROOT / "foglight_native.spec"
PACKAGE_PATH = ROOT / "package.json"


def make_icon() -> None:
    ASSET_DIR.mkdir(exist_ok=True)
    base_size = 1024
    img = Image.new("RGBA", (base_size, base_size), (0, 0, 0, 0))

    bg = Image.new("RGBA", img.size, (5, 8, 16, 255))
    draw = ImageDraw.Draw(bg)
    for y in range(base_size):
        t = y / base_size
        r = int(4 + 6 * t)
        g = int(7 + 18 * t)
        b = int(16 + 38 * t)
        draw.line([(0, y), (base_size, y)], fill=(r, g, b, 255))

    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((154, 154, 870, 870), outline=(58, 123, 213, 160), width=22)
    gd.ellipse((258, 258, 766, 766), outline=(78, 197, 255, 120), width=10)
    glow = glow.filter(ImageFilter.GaussianBlur(16))

    mark = Image.new("RGBA", img.size, (0, 0, 0, 0))
    md = ImageDraw.Draw(mark)
    md.ellipse((154, 154, 870, 870), outline=(82, 142, 230, 255), width=24)
    md.ellipse((260, 260, 764, 764), outline=(30, 49, 78, 255), width=8)
    md.line((512, 124, 512, 246), fill=(126, 224, 255, 220), width=12)
    md.line((512, 778, 512, 900), fill=(126, 224, 255, 180), width=12)
    md.line((124, 512, 246, 512), fill=(126, 224, 255, 180), width=12)
    md.line((778, 512, 900, 512), fill=(126, 224, 255, 180), width=12)

    md.ellipse((386, 386, 638, 638), fill=(255, 59, 110, 255))
    md.ellipse((438, 438, 586, 586), fill=(255, 138, 61, 255))
    md.ellipse((484, 484, 540, 540), fill=(255, 255, 255, 245))

    sweep = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sweep)
    sd.pieslice((186, 186, 838, 838), start=315, end=28, fill=(78, 197, 255, 70))
    sweep = sweep.filter(ImageFilter.GaussianBlur(4))

    composed = Image.alpha_composite(bg, glow)
    composed = Image.alpha_composite(composed, sweep)
    composed = Image.alpha_composite(composed, mark)

    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((42, 42, 982, 982), radius=190, fill=255)
    rounded = Image.new("RGBA", img.size, (0, 0, 0, 0))
    rounded.paste(composed, (0, 0), mask)

    rounded.save(PNG_ICON_PATH)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    rounded.save(ICON_PATH, sizes=sizes)


def make_version_info() -> Path:
    """Generate deterministic Windows version metadata from package.json."""
    package = json.loads(PACKAGE_PATH.read_text(encoding="utf-8"))
    version = package.get("version")
    if not isinstance(version, str):
        raise RuntimeError("package.json version must be a string")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not match:
        raise RuntimeError("package.json version must contain three or four integers")
    parts = tuple(int(value or 0) for value in match.groups())
    if any(value > 65_535 for value in parts):
        raise RuntimeError("Windows version components must not exceed 65535")
    numeric = ", ".join(str(value) for value in parts)
    dotted = ".".join(str(value) for value in parts)
    VERSION_PATH.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numeric}),
    prodvers=({numeric}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Foglight contributors'),
          StringStruct('FileDescription', 'Foglight Desktop Situation Room'),
          StringStruct('FileVersion', '{dotted}'),
          StringStruct('InternalName', 'Foglight'),
          StringStruct('LegalCopyright', 'Copyright (c) 2026 Foglight contributors'),
          StringStruct('OriginalFilename', 'Foglight.exe'),
          StringStruct('ProductName', 'Foglight'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return VERSION_PATH


def write_spec() -> None:
    spec = f"""# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

webview_datas, webview_binaries, webview_hiddenimports = collect_all('webview')
pythonnet_datas, pythonnet_binaries, pythonnet_hiddenimports = collect_all('pythonnet')
clr_datas, clr_binaries, clr_hiddenimports = collect_all('clr_loader')

a = Analysis(
    ['foglight_native.py'],
    pathex=[],
    binaries=webview_binaries + pythonnet_binaries + clr_binaries,
    datas=[
        ('index.html', '.'),
        ('web', 'web'),
        ('config', 'config'),
    ] + webview_datas + pythonnet_datas + clr_datas,
    hiddenimports=webview_hiddenimports + pythonnet_hiddenimports + clr_hiddenimports + [
        'webview.platforms.winforms',
        'webview.platforms.edgechromium',
        'clr',
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Foglight',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={str(ICON_PATH.relative_to(ROOT))!r},
    version={str(VERSION_PATH.relative_to(ROOT))!r},
)
"""
    SPEC_PATH.write_text(spec, encoding="utf-8")


def find_signtool() -> str | None:
    configured = os.environ.get("FOGLIGHT_SIGNTOOL", "").strip()
    if configured:
        return configured
    discovered = shutil.which("signtool")
    if discovered:
        return discovered
    program_files = os.environ.get("ProgramFiles(x86)", "").strip()
    if not program_files:
        return None
    kits = Path(program_files) / "Windows Kits" / "10" / "bin"
    if kits.is_dir():
        candidates = sorted(kits.glob("*/x64/signtool.exe"), reverse=True)
        if candidates:
            return str(candidates[0])
    return None


def sign_executable(exe_path: Path, *, required: bool = False) -> bool:
    """Authenticode-sign when a certificate thumbprint is provided."""
    thumbprint = re.sub(r"\s+", "", os.environ.get("FOGLIGHT_SIGN_CERT_SHA1", ""))
    if not thumbprint:
        if required:
            raise RuntimeError(
                "production signing is required but FOGLIGHT_SIGN_CERT_SHA1 is unset"
            )
        print("[build] FOGLIGHT_SIGN_CERT_SHA1 is unset; executable remains unsigned")
        return False
    if not re.fullmatch(r"[A-Fa-f0-9]{40}", thumbprint):
        raise RuntimeError("FOGLIGHT_SIGN_CERT_SHA1 must be a 40-digit SHA-1 thumbprint")

    signtool = find_signtool()
    if not signtool:
        raise RuntimeError("FOGLIGHT_SIGN_CERT_SHA1 is set but signtool was not found")

    command = [
        signtool, "sign", "/sha1", thumbprint, "/fd", "SHA256",
        "/d", "Foglight", "/du", "https://github.com/aivrar/foglight",
    ]
    timestamp_url = os.environ.get("FOGLIGHT_TIMESTAMP_URL", "").strip()
    if timestamp_url:
        command.extend(["/tr", timestamp_url, "/td", "SHA256"])
    elif required:
        raise RuntimeError(
            "production signing requires an RFC 3161 FOGLIGHT_TIMESTAMP_URL"
        )
    command.append(str(exe_path))
    subprocess.run(command, cwd=ROOT, check=True)
    subprocess.run(
        [signtool, "verify", "/pa", "/v", str(exe_path)],
        cwd=ROOT,
        check=True,
    )
    return True


def write_checksum(exe_path: Path) -> Path:
    digest = hashlib.sha256()
    with exe_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum_path = exe_path.parent / "SHA256SUMS.txt"
    checksum_path.write_text(f"{digest.hexdigest()}  {exe_path.name}\n", encoding="ascii")
    return checksum_path


def build(*, require_signature: bool = False) -> None:
    if require_signature:
        # Fail before the expensive package build if production credentials or
        # tooling are not configured.
        thumbprint = re.sub(
            r"\s+", "", os.environ.get("FOGLIGHT_SIGN_CERT_SHA1", "")
        )
        if not thumbprint:
            raise RuntimeError(
                "production signing is required but FOGLIGHT_SIGN_CERT_SHA1 is unset"
            )
        if not re.fullmatch(r"[A-Fa-f0-9]{40}", thumbprint):
            raise RuntimeError(
                "FOGLIGHT_SIGN_CERT_SHA1 must be a 40-digit SHA-1 thumbprint"
            )
        if not os.environ.get("FOGLIGHT_TIMESTAMP_URL", "").strip():
            raise RuntimeError(
                "production signing requires an RFC 3161 FOGLIGHT_TIMESTAMP_URL"
            )
        if not find_signtool():
            raise RuntimeError("production signing requires signtool.exe")
    make_icon()
    make_version_info()
    write_spec()
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC_PATH)],
        cwd=ROOT,
        check=True,
    )
    exe_path = ROOT / "dist" / "Foglight.exe"
    if not exe_path.is_file():
        raise RuntimeError(f"PyInstaller did not produce {exe_path}")
    signed = sign_executable(exe_path, required=require_signature)
    checksum_path = write_checksum(exe_path)
    print(f"[build] Authenticode: {'verified' if signed else 'not signed'}")
    print(f"[build] wrote {checksum_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-signature",
        action="store_true",
        help="fail unless the executable is timestamped and passes signtool /pa verification",
    )
    arguments = parser.parse_args()
    build(require_signature=arguments.require_signature)
