#!/usr/bin/env python3
"""
Partner Tool Restriction Hook

This script disables research-related toolsets when the partner skill is active,
preventing Hermes from doing research itself (it should only pass tasks to Partner).

Usage:
  - Called by partner skill on load: python3 partner_restrict_tools.py disable
  - Called by partner skill on unload: python3 partner_restrict_tools.py enable

Disabled toolsets: web, browser, delegation
These are the tools Hermes would use to do research itself.
"""

import subprocess
import sys
import json

# Toolsets to disable when partner skill is active
RESTRICTED_TOOLSETS = ["web", "browser", "delegation"]

def disable_tools():
    """Disable research-related toolsets."""
    for toolset in RESTRICTED_TOOLSETS:
        try:
            result = subprocess.run(
                ["hermes", "tools", "disable", toolset],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                print(f"✅ Disabled toolset: {toolset}")
            else:
                print(f"⚠️ Failed to disable {toolset}: {result.stderr}")
        except Exception as e:
            print(f"❌ Error disabling {toolset}: {e}")

def enable_tools():
    """Re-enable research-related toolsets."""
    for toolset in RESTRICTED_TOOLSETS:
        try:
            result = subprocess.run(
                ["hermes", "tools", "enable", toolset],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                print(f"✅ Enabled toolset: {toolset}")
            else:
                print(f"⚠️ Failed to enable {toolset}: {result.stderr}")
        except Exception as e:
            print(f"❌ Error enabling {toolset}: {e}")

def main():
    if len(sys.argv) < 2:
        print("Usage: partner_restrict_tools.py [disable|enable]")
        sys.exit(1)
    
    action = sys.argv[1].lower()
    
    if action == "disable":
        print("🔒 Partner skill active: restricting research tools...")
        disable_tools()
    elif action == "enable":
        print("🔓 Partner skill inactive: restoring research tools...")
        enable_tools()
    else:
        print(f"Unknown action: {action}")
        sys.exit(1)

if __name__ == "__main__":
    main()
