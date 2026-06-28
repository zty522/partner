"""Path mapping utilities for multi-environment workspace support.

Converts between different path formats:
- Windows: E:\\work\\partner_workspace
- WSL: /mnt/e/work/partner_workspace
- UNC: \\\\wsl$\\Ubuntu\\mnt\\e\\work\\partner_workspace
- SSH: /home/ubuntu/partner_workspace
"""

import os
import platform

ENVIRONMENT_TYPES = ["local_windows", "wsl_linux", "ssh_remote"]
ENVIRONMENT_LABELS = {
    "local_windows": "\U0001fa9f Windows",
    "wsl_linux": "\U0001f427 WSL Linux",
    "ssh_remote": "\u2601\ufe0f SSH \u8fdc\u7a0b",
}

def detect_current_environment() -> str:
    """Detect current GUI runtime environment."""
    if os.name == "nt":
        # Check if running inside WSL
        if os.path.exists("/mnt/c/") or platform.uname().release.lower().find("microsoft") >= 0:
            return "wsl_linux"
        return "local_windows"
    # Linux, could be native or WSL
    if platform.uname().release.lower().find("microsoft") >= 0:
        return "wsl_linux"
    return "ssh_remote"  # Assume SSH if not Windows and not WSL

def is_wsl_path(path: str) -> bool:
    """Check if path is a WSL Linux path (starts with /mnt/)."""
    return bool(path and path.replace("\\", "/").startswith("/mnt/"))

def is_windows_path(path: str) -> bool:
    """Check if path is a Windows path (has drive letter like C:\\)."""
    return bool(path and len(path) >= 2 and path[1] == ":")

def wsl_to_windows(path: str) -> str:
    """Convert /mnt/e/work -> E:\\work"""
    if not path:
        return path
    clean = path.replace("/", "\\").strip("\\")
    if clean.startswith("mnt\\"):
        drive = clean[4]
        rest = clean[5:]  # Include the backslash after drive letter
        return f"{drive.upper()}:{rest}"
    return path

def windows_to_wsl(path: str) -> str:
    """Convert E:\\work -> /mnt/e/work"""
    if not path:
        return path
    if len(path) >= 2 and path[1] == ":":
        drive = path[0].lower()
        rest = path[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return path

def to_unc_path(path: str, distro: str = "Ubuntu") -> str:
    """Convert /mnt/e/work -> \\\\wsl$\\Ubuntu\\mnt\\e\\work"""
    if not path.startswith("/mnt/"):
        return path
    sep = "\\"
    return f"\\\\wsl${distro}{sep}{path.replace('/', sep).lstrip(sep)}"

def display_path(path: str, env: str = "") -> str:
    """Return a human-readable display path for the given environment."""
    if not path:
        return ""
    current_env = env or detect_current_environment()
    if current_env == "local_windows" and is_wsl_path(path):
        return wsl_to_windows(path)
    if current_env == "wsl_linux" and is_windows_path(path):
        return windows_to_wsl(path)
    return path

def format_environment_tag(env: str) -> str:
    """Return a display tag for the environment."""
    return ENVIRONMENT_LABELS.get(env, env)

def infer_environment_from_path(path: str) -> str:
    """Infer the environment type from a path string."""
    if not path:
        return detect_current_environment()
    if is_windows_path(path):
        return "local_windows"
    if is_wsl_path(path):
        return "wsl_linux"
    if path.startswith("/home/") or path.startswith("/root/"):
        return "ssh_remote"
    return detect_current_environment()
