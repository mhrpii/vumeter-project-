# -*- mode: python ; coding: utf-8 -*-
"""VumeterLCD - Windows LCD paneli .exe (PyInstaller)

Kullanim:
    py -3.12 -m PyInstaller VumeterLCD.spec --clean

Cikti: dist\\VumeterLCD.exe

GEREKSINIM (exe calisirken):
  - trcc kurulu olmali (C:\\Program Files\\TRCC) - exe onu cagirir
  - YONETICI hakki (uac_admin=True ile otomatik istenir): USB + sensorler icin
  - Kaspersky varsa: trcc-user klasoru + python/exe istisna listesine eklenmeli

NOT: console=True (hata gorunur). Sorunsuz calisinca False yapip yeniden derle.
"""
import glob as _glob
import os as _os

# LHM ve TUM bagimliliklari (sensorler icin) - klasordeki her .dll gomulur
_ALL_DLLS = [(p, '.') for p in _glob.glob('*.dll')]
print("PAKETE GOMULEN DLL'ler:", [_os.path.basename(p) for p, _ in _ALL_DLLS])

block_cipher = None

a = Analysis(
    ['native_proto_win.py'],
    pathex=[],
    binaries=_ALL_DLLS,
    datas=[
        # VU kadran arka planlari
        ('vu_bg.png', '.'),
        ('vu_bg2.png', '.'),
        ('vu_bg3.png', '.'),
        # runtime import edilen moduller
        ('control_window.py', '.'),
        ('sysmon_win.py', '.'),
    ],
    hiddenimports=[
        # ses (WASAPI loopback)
        'soundcard',
        'soundcard.mediafoundation',
        'cffi',
        '_cffi_backend',
        # sensorler (.NET kopru)
        'clr',
        'pythonnet',
        'clr_loader',
        # HTTP (trcc API)
        'requests',
        'urllib3',
        'charset_normalizer',
        'idna',
        'certifi',
        # runtime moduller
        'control_window',
        'sysmon_win',
        # arayuz
        'PyQt5.QtCore',
        'PyQt5.QtGui',
        'PyQt5.QtWidgets',
        'numpy',
        # multiprocessing (sender ayri surec)
        'multiprocessing',
        'multiprocessing.shared_memory',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas', 'tkinter', 'PySide2', 'PySide6'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='VumeterLCD',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # siyah pencere yok (stdout korumasi kodda)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,        # YONETICI (USB + sensorler icin sart)
)
