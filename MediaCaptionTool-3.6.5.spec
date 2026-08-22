# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

datas = [
    ("assets/launch-qianyi.png", "assets"),
    ("assets/qianyi-app-icon.png", "assets"),
    ("assets/qianyi-app.ico", "assets"),
    ("assets/nav-icons", "assets/nav-icons"),
    ("assets/provider-icons", "assets/provider-icons"),
]
for hero_asset in ("assets/launch-hero.jpg", "assets/launch-hero.png"):
    if Path(hero_asset).is_file():
        datas.append((hero_asset, "assets"))
if Path("assets/media").is_dir():
    datas.append(("assets/media", "assets/media"))

a = Analysis(
    ["media_caption_tool_v3.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["_pillow_heif", "tkinterdnd2", "imageio_ffmpeg"],
    hookspath=["."],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MediaCaptionTool-3.6.5-Studio",
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
    icon="assets/qianyi-app.ico",
    version="assets/qianyi-version-info.txt",
)
