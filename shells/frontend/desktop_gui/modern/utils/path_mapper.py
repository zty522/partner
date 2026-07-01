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


def normalize_workspace_path(raw_path: str, target_env: str) -> tuple[str, list[str]]:
    """Normalize workspace path format to match target environment.

    Returns (normalized_path, warnings_list).

    Rules:
      target_env='wsl_linux' + raw='E:\\work\\ws'  → '/mnt/e/work/ws'
      target_env='local_windows' + raw='/mnt/e/work/ws'  → 'E:\\work\\ws'
      target_env='wsl_linux' + raw='/mnt/e/work/ws'  → unchanged
      target_env='ssh_remote' + raw='E:\\...' or '/mnt/...'  → error
    """
    warnings = []
    clean = raw_path.replace("\\\\", "/").strip().rstrip("/\\\\".strip())

    if target_env == "wsl_linux":
        # Convert Windows paths to /mnt/x/...
        if is_windows_path(clean):
            normalized = windows_to_wsl(clean)
            if clean[0].upper() == "C":
                warnings.append("⚠️ 系统盘 C: 可能不如数据盘适合存放工作区数据")
            return normalized, warnings
        if is_wsl_path(clean):
            return clean, warnings
        warnings.append(f"⚠️ 路径格式可能不受支持: {raw_path}")
        return clean, warnings

    elif target_env == "local_windows":
        # Convert /mnt/x/... to X:\\...
        if is_wsl_path(clean):
            return wsl_to_windows(clean), warnings
        if is_windows_path(clean):
            return clean, warnings
        warnings.append(f"❌ Linux 路径 {raw_path} 无法在 Windows 环境中直接访问")
        return raw_path, warnings

    elif target_env == "ssh_remote":
        # SSH paths must be absolute Linux paths
        if is_windows_path(clean) or is_wsl_path(clean):
            warnings.append(f"❌ 远程服务器不能使用本地路径 {raw_path}")
            return raw_path, warnings
        if clean.startswith("/"):
            return clean, warnings
        warnings.append(f"❌ 路径必须是绝对路径: {raw_path}")
        return raw_path, warnings

    # Fallback: try to detect
    detected = infer_environment_from_path(raw_path)
    if detected != target_env:
        warnings.append(f"⚠️ 路径格式与所选环境 ({target_env}) 不匹配")
    return raw_path, warnings


def environment_to_short_label(env: str) -> str:
    """Return short label for environment selector display."""
    return {
        "wsl_linux": "🐧 WSL Linux",
        "local_windows": "🪟 Windows",
        "ssh_remote": "☁️ 远程服务器",
    }.get(env, env)


def environment_to_tag(env: str) -> str:
    """Return short tag for instance display."""
    m = {"wsl_linux": "WSL", "local_windows": "Win", "ssh_remote": "SSH"}
    return m.get(env, env)
