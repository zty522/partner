"""Partner File Operations — search, read, clone, conda, run code (Sprint 7)."""

import os, subprocess, glob, json, logging

logger = logging.getLogger(__name__)


def find_files(pattern: str, root: str = "/mnt/c/Users", max_depth: int = 4, limit: int = 50) -> dict:
    """Search for files by pattern across Windows filesystem."""
    results = []
    try:
        for depth in range(1, max_depth + 1):
            path_pattern = os.path.join(root, *(["*"] * depth), pattern)
            matches = glob.glob(path_pattern, recursive=False)
            for m in matches[:limit]:
                if os.path.isfile(m):
                    results.append({"path": m, "size": os.path.getsize(m)})
            if len(results) >= limit:
                break
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "count": len(results), "files": results[:limit]}


def find_directories(name: str, root: str = "/mnt/c/", limit: int = 30) -> dict:
    """Find directories by name."""
    results = []
    try:
        for dirpath, dirnames, _ in os.walk(root):
            for d in dirnames:
                if name.lower() in d.lower():
                    results.append(os.path.join(dirpath, d))
                    if len(results) >= limit:
                        return {"ok": True, "count": len(results), "dirs": results}
            if len(results) >= limit:
                break
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "count": len(results), "dirs": results}


def read_file_content(filepath: str, max_chars: int = 5000) -> dict:
    """Read file content safely."""
    try:
        if not os.path.isfile(filepath):
            return {"ok": False, "error": f"Not a file: {filepath}"}
        with open(filepath, errors='replace') as f:
            content = f.read(max_chars)
        return {"ok": True, "path": filepath, "size": os.path.getsize(filepath), "content": content}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def git_clone(repo_url: str, target_dir: str, branch: str = "") -> dict:
    """Clone a git repository."""
    try:
        os.makedirs(os.path.dirname(target_dir) or ".", exist_ok=True)
        cmd = ["git", "clone"]
        if branch:
            cmd += ["-b", branch]
        cmd += [repo_url, target_dir]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            file_count = sum(1 for _ in glob.glob(f"{target_dir}/**/*", recursive=True) if os.path.isfile(_))
            return {"ok": True, "dir": target_dir, "files": file_count, "output": r.stdout[-500:]}
        return {"ok": False, "error": r.stderr[-500:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Clone timed out (120s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def pip_install(packages: list, target_dir: str = "", venv: str = "") -> dict:
    """Install Python packages."""
    try:
        cmd = ["pip", "install", "--no-deps"] + packages
        if target_dir:
            cmd += ["-t", target_dir]
        env = os.environ.copy()
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-500:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_python_script(script_path: str, args: list = None, cwd: str = "", timeout: int = 300) -> dict:
    """Run a Python script with full logging."""
    try:
        cmd = ["python3", script_path] + (args or [])
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd or os.path.dirname(script_path), env=env)
        return {"ok": r.returncode == 0, "exit_code": r.returncode,
                "stdout": r.stdout[-2000:], "stderr": r.stderr[-1000:],
                "cwd": cwd or os.path.dirname(script_path)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout ({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def conda_env_exists(name: str) -> bool:
    try:
        r = subprocess.run(["conda", "env", "list", "--json"], capture_output=True, text=True, timeout=15)
        envs = json.loads(r.stdout).get("envs", [])
        return any(name in e for e in envs)
    except:
        return False


def conda_create(name: str, python_version: str = "3.10", packages: list = None) -> dict:
    try:
        if conda_env_exists(name):
            return {"ok": True, "env": name, "status": "already exists"}
        cmd = ["conda", "create", "-n", name, f"python={python_version}", "-y", "-q"]
        if packages:
            cmd += packages
        env = os.environ.copy()
        env["TMPDIR"] = "/tmp"
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
        return {"ok": r.returncode == 0, "env": name, "python": python_version, "error": r.stderr[-300:] if r.returncode != 0 else ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timeout (180s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def conda_install(env_name: str, packages: list) -> dict:
    try:
        cmd = ["conda", "install", "-n", env_name, "-y", "-q"] + packages
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return {"ok": r.returncode == 0, "output": r.stdout[-500:]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
