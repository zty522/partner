"""Tool/Agent Pre-flight Checker - reads README, checks requirements, decides local vs remote."""
import json, logging, os, re, shutil, subprocess
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
README_NAMES = ["README.md", "readme.md"]

EXTRACT_PROMPT = """分析以下工具的 README，提取运行需求。输出 JSON:
{readme}
JSON: {{"name":"","needs_gpu":bool,"gpu_reason":"","disk_estimate_gb":0,"needs_data_download":bool,"data_size_gb":0,"needs_model_checkpoint":bool,"needs_training":bool,"dependencies":[],"can_run_cpu":bool,"cpu_note":"","min_memory_gb":0}}
"""

@dataclass
class PreflightResult:
    tool_name: str = ""
    can_run_locally: bool = False
    needs_gpu: bool = False
    needs_server: bool = False
    needs_data_download: bool = False
    needs_model: bool = False
    needs_training: bool = False
    disk_estimate_gb: float = 0
    data_size_gb: float = 0
    can_run_cpu: bool = True
    missing_locally: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    user_message: str = ""
    raw_extraction: dict = field(default_factory=dict)

def read_readme(tool_dir):
    for name in README_NAMES:
        p = os.path.join(tool_dir, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read()[:10000]
    return None

def _check_local_gpu():
    try:
        if shutil.which("nvidia-smi"):
            r = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10)
            return r.returncode == 0 and "GPU" in r.stdout
    except: pass
    return False

def _check_local_disk_gb(path="/"):
    try:
        s = os.statvfs(path)
        return (s.f_bavail * s.f_frsize) / (1024**3)
    except: return 999

def _check_local_memory_gb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if "MemAvailable" in line:
                    return int(line.split()[1]) / (1024**2)
    except: pass
    return 0

async def run_preflight(tool_name, tool_dir, adapter):
    """Read README, extract requirements via LLM, check local env, return result."""
    result = PreflightResult(tool_name=tool_name)
    readme = read_readme(tool_dir)
    if not readme:
        result.can_run_locally = True
        result.user_message = f"[预检] {tool_name}: 无README, 尝试直接运行"
        return result
    
    prompt = EXTRACT_PROMPT.format(readme=readme[:8000])
    try:
        resp = adapter.chat(prompt, purpose="preflight")
        if resp:
            resp = resp.strip()
            if resp.startswith("```"):
                import re
                resp = re.sub(r"^```[a-zA-Z]*\n", "", resp)
                resp = re.sub(r"\n```$", "", resp)
            reqs = json.loads(resp)
            result.raw_extraction = reqs
        else:
            reqs = {}
    except Exception as e:
        logger.warning("[PREFLIGHT] extract failed: %s", e)
        reqs = {}
    
    if not reqs:
        result.can_run_locally = True
        result.user_message = f"[预检] {tool_name}: 无法解析README, 尝试直接运行"
        return result
    
    result.needs_gpu = reqs.get("needs_gpu", False)
    result.needs_data_download = reqs.get("needs_data_download", False)
    result.needs_model = reqs.get("needs_model_checkpoint", False)
    result.needs_training = reqs.get("needs_training", False)
    result.disk_estimate_gb = float(reqs.get("disk_estimate_gb", 0) or reqs.get("data_size_gb", 0) or 0)
    result.data_size_gb = float(reqs.get("data_size_gb", 0))
    result.can_run_cpu = reqs.get("can_run_cpu", True)
    
    has_gpu = _check_local_gpu()
    disk_free = _check_local_disk_gb()
    mem_free = _check_local_memory_gb()
    
    missing = []
    recs = []
    
    if result.needs_gpu and not has_gpu:
        if not result.can_run_cpu:
            missing.append("GPU")
            result.needs_server = True
            recs.append(f"需要GPU: {reqs.get('gpu_reason','')}")
        else:
            recs.append(f"推荐GPU, CPU可运行: {reqs.get('cpu_note','')}")
    
    if result.disk_estimate_gb > 0 and disk_free < result.disk_estimate_gb:
        missing.append(f"磁盘(需{result.disk_estimate_gb:.0f}GB,可用{disk_free:.0f}GB)")
        if result.disk_estimate_gb > 10:
            result.needs_server = True
    
    if result.needs_data_download:
        recs.append(f"需下载数据(~{result.data_size_gb}GB)")
    if result.needs_model:
        recs.append(f"需模型: {reqs.get('model_description','')}")
    if result.needs_training:
        recs.append(f"需训练: {reqs.get('training_description','')}")
    
    result.missing_locally = missing
    result.recommendations = recs
    result.can_run_locally = len(missing) == 0
    
    if result.can_run_locally:
        parts = [f"[预检] {tool_name}: 可本地运行"]
        if recs: parts.extend(f"  - {r}" for r in recs)
        result.user_message = "\n".join(parts)
    else:
        parts = [f"[预检] {tool_name}: 无法本地运行"]
        parts.extend(f"  缺少: {m}" for m in missing)
        if result.needs_server:
            parts.append("\n需要远程服务器(提供 host user key_path)")
        if recs: parts.extend(f"  - {r}" for r in recs)
        result.user_message = "\n".join(parts)
    
    return result
