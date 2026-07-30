# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ["media_caption_tool_v3.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("assets/launch-im-aios.jpg", "assets"),
        ("assets/launch-qianyi.png", "assets"),
        ("assets/qianyi-app-icon.png", "assets"),
        ("assets/qianyi-app.ico", "assets"),
    ],
    hiddenimports=["_pillow_heif"],
    hookspath=[],
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
    name="MediaCaptionTool-3.3-Studio",
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
)
