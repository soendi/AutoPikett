# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Spec für AutoPikett.

--onedir (kein onefile): vermeidet Temp-Ordner-Probleme, und der Installer
ersetzt so nur den Programmordner.
"""

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/AutoPikett.ico', 'assets'),
    ],
    hiddenimports=[
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL._tkinter_finder',
        'pywinauto',
        'comtypes',
        'ctypes.wintypes',
        'tkinter',
        'tkinter.ttk',
        'autopikett',
        'autopikett.core',
        'autopikett.dialer',
        'autopikett.tray',
        'autopikett.app',
    ],
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
    name='AutoPikett',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/AutoPikett.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='AutoPikett',
)
