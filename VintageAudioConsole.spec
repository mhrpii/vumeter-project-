# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec dosyasi: VintageAudioConsole.app
#
# KULLANIM (Mac'inde, ~/Downloads klasorunde, vumeter_trcc_mac.py ile AYNI klasorde):
#   pip3 install pyinstaller
#   pyinstaller VintageAudioConsole.spec
#
# Cikti: dist/VintageAudioConsole.app
#
# ONEMLI: pygame Homebrew'in 'sdl2-compat' paketini kullaniyor -- bu SDL2
# API'sini taklit eden ama gercekte SDL3'e bagimli bir katman. PyInstaller
# varsayilan olarak libSDL2'yi buluyor ama arkasindaki gercek libSDL3.dylib'i
# GOMMUYOR -- bu da "Failed loading SDL3 library" fatal hatasina sebep
# oluyordu. Asagida ikisini de acikca binaries listesine ekliyoruz.

import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# cava binary'sini app icine gomuyoruz (kullanicida brew olmasa da calissin diye).
cava_binary = ("cava", ".") if os.path.isfile("cava") else None

# SDL3 + sdl2-compat dylib'lerini bul (Homebrew Intel yolu varsayilan)
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

# pygame, numpy ve rumps (PyObjC tabanli menu bar) icin TUM native
# kutuphaneleri ve veri dosyalarini topla
pygame_datas, pygame_binaries, pygame_hidden = collect_all("pygame")
numpy_datas, numpy_binaries, numpy_hidden = collect_all("numpy")
psutil_datas, psutil_binaries, psutil_hidden = collect_all("psutil")
rumps_datas, rumps_binaries, rumps_hidden = collect_all("rumps")

binaries += pygame_binaries + numpy_binaries + rumps_binaries + psutil_binaries
datas = pygame_datas + numpy_datas + rumps_datas + psutil_datas
if os.path.isfile("menubar_icon.png"):
    datas.append(("menubar_icon.png", "."))
if os.path.isfile("vu_bg.png"):
    datas.append(("vu_bg.png", "."))
if os.path.isfile("sysmon.py"):
    datas.append(("sysmon.py", "."))
if os.path.isfile("smc_reader.py"):
    datas.append(("smc_reader.py", "."))

a = Analysis(
    ["vumeter_trcc_mac.py"],
    pathex=[os.path.abspath('.')],
    binaries=binaries,
    datas=datas,
    hiddenimports=["numpy", "pygame", "requests", "rumps", "psutil", "sysmon", "smc_reader", "psutil", "sysmon", "smc_reader",
                   "objc", "Foundation", "AppKit", "PyObjCTools"]
                  + pygame_hidden + numpy_hidden + rumps_hidden + psutil_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VintageAudioConsole",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # Terminal ciktisini gormek icin acik tutuyoruz; istersen False yap
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="AppIcon.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VintageAudioConsole",
)

app = BUNDLE(
    coll,
    name="VintageAudioConsole.app",
    icon="AppIcon.icns",
    bundle_identifier="com.local.vintageaudioconsole",
    info_plist={
        "NSMicrophoneUsageDescription": (
            "Vintage Audio Console, ses girisini analiz edip canlı "
            "spektrum gorsellestirmesi olusturmak icin mikrofon/ses "
            "kartı erisimine gerek duyar."
        ),
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
        "LSUIElement": False,
    },
)
