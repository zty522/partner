# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Partner Windows GUI — restructured layout (shells/)."""

import sys
from pathlib import Path

# Resolve Partner repo root relative to this spec file's location
_repo_root = Path(SPECPATH).resolve().parent  # shells/.. = repo root
_shells_dir = _repo_root / "shells"

# Entry script lives in shells/partner_gui_entry.py
entry_script = str(_shells_dir / "partner_gui_entry.py")

a = Analysis(
    [entry_script],
    pathex=[
        str(_repo_root),   # for partner package
        str(_shells_dir),  # for frontend.desktop_gui.*
    ],
    binaries=[],
    datas=[
        (str(_repo_root / 'partner' / 'locales'), 'partner/locales'),
        (str(_shells_dir / 'frontend' / 'desktop_gui' / 'assets'), 'frontend/desktop_gui/assets'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        # Core Partner package tree (needed for runtime imports)
        'partner',
        'partner.state',
        'partner.state.config',
        'partner.state.setup',
        'partner.monitoring',
        'partner.monitoring.instance_root',
        'partner.cli',
        'partner.cli.common',
        'partner.workspace',
        'partner.workspace.workspace_layout',
        'partner.file_tools',
        'partner.mind',
        'partner.mind.event_types',
        'partner.mind.harness',
        'partner.stage_report',
        # Modern GUI (in shells/frontend/desktop_gui/)
        'frontend.desktop_gui',
        'frontend.desktop_gui.modern',
        'frontend.desktop_gui.modern.main_window',
        'frontend.desktop_gui.modern.theme',
        'frontend.desktop_gui.modern.widgets',
        'frontend.desktop_gui.modern.pages.chat',
        'frontend.desktop_gui.modern.pages.settings',
        'frontend.desktop_gui.modern.pages.instances',
        'frontend.desktop_gui.modern.pages.agents',
        'frontend.desktop_gui.modern.utils.path_mapper',
        'frontend.desktop_gui.modern.utils.config_watcher',
        # Legacy GUI (still referenced)
        'frontend.desktop_gui.gui_qt',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Icon path — under shells/frontend/desktop_gui/assets/
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
