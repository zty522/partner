"""Entry point for PyInstaller-built Partner Windows GUI."""
import sys
import os
import traceback

# Ensure the partner package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from frontend.desktop_gui.launcher import main
    sys.exit(main())
except Exception:
    # Show error in a message box before exiting
    tb = traceback.format_exc()
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, tb, "Partner 启动错误", 0x10)
    except Exception:
        pass
    print(tb, flush=True)
    sys.exit(1)
