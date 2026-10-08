# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [("../momo.lua", "."), ("models", "models")]
binaries = []
hiddenimports = ["sounddevice", "soundfile", "pydub", "tkinter", "tkinter.ttk"]

for package in ("audio_separator", "librosa", "torch"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    ["../momo.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MoMo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MoMo",
)
app = BUNDLE(
    coll,
    name="MoMo.app",
    icon="MoMo.icns",
    bundle_identifier="com.momo.player",
    info_plist={
        "CFBundleName": "MoMo",
        "CFBundleDisplayName": "MoMo",
        "CFBundleIconFile": "MoMo",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Video",
                "CFBundleTypeRole": "Viewer",
                "CFBundleTypeExtensions": ["mkv", "mp4"],
                "LSHandlerRank": "Alternate",
            }
        ],
    },
)
