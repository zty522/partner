# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Partner Windows GUI — portable (no hardcoded paths)."""

import sys
from pathlib import Path

# Resolve Partner source root relative to this spec file's location
_partner_root = Path(SPECPATH).resolve().parent
entry_script = str(_partner_root / "scripts" / "partner_gui_entry.py")

a = Analysis(
    [entry_script],
    pathex=[str(_partner_root)],
    binaries=[],
    datas=[
        (str(_partner_root / 'partner' / 'locales'), 'partner/locales'),
        (str(_partner_root / 'partner' / 'desktop_gui' / 'assets'), 'partner/desktop_gui/assets'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'partner.desktop_gui.modern',
        'partner.desktop_gui.modern.main_window',
        'partner.desktop_gui.modern.theme',
        'partner.desktop_gui.modern.widgets',
        'partner.desktop_gui.modern.pages.chat',
        'partner.desktop_gui.modern.pages.settings',
        'partner.desktop_gui.modern.pages.instances',
        'partner.desktop_gui.modern.pages.agents',
        'partner.desktop_gui.modern.utils.path_mapper',
        'partner.desktop_gui.modern.utils.config_watcher',
        'partner.desktop_gui.gui_qt',
        'partner.setup',
        'partner.config',
        'partner.file_tools',
        'partner.workspace_layout',
        'partner.workspace_migration',
        'partner.outbound_policy',
        'partner.project_registry',
        'partner.project_state',
        'partner.mind.event_types',
        'partner.mind.harness',
        'partner.agent_config_sync',
        'partner.stage_report',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Icon path — relative to repo root
_icon_path = str(_partner_root / 'partner' / 'desktop_gui' / 'assets' / 'partner_app_v2.ico')

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
