# -*- mode: python ; coding: utf-8 -*-
"""VintageAudioConsole - Windows MASAUSTU .exe

Kullanim (NORMAL PowerShell, yonetici degil):
    py -3.12 -m PyInstaller VumeterWin.spec --clean

Cikti: dist\VintageAudioConsole.exe

uac_admin=True -> calisirken yonetici ister (sensorler icin sart).
console=False  -> siyah terminal penceresi acilmaz (stdout korumasi kodda var).
"""
import glob as _glob
import os as _os

# LHM + tum bagimliliklari (sensorler icin)
_ALL_DLLS = [(p, '.') for p in _glob.glob('*.dll')]
print("PAKETE GOMULEN DLL'ler:", [_os.path.basename(p) for p, _ in _ALL_DLLS])

block_cipher = None

a = Analysis(
    ['vumeter_win.py'],
    pathex=[],
    binaries=_ALL_DLLS,
    datas=[
        ('vu_bg.png', '.'),
        ('vu_bg2.png', '.'),
        ('vu_bg3.png', '.'),
        ('control_window_desktop.py', '.'),
        ('sysmon_window.py', '.'),
        ('sysmon_win.py', '.'),
    ],
    hiddenimports=[
        'soundcard', 'soundcard.mediafoundation', 'cffi', '_cffi_backend',
        'pycaw', 'pycaw.pycaw', 'pycaw.utils',
        'comtypes', 'comtypes.client', 'comtypes.stream',
        'clr', 'pythonnet', 'clr_loader',
        'control_window_desktop', 'sysmon_window', 'sysmon_win',
        'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets',
        'numpy',
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
    name='VintageAudioConsole',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # siyah pencere yok (stdout korumasi kodda)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,         # yonetici (sensorler icin)
)
