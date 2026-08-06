"""Remote Server Manager — SSH connection, env check, conda setup on remote servers.

Flow:
1. preflight says needs_server → user provides SSH info
2. LLM parses user reply → {host, user, key_path, port}
3. connect → check requirements (GPU, disk, conda) 
4. create work dir + conda env on server
5. dispatch tool execution on server
6. download results

Usage:
    from partner.agents.server import ServerManager
    mgr = ServerManager()
    ok = mgr.connect(host="gpu-server", user="zll", key_path="~/.ssh/id_rsa")
    mgr.check_requirements()  # check GPU, disk, conda
    mgr.setup_env("pocketflow", pip_packages=["torch==2.5.0", "rdkit-pypi"])
"""

import json, logging, os, re, subprocess, tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SSH_PARSE_PROMPT = """从用户消息中提取 SSH 登录信息。输出 JSON:
{user_message}

JSON: {{"host":"","user":"","port":22,"key_path":"","password":"","found":true/false}}
如果用户没有提供SSH信息, found=false
"""

REMOTE_CHECK_PROMPT = """你在远程服务器上运行了检测命令。根据输出判断是否满足运行条件。
## 需求
{requirements}

## 检测结果
{check_output}

输出 JSON: {{"ready":true/false,"missing":["缺什么"],"suggestions":["建议"]}}
"""


@dataclass
class ServerConfig:
    host: str = ""
    user: str = ""
    port: int = 22
    key_path: str = ""
    password: str = ""
    work_dir: str = "~/partner_work"
    valid: bool = False

    def ssh_base(self) -> list[str]:
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10"]
        if self.key_path:
            cmd += ["-i", os.path.expanduser(self.key_path)]
        if self.port != 22:
            cmd += ["-p", str(self.port)]
        cmd.append(f"{self.user}@{self.host}")
        return cmd


class ServerManager:
    """Manage remote server connections and tool execution."""

    def __init__(self):
        self._config: ServerConfig | None = None
        self._config_path = Path(os.path.expanduser("~/.partner/server_config.json"))

    def load_config(self) -> ServerConfig | None:
        if self._config_path.exists():
            try:
                d = json.loads(self._config_path.read_text())
                self._config = ServerConfig(**d)
                return self._config
            except Exception:
                pass
        return None

    def save_config(self):
        if self._config:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(json.dumps({
                "host": self._config.host,
                "user": self._config.user,
                "port": self._config.port,
                "key_path": self._config.key_path,
                "password": "",
                "work_dir": self._config.work_dir,
                "valid": self._config.valid,
            }, indent=2, ensure_ascii=False))

    async def parse_user_reply(self, user_message: str, adapter: Any) -> ServerConfig:
        """Use LLM to parse user's server info from a message."""
        prompt = SSH_PARSE_PROMPT.format(user_message=user_message[:2000])
        try:
            resp = adapter.chat(prompt, purpose="ssh_parse")
            data = json.loads(resp.strip())
            if data.get("found"):
                cfg = ServerConfig(
                    host=data.get("host", ""),
                    user=data.get("user", ""),
                    port=data.get("port", 22),
                    key_path=data.get("key_path", "~/.ssh/id_rsa"),
                    password=data.get("password", ""),
                    valid=True,
                )
                self._config = cfg
                self.save_config()
                return cfg
        except Exception as e:
            logger.warning("[SERVER] SSH parse failed: %s", e)
        return ServerConfig(valid=False)

    def connect(self, host="", user="", port=22, key_path="~/.ssh/id_rsa") -> bool:
        """Test SSH connection to server."""
        if host:
            self._config = ServerConfig(host=host, user=user, port=port, key_path=key_path)
        
        if not self._config or not self._config.valid:
            return False

        cmd = self._config.ssh_base() + ["echo CONNECTED"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            ok = "CONNECTED" in r.stdout
            if self._config:
                self._config.valid = ok
                self.save_config()
            return ok
        except Exception as e:
            logger.warning("[SERVER] connect failed: %s", e)
            return False

    def remote_run(self, command: str, timeout: int = 300) -> tuple[int, str, str]:
        """Run a command on the remote server. Returns (exit_code, stdout, stderr)."""
        if not self._config or not self._config.valid:
            return -1, "", "Not connected"
        cmd = self._config.ssh_base() + [command]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Timeout"
        except Exception as e:
            return -1, "", str(e)

    def check_requirements(self, needs_gpu=False, disk_gb=0, memory_gb=0) -> dict:
        """Check if remote server meets requirements."""
        if not self._config or not self._config.valid:
            return {"ready": False, "missing": ["未连接到服务器"]}

        results = {}
        missing = []
        suggestions = []

        # GPU check
        if needs_gpu:
            code, out, _ = self.remote_run("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo NO_GPU")
            if "NO_GPU" in out or code != 0:
                missing.append("GPU")
                suggestions.append("服务器无GPU或nvidia-smi不可用")
            else:
                results["gpu"] = out.strip()

        # Disk check
        if disk_gb > 0:
            code, out, _ = self.remote_run(f"df -BG $HOME | tail -1 | awk '{{print $4}}' | sed 's/G//'")
            try:
                available = float(out.strip())
                results["disk_free_gb"] = available
                if available < disk_gb:
                    missing.append(f"磁盘(需{disk_gb}GB,可用{available}GB)")
                else:
                    results["disk_ok"] = True
            except Exception:
                missing.append("磁盘(无法检测)")

        # Memory check
        if memory_gb > 0:
            code, out, _ = self.remote_run("free -g | awk '/^Mem:/{print $7}'")
            try:
                available = float(out.strip())
                results["mem_free_gb"] = available
                if available < memory_gb:
                    missing.append(f"内存(需{memory_gb}GB,可用{available}GB)")
                else:
                    results["mem_ok"] = True
            except Exception:
                pass

        # Conda check
        code, out, _ = self.remote_run("which conda 2>/dev/null || which mamba 2>/dev/null || echo NO_CONDA")
        if "NO_CONDA" in out:
            suggestions.append("服务器未安装conda, 建议先安装miniconda")

        ready = len(missing) == 0
        return {
            "ready": ready,
            "missing": missing,
            "suggestions": suggestions,
            "details": results,
        }

    def setup_work_dir(self, tool_name: str = "partner_work") -> str | None:
        """Create work directory on remote server."""
        if not self._config:
            return None
        work_dir = f"{self._config.work_dir}/{tool_name}"
        code, out, err = self.remote_run(f"mkdir -p {work_dir} && echo {work_dir}")
        if code == 0 and out.strip():
            return out.strip()
        return None

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload a file to the remote server via scp."""
        if not self._config or not self._config.valid:
            return False
        scp_cmd = ["scp", "-o", "StrictHostKeyChecking=no"]
        if self._config.key_path:
            scp_cmd += ["-i", os.path.expanduser(self._config.key_path)]
        if self._config.port != 22:
            scp_cmd += ["-P", str(self._config.port)]
        scp_cmd += [local_path, f"{self._config.user}@{self._config.host}:{remote_path}"]
        try:
            r = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            return r.returncode == 0
        except Exception:
            return False

    def download_results(self, remote_dir: str, local_dir: str) -> bool:
        """Download results from remote server via scp -r."""
        if not self._config or not self._config.valid:
            return False
        os.makedirs(local_dir, exist_ok=True)
        scp_cmd = ["scp", "-r", "-o", "StrictHostKeyChecking=no"]
        if self._config.key_path:
            scp_cmd += ["-i", os.path.expanduser(self._config.key_path)]
        if self._config.port != 22:
            scp_cmd += ["-P", str(self._config.port)]
        scp_cmd += [f"{self._config.user}@{self._config.host}:{remote_dir}/*", local_dir]
        try:
            r = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=300)
            return r.returncode == 0
        except Exception:
            return False

    async def full_setup(
        self, tool_name: str, adapter: Any,
        needs_gpu=False, disk_gb=0, memory_gb=0,
        conda_env=None, pip_packages=None,
    ) -> dict:
        """Full server setup: connect, check, create env, ready to run."""
        report = {"ready": False, "steps": []}

        # 1. Check connection
        if not self.connect():
            report["error"] = "无法连接到服务器，请检查SSH配置"
            return report
        report["steps"].append("ssh_connected")

        # 2. Check requirements
        req = self.check_requirements(needs_gpu, disk_gb, memory_gb)
        report["requirements"] = req
        if not req["ready"]:
            report["error"] = f"服务器不满足条件: {req['missing']}"
            report["suggestions"] = req.get("suggestions", [])
            return report
        report["steps"].append("requirements_ok")

        # 3. Create work dir
        work_dir = self.setup_work_dir(tool_name)
        if not work_dir:
            report["error"] = "无法创建工作目录"
            return report
        report["work_dir"] = work_dir
        report["steps"].append("work_dir_created")

        # 4. Setup conda env if needed
        if conda_env and pip_packages:
            # Create conda env
            code, out, err = self.remote_run(
                f"conda create -n {conda_env} python=3.10 -y 2>&1 | tail -3"
            )
            if "already exists" not in out and code != 0:
                report["error"] = f"创建conda环境失败: {err[-200:]}"
                return report
            report["steps"].append("conda_env_ready")

            # Install pip packages
            pkgs = " ".join(pip_packages)
            code, out, err = self.remote_run(
                f"source ~/miniconda3/etc/profile.d/conda.sh && "
                f"conda activate {conda_env} && "
                f"pip install {pkgs} 2>&1 | tail -5",
                timeout=600,
            )
            report["pip_result"] = out[-300:]
            report["steps"].append("pip_packages_installed")

        report["ready"] = True
        report["steps"].append("setup_complete")
        return report
