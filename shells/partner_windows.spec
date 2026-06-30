# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Partner Windows GUI — restructured layout (shells/)."""

import sys
from pathlib import Path

# ── 使用 SPECPATH（PyInstaller 提供的变量） ──
_repo_root = Path(SPECPATH).resolve().parent
_shells_dir = _repo_root / "shells"

entry_script = str(_shells_dir / "partner_gui_entry.py")

# ── PySide6: 只包含实际使用的模块，排除大量无用模块（QtQml/QtQuick/Qt3D/...）
#     这能将 EXE 从 ~271MB 砍到 ~80MB，大幅缩短解压启动时间。
_PYSIDE6_USED = [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtNetwork',
    'PySide6.QtSvg',
    'PySide6.QtXml',
]
_PYSIDE6_EXCLUDED = [
    # QtQml / QtQuick 体系（最大的一坨）
    'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuick3D',
    'PySide6.QtQuickControls2', 'PySide6.QtQuickLayouts',
    'PySide6.QtQuickParticles', 'PySide6.QtQuickShapes',
    'PySide6.QtQuickTest', 'PySide6.QtQuickWidgets',
    'PySide6.QtQmlCompiler', 'PySide6.QtQmlCore',
    'PySide6.QtQmlDebug', 'PySide6.QtQmlDom',
    'PySide6.QtQmlImportScanner', 'PySide6.QtQmlLocalStorage',
    'PySide6.QtQmlModels', 'PySide6.QtQmlToolingSettings',
    'PySide6.QtQmlWorkerScript', 'PySide6.QtQmlXmlListModel',
    # Qt3D
    'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput',
    'PySide6.Qt3DLogic', 'PySide6.Qt3DExtras', 'PySide6.Qt3DAnimation',
    # 多媒体
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
    'PySide6.QtSpatialAudio',
    # Web
    'PySide6.QtWebChannel', 'PySide6.QtWebEngine',
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineQuick',
    'PySide6.QtWebEngineWidgets', 'PySide6.QtWebSockets',
    'PySide6.QtWebView',
    # 蓝牙/NFC/定位/传感器
    'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning',
    'PySide6.QtSensors', 'PySide6.QtSensorsQuick',
    # 其他不用
    'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
    'PySide6.QtGraphsWidgets', 'PySide6.QtGrpc',
    'PySide6.QtHelp', 'PySide6.QtHttpServer',
    'PySide6.QtNetworkAuth', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    'PySide6.QtScxml', 'PySide6.QtSerialPort', 'PySide6.QtSerialBus',
    'PySide6.QtShaderTools', 'PySide6.QtSpeech',
    'PySide6.QtSql', 'PySide6.QtSvgWidgets',
    'PySide6.QtTest', 'PySide6.QtTextToSpeech', 'PySide6.QtUiTools',
    'PySide6.QtVirtualKeyboard', 'PySide6.QtXmlPatterns',
    'PySide6.QtCharts', 'PySide6.QtDBus',
]

pyside6_hiddenimports = _PYSIDE6_USED[:]
pyside6_binaries = []
pyside6_datas = []

a = Analysis(
    [entry_script],
    pathex=[
        str(_repo_root),
        str(_shells_dir),
    ],
    binaries=[],
    datas=[
        (str(_repo_root / 'partner' / 'locales'), 'partner/locales'),
        (str(_shells_dir / 'frontend' / 'desktop_gui' / 'assets'), 'frontend/desktop_gui/assets'),
    ],
    hiddenimports=[
        *pyside6_hiddenimports,
        'shiboken6',
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
    excludes=[
        *_PYSIDE6_EXCLUDED,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

_icon_path = str(_shells_dir / 'frontend' / 'desktop_gui' / 'assets' / 'partner_app_v2.ico')

_splash_path = str(_shells_dir / 'frontend' / 'desktop_gui' / 'assets' / 'splash.bmp')

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
    splash=_splash_path,
)
