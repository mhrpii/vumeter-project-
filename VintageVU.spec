# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: VintageVU.app (pencere surumu -- LCD'ye gondermez)
# KULLANIM: pyinstaller VintageVU.spec  ->  dist/VintageVU.app
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

cava_binary = ("cava", ".") if os.path.isfile("cava") else None

_sdl_candidates = [
    "/usr/local/lib/libSDL3.dylib",
    "/usr/local/opt/sdl2-compat/lib/libSDL2-2.0.0.dylib",
    "/opt/homebrew/lib/libSDL3.dylib",
    "/opt/homebrew/opt/sdl2-compat/lib/libSDL2-2.0.0.dylib",
]
sdl_binaries = [(p, ".") for p in _sdl_candidates if os.path.isfile(p)]

binaries = sdl_binaries[:]
if cava_binary:
    binaries.append(cava_binary)

pygame_datas, pygame_binaries, pygame_hidden = collect_all("pygame")
numpy_datas, numpy_binaries, numpy_hidden = collect_all("numpy")
psutil_datas, psutil_binaries, psutil_hidden = collect_all("psutil")

binaries += pygame_binaries + numpy_binaries + psutil_binaries
datas = pygame_datas + numpy_datas + psutil_datas

# Arka plan VU gorseli
if os.path.isfile("vu_bg.png"):
    datas.append(("vu_bg.png", "."))
if os.path.isfile("sysmon_window.py"):
    datas.append(("sysmon_window.py", "."))
if os.path.isfile("smc_reader.py"):
    datas.append(("smc_reader.py", "."))
if os.path.isfile("sysmon.py"):
    datas.append(("sysmon.py", "."))

a = Analysis(
    ["vumeter_mac.py"],
    pathex=[os.path.abspath('.')],
    binaries=binaries,
    datas=datas,
    hiddenimports=["numpy", "pygame", "psutil", "sysmon", "smc_reader", "sysmon_window"] + pygame_hidden + numpy_hidden + psutil_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["rumps", "objc", "Foundation", "AppKit"],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VintageVU",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="AppIcon.icns" if os.path.isfile("AppIcon.icns") else None,
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="VintageVU",
)

app = BUNDLE(
    coll,
    name="VintageVU.app",
    icon="AppIcon.icns" if os.path.isfile("AppIcon.icns") else None,
    bundle_identifier="com.local.vintagevu",
    info_plist={
        "NSMicrophoneUsageDescription": (
            "Vintage VU, ses girisini analiz edip canli spektrum "
            "gorsellestirmesi olusturmak icin ses karti erisimine gerek duyar."
        ),
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
        "LSUIElement": False,
    },
)
