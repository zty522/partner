"""Conda environment management + code execution for Partner."""
import os, subprocess, logging, json, time

logger = logging.getLogger(__name__)


def atomic_conda_env(ctx, params):
    """Create/manage conda environment. Returns {ok, env_name, action, output}.
    
    Params:
        action: create|install|list|activate
        env_name: str — environment name (default: 'partner_env')
        packages: list[str] — packages to install
    """
    try:
        action = params.get("action", "list")
        env_name = params.get("env_name", "partner_env")
        packages = params.get("packages", [])
        
        if action == "create":
            r = subprocess.run(
                ["conda", "create", "-n", env_name, "python=3.10", "-y"],
                capture_output=True, text=True, timeout=120
            )
            return {"ok": r.returncode == 0, "env_name": env_name, "action": "create", "output": r.stdout[-500:]}
        
        elif action == "install":
            cmd = ["conda", "install", "-n", env_name, "-y"] + packages
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            return {"ok": r.returncode == 0, "env_name": env_name, "action": "install", "packages": packages, "output": r.stdout[-500:]}
        
        elif action == "list":
            r = subprocess.run(["conda", "env", "list"], capture_output=True, text=True, timeout=10)
            return {"ok": True, "action": "list", "output": r.stdout}
        
        else:
            return {"ok": False, "error": f"unknown action: {action}"}
    
    except Exception as e:
        return {"ok": False, "error": str(e)}


def atomic_run_in_env(ctx, params):
    """Execute Python code in a specific conda environment.
    
    Params:
        env_name: str — conda environment name
        code: str — Python code to execute
        script: str — path to Python script (alternative to code)
        timeout: int — timeout seconds (default: 120)
    
    Returns {ok, exit_code, stdout, stderr}
    """
    try:
        env_name = params.get("env_name", "partner_env")
        code = params.get("code", "")
        script = params.get("script", "")
        timeout = params.get("timeout", 120)
        
        # Find conda env python
        conda_base = os.environ.get("CONDA_PREFIX", os.path.expanduser("~/miniconda3"))
        candidates = [
            os.path.join(conda_base, "envs", env_name, "bin", "python3"),
            os.path.join(conda_base, "envs", env_name, "bin", "python"),
            os.path.join(conda_base, "bin", "python3") if env_name == "base" else None,
            "/usr/bin/python3",
        ]
        env_python = None
        for c in candidates:
            if c and os.path.exists(c):
                env_python = c
                break
        if not env_python:
            return {"ok": False, "error": f"env not found: {env_name}, tried {candidates}"}
        
        if script and os.path.exists(script):
            r = subprocess.run([env_python, script], capture_output=True, text=True, timeout=timeout)
        elif code:
            r = subprocess.run([env_python, "-c", code], capture_output=True, text=True, timeout=timeout)
        else:
            return {"ok": False, "error": "no code or script"}
        
        return {
            "ok": r.returncode == 0,
            "exit_code": r.returncode,
            "stdout": r.stdout[-3000:],
            "stderr": r.stderr[-500:],
            "env_name": env_name,
        }
    
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
