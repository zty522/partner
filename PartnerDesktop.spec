# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules


a = Analysis(
    ['partner_desktop_entry.py'],
    pathex=['E:\\work\\partner'],
    binaries=[],
    datas=[('partner\\assets', 'partner\\assets'), ('partner\\locales', 'partner\\locales'), ('partner\\events', 'partner\\events')],
    hiddenimports=collect_submodules('partner'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Partner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['partner\\assets\\partner_app_v2.ico'],
    version='windows_version_info.txt',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Partner',
)
