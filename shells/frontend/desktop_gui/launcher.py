"""Launcher for the modern Partner GUI.

Usage:
    python -m shells.frontend.desktop_gui.launcher
    python -m shells.frontend.desktop_gui.launcher --workspace /path/to/workspace
    partner desktop  (if cli command is configured)
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import tempfile
from pathlib import Path

# Ensure UTF-8 encoding for subprocess pipes
os.environ.setdefault("PYTHONUTF8", "1")


def detect_platform() -> str:
    """Detect the platform: 'windows', 'linux', or 'wsl'."""
    if os.name == "nt":
        return "windows"
    # Check if running inside WSL
    if os.path.exists("/proc/version"):
        try:
            with open("/proc/version", "r") as f:
                content = f.read().lower()
            if "microsoft" in content or "wsl" in content:
                return "wsl"
        except Exception:
            pass
    return "linux"


def setup_dpi_awareness():
    """Enable DPI awareness on Windows."""
    if os.name == "nt" or detect_platform() == "windows":
        try:
            # Windows 10+ per-monitor DPI awareness (v2)
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                # Fallback to system DPI awareness
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def find_workspace() -> str | None:
    """Find the Partner workspace using partner.setup.find_workspace()."""
    try:
        from partner.state.setup import find_workspace as _find_ws
        return _find_ws()
    except ImportError:
        pass

    # Fallback: try known paths
    from partner.monitoring.instance_root import resolve_partner_root
    root = str(resolve_partner_root())
    config_dir = os.path.join(root, "config")
    if os.path.exists(os.path.join(config_dir, "partner_config.json")):
        return root

    # Try environment variable
    ws = os.environ.get("PARTNER_WORKSPACE")
    if ws:
        if os.path.exists(os.path.join(ws, "config", "partner_config.json")):
            return ws
        if os.path.exists(os.path.join(ws, "partner_config.json")):
            return ws

    return None


def check_dual_installation() -> list[str]:
    """Check if both PySide6 and tkinter are available."""
    issues = []
    try:
        import PySide6  # noqa: F401
    except ImportError:
        issues.append("PySide6 未安装，请执行: pip install PySide6")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        issues.append("tkinter 未安装 (仅当 PySide6 不可用时需要)")
    return issues


MUTEX_NAME = "PartnerApp-SingleInstance-Mutex"


def _ensure_single_instance() -> bool:
    """Ensure only one instance runs. Kills old instances if found."""
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
            err = kernel32.GetLastError()
            if err == 183:  # ERROR_ALREADY_EXISTS
                # Kill old Partner.exe processes (except ourselves)
                os.system("taskkill /f /im Partner.exe 2>nul")
                return True  # Continue — old process is dead
            _ensure_single_instance._mutex = mutex
        except Exception:
            pass
    else:
        # Linux/WSL fallback: file lock
        import fcntl
        import tempfile
        lock_path = os.path.join(tempfile.gettempdir(), ".partner_gui.lock")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _ensure_single_instance._lock_fd = fd
        except (IOError, BlockingIOError):
            # Kill the old process
            os.system("pkill -f 'Partner' 2>/dev/null || true")
            return True
    return True


def launch_gui(workspace_path: str | None = None):
    """Launch the modern Partner GUI.

    Args:
        workspace_path: Optional explicit workspace path.
                        If None, auto-detects from partner.setup.
    """
    # Single-instance check (before QApplication)
    if not _ensure_single_instance():
        return

    # Check for issues
    issues = check_dual_installation()
    if issues:
        for issue in issues:
            print(f"⚠ {issue}")

    # Find workspace
    if workspace_path is None:
        workspace_path = find_workspace()

    if workspace_path:
        print(f"工作区: {workspace_path}")
    else:
        print("未找到工作区，将使用默认路径")
        from partner.monitoring.instance_root import resolve_partner_root
        workspace_path = str(resolve_partner_root())

    # Setup DPI awareness
    setup_dpi_awareness()

    # Import and launch
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Partner")
    app.setOrganizationName("Nous Research")
    app.setOrganizationDomain("nousresearch.com")

    # Set app icon
    icon_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "assets", "partner_app_v2.ico"
    )
    if os.path.exists(icon_path):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon_path))

    # Use Fusion style for consistent dark theme
    app.setStyle("Fusion")

    from .modern import ModernMainWindow
    window = ModernMainWindow(workspace_path=workspace_path, app=app)
    window.show()

    sys.exit(app.exec())


def main():
    """CLI entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description="Partner Desktop GUI - Modern PySide6 Interface"
    )
    parser.add_argument(
        "--workspace", "-w",
        type=str,
        default=None,
        help="Partner workspace path (auto-detected if not specified)"
    )
    parser.add_argument(
        "--version", "-v",
        action="store_true",
        help="Show version and exit"
    )

    args = parser.parse_args()

    if args.version:
        print("Partner Desktop GUI v2.0 (Modern)")
        print("Powered by PySide6 / Nous Research")
        return

    launch_gui(workspace_path=args.workspace)


if __name__ == "__main__":
    main()
