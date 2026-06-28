"""WSL Bridge - connect Linux Partner to Windows filesystem."""

import os
import platform


def is_wsl() -> bool:
    """Detect if running in Windows Subsystem for Linux."""
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except (FileNotFoundError, PermissionError):
        return False


def is_windows() -> bool:
    return platform.system() == "Windows"


def is_mac() -> bool:
    return platform.system() == "Darwin"


def get_platform() -> str:
    """Return 'wsl', 'windows', 'linux', or 'mac'."""
    if is_wsl():
        return "wsl"
    if is_windows():
        return "windows"
    if is_mac():
        return "mac"
    return "linux"


def get_windows_drives() -> list:
    """Get available Windows drive mount points in WSL."""
    drives = []
    if not is_wsl():
        return drives
    
    for letter in "cdefghijklmnopqrstuvwxyz":
        mount = f"/mnt/{letter}"
        if os.path.isdir(mount):
            # Check if it has content
            try:
                items = os.listdir(mount)
                if items:
                    drives.append({
                        "letter": letter,
                        "mount": mount,
                        "label": f"{letter.upper()}:\\",
                    })
            except PermissionError:
                pass
    
    return drives


def get_windows_user_dirs() -> list:
    """Find common Windows user directories."""
    dirs = []
    if not is_wsl():
        return dirs
    
    # Try to find Users directory on C:
    users_dir = "/mnt/c/Users"
    if os.path.isdir(users_dir):
        for user in os.listdir(users_dir):
            user_path = os.path.join(users_dir, user)
            if os.path.isdir(user_path) and user not in ("Public", "Default", "Default User", "All Users"):
                # Check for common dirs
                common = {}
                for name in ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"]:
                    d = os.path.join(user_path, name)
                    if os.path.isdir(d):
                        common[name] = d
                if common:
                    dirs.append({
                        "user": user,
                        "home": user_path,
                        "dirs": common,
                    })
    
    return dirs


def search_windows_files(query: str, search_dirs: list = None, max_results: int = 20) -> list:
    """Search for files on Windows drives from WSL."""
    if not is_wsl():
        return []
    
    if not search_dirs:
        # Default: search Desktop and Documents of first user
        users = get_windows_user_dirs()
        if users:
            search_dirs = [
                users[0]["dirs"].get("Desktop", ""),
                users[0]["dirs"].get("Documents", ""),
                users[0]["dirs"].get("Downloads", ""),
            ]
        else:
            search_dirs = ["/mnt/c"]
    
    results = []
    query_lower = query.lower()
    
    for search_dir in search_dirs:
        if not search_dir or not os.path.isdir(search_dir):
            continue
        
        try:
            for root, dirs, files in os.walk(search_dir):
                # Skip hidden and system dirs
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
                    "node_modules", "__pycache__", ".git", "AppData", "Windows",
                    "$Recycle.Bin", "System Volume Information",
                )]
                
                for f in files:
                    if query_lower in f.lower():
                        full_path = os.path.join(root, f)
                        results.append({
                            "name": f,
                            "path": full_path,
                            "size": os.path.getsize(full_path) if os.path.exists(full_path) else 0,
                        })
                        if len(results) >= max_results:
                            return results
        except (PermissionError, OSError):
            continue
    
    return results


def read_windows_file(path: str) -> str:
    """Read a file from Windows filesystem."""
    if not os.path.exists(path):
        return f"File not found: {path}"
    
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"
