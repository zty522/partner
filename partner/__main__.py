"""Support 'python -m partner' entry point (main module)."""
import sys
import os

# Set UTF-8 encoding for cross-platform compatibility
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from partner.cli import main
main()
