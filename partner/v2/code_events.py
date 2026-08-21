"""Code execution events for Partner Harness."""
import os, subprocess, logging

logger = logging.getLogger(__name__)


def atomic_write_code(ctx, params):
    """Write Python code to a file. Returns {ok, path}."""
    try:
        code = params.get("code", "")
        filename = params.get("filename", "script.py")
        out_dir = params.get("output_dir") or os.path.join(ctx.get("workspace", "/tmp"), "scripts")
        os.makedirs(out_dir, exist_ok=True)
        
        path = os.path.join(out_dir, filename)
        with open(path, "w") as f:
            f.write(code)
        
        logger.info("[CODE-EVENT] Written %d chars to %s", len(code), path)
        return {"ok": True, "path": path, "size": len(code)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def atomic_run_code(ctx, params):
    """Execute a Python script or inline code. Returns {ok, stdout, stderr, exit_code}."""
    try:
        script_path = params.get("script")
        code = params.get("code", "")
        timeout = params.get("timeout", 120)
        cwd = params.get("cwd") or ctx.get("workspace", "/tmp")
        
        if script_path and os.path.exists(script_path):
            r = subprocess.run(["python3", script_path], capture_output=True, text=True,
                              timeout=timeout, cwd=cwd)
        elif code:
            r = subprocess.run(["python3", "-c", code], capture_output=True, text=True,
                              timeout=timeout, cwd=cwd)
        else:
            return {"ok": False, "error": "no script or code provided"}
        
        logger.info("[CODE-EVENT] exit=%d stdout=%d stderr=%d", 
                   r.returncode, len(r.stdout), len(r.stderr))
        
        return {
            "ok": r.returncode == 0,
            "exit_code": r.returncode,
            "stdout": r.stdout[-2000:],
            "stderr": r.stderr[-500:],
            "stdout_len": len(r.stdout),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
