"""Conda Environment Manager — auto check, create, activate conda environments.

Partner needs isolated conda envs for different tools.
Usage:
    from partner.utils.conda_manager import CondaEnvManager
    mgr = CondaEnvManager()
    python_bin = mgr.ensure("pocketflow", pip_packages=["torch==2.5.0", "rdkit-pypi"])
"""

import json, logging, os, shutil, subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
_PROXY_VARS = ["http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]
_CONFIG_PATH = Path(os.path.expanduser("~/.partner/conda_envs.json"))

def _clean_env():
    env = os.environ.copy()
    for v in _PROXY_VARS:
        env.pop(v, None)
    return env

def _load_config():
    try:
        return json.loads(_CONFIG_PATH.read_text()) if _CONFIG_PATH.exists() else {}
    except Exception:
        return {}

def _save_config(cfg):
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

class CondaEnvManager:

    def __init__(self):
        self._conda_bin = shutil.which("conda") or os.path.expanduser("~/miniconda3/bin/conda")

    def _run(self, cmd, timeout=120):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_clean_env())
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def exists(self, name):
        code, out, _ = self._run([self._conda_bin, "env", "list", "--json"], timeout=30)
        if code != 0:
            return any(os.path.isdir(p) for p in [
                os.path.expanduser(f"~/miniconda3/envs/{name}"),
                f"/home/os/conda/{name}"])
        try:
            return any(name == os.path.basename(p) for p in json.loads(out).get("envs", []))
        except Exception:
            return False

    def python_path(self, name):
        for p in [os.path.expanduser(f"~/miniconda3/envs/{name}/bin/python"),
                  f"/home/os/conda/{name}/bin/python"]:
            if os.path.isfile(p): return p
        return None

    def create(self, name, python_version="3.10"):
        logger.info("[CONDA] creating env '%s'", name)
        code, _, err = self._run([self._conda_bin, "create", "-n", name, f"python={python_version}", "-y"], timeout=300)
        return code == 0

    def pip_install(self, name, packages, index_url=""):
        python = self.python_path(name)
        if not python: return False
        cmd = [python, "-m", "pip", "install"] + packages
        if index_url: cmd += ["-i", index_url]
        code, out, _ = self._run(cmd, timeout=600)
        return code == 0 or "Successfully installed" in out

    def ensure(self, name, python_version="3.10", pip_packages=None, pip_index=""):
        if not self.exists(name) and not self.create(name, python_version):
            return None
        python = self.python_path(name)
        if not python: return None
        if pip_packages:
            code, out, _ = self._run([python, "-m", "pip", "list", "--format=json"], timeout=30)
            installed = set()
            if code == 0:
                try: installed = {p["name"].lower() for p in json.loads(out)}
                except Exception: pass
            missing = [p for p in pip_packages if p.split("==")[0].split(">=")[0].lower() not in installed]
            if missing:
                self.pip_install(name, missing, pip_index)
        return python

    def list_envs(self):
        code, out, _ = self._run([self._conda_bin, "env", "list", "--json"], timeout=30)
        if code != 0: return []
        try: return [os.path.basename(p) for p in json.loads(out).get("envs", [])]
        except Exception: return []

def get_env_python(name, pip_packages=None):
    return CondaEnvManager().ensure(name, pip_packages=pip_packages)

if __name__ == "__main__":
    mgr = CondaEnvManager()
    print("envs:", mgr.list_envs())
    print("pocketflow exists:", mgr.exists("pocketflow"))
    print("python:", mgr.python_path("pocketflow"))
