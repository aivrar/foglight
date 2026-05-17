#!/usr/bin/env python3
"""Build the single-file Windows Foglight executable."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent
ASSET_DIR = ROOT / "assets"
ICON_PATH = ASSET_DIR / "foglight.ico"
PNG_ICON_PATH = ASSET_DIR / "foglight-icon.png"
SPEC_PATH = ROOT / "foglight_native.spec"


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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={str(ICON_PATH)!r},
)
"""
    SPEC_PATH.write_text(spec, encoding="utf-8")


def build() -> None:
    make_icon()
    write_spec()
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC_PATH)],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    build()
