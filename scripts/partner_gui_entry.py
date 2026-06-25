"""Entry point for PyInstaller-built Partner Windows GUI."""
import sys
import os

# Ensure the partner package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from partner.desktop_gui.launcher import main
sys.exit(main())
