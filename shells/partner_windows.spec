# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Partner Windows GUI — restructured layout (shells/)."""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

# ── 使用 SPECPATH（PyInstaller 提供的变量） ──
_repo_root = Path(SPECPATH).resolve().parent
_shells_dir = _repo_root / "shells"

entry_script = str(_shells_dir / "partner_gui_entry.py")

# ── Collect all PySide6 dependencies ──
try:
    pyside6_datas, pyside6_binaries, pyside6_hiddenimports = collect_all('PySide6')
except Exception:
    pyside6_datas = []
    pyside6_binaries = []
    pyside6_hiddenimports = [
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtSvg',
        'PySide6.QtPrintSupport',
        'PySide6.QtXml',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'shiboken6',
    ]

a = Analysis(
    [entry_script],
    pathex=[
        str(_repo_root),
        str(_shells_dir),
    ],
    binaries=pyside6_binaries,
    datas=[
        (str(_repo_root / 'partner' / 'locales'), 'partner/locales'),
        (str(_shells_dir / 'frontend' / 'desktop_gui' / 'assets'), 'frontend/desktop_gui/assets'),
        *pyside6_datas,
    ],
    hiddenimports=[
        *pyside6_hiddenimports,
        'partner.state',
        'partner.state.config',
        'partner.state.setup',
        'partner.state.state',
        'partner.state.state_persistence',
        'partner.monitoring',
        'partner.monitoring.instance_root',
        'partner.monitoring.runtime_monitor',
        'partner.workspace',
        'partner.workspace.workspace_layout',
        'partner.cli',
        'partner.cli.common',
        'partner.file_tools',
        'frontend.desktop_gui',
        'frontend.desktop_gui.modern',
        'frontend.desktop_gui.modern.main_window',
        'frontend.desktop_gui.modern.theme',
        'frontend.desktop_gui.modern.widgets',
        'frontend.desktop_gui.modern.pages',
        'frontend.desktop_gui.modern.pages.chat',
        'frontend.desktop_gui.modern.pages.instances',
        'frontend.desktop_gui.modern.pages.settings',
        'frontend.desktop_gui.modern.pages.agents',
        'frontend.desktop_gui.modern.pages.setup_wizard',
        'frontend.desktop_gui.modern.utils.path_mapper',
        'frontend.desktop_gui.modern.utils.config_watcher',
        'frontend.desktop_gui.modern.utils.local_config',
        'frontend.desktop_gui.gui_qt',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

_icon_path = str(_shells_dir / 'frontend' / 'desktop_gui' / 'assets' / 'partner_app_v2.ico')

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Partner',
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
    icon=_icon_path,
)
