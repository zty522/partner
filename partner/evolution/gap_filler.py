"""Gap Filler — C3 增强：缺口自动检测与自动补缺执行。

检测工具是否已就绪（external/tools/ 与 PATH），对支持自动下载的工具执行补缺，
不支持的给出明确的手动安装建议。所有补缺动作记录到 gap_fill_log.jsonl。
"""
import glob
import json
import logging
import os
import shutil
import subprocess
import urllib.request
import zipfile
import tarfile
from datetime import datetime

logger = logging.getLogger(__name__)

TOOLS_DIR = "/mnt/e/work/partner_workspace/external/tools"

# 工具 → 探测路径（相对 TOOLS_DIR 或 PATH）
_TOOL_PROBES: dict[str, list[str]] = {
    "plink": [os.path.join(TOOLS_DIR, "plink", "plink")],
    "iqtree": sorted(glob.glob(os.path.join(TOOLS_DIR, "iqtree", "*", "bin", "iqtree2"))),
    "bcftools": [os.path.join(TOOLS_DIR, "bcftools", "usr", "bin", "bcftools")],
    "samtools": [shutil.which("samtools") or ""],
    "mafft": [shutil.which("mafft") or ""],
    "muscle": [shutil.which("muscle") or ""],
    "seqkit": [shutil.which("seqkit") or ""],
    "prokka": [shutil.which("prokka") or ""],
}

# 已知工具的补缺来源：download 为可自动执行的官方二进制；manual 为需人工/需 sudo 的说明
_KNOWN_TOOL_SOURCES: dict[str, dict] = {
    "plink": {
        "download": ("https://s3.amazonaws.com/plink1-assets/plink_linux_x86_64_20240818.zip", "zip", "plink"),
        "manual": "apt install plink 或官方二进制 s3.amazonaws.com/plink1-assets/",
    },
    "iqtree": {
        "download": ("https://github.com/iqtree/iqtree2/releases/download/v2.4.0/iqtree-2.4.0-Linux-intel.tar.gz", "tgz", "iqtree"),
        "manual": "官方二进制 github.com/iqtree/iqtree2/releases",
    },
    "bcftools": {
        "manual": "apt download bcftools libhts3t64 + dpkg -x 到 external/tools/bcftools/（无需 sudo）",
    },
    "prokka": {
        "manual": "apt install prokka（需 sudo，依赖 perl/bioperl 生态）或 conda -c bioconda prokka；备选 bakta",
    },
}


def detect_tool(name: str) -> str:
    """检测工具是否已就绪。返回可执行文件路径；未找到返回空串。"""
    for p in _TOOL_PROBES.get(name, []):
        if p and os.path.exists(p):
            return p
    return ""


def detect_all() -> dict[str, str]:
    """检测所有已知工具。返回 {工具名: 路径或空}。"""
    return {name: detect_tool(name) for name in _TOOL_PROBES}


def _download_extract(url: str, kind: str, dest_sub: str) -> bool:
    """下载并解压官方二进制到 TOOLS_DIR/<dest_sub>/。"""
    os.makedirs(TOOLS_DIR, exist_ok=True)
    tmp = os.path.join(TOOLS_DIR, f"_dl_{dest_sub}.tmp")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    try:
        with opener.open(url, timeout=180) as resp, open(tmp, "wb") as f:
            f.write(resp.read())
        dest = os.path.join(TOOLS_DIR, dest_sub)
        os.makedirs(dest, exist_ok=True)
        if kind == "zip":
            with zipfile.ZipFile(tmp) as z:
                z.extractall(dest)
        elif kind == "tgz":
            with tarfile.open(tmp, "r:gz") as t:
                t.extractall(dest)
        os.remove(tmp)
        return True
    except Exception as exc:
        logger.warning("[GAP_FILLER] download failed for %s: %s", dest_sub, exc)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def fill_gap(workspace: str, tool_key: str) -> dict:
    """自动补缺：检测 → 自动下载（支持时）→ 记录。绝不抛异常。

    Returns: {ok, status: already_present|filled|manual_required|unsupported|download_failed,
              path, message, ts}
    """
    ts = datetime.now().isoformat(timespec="seconds")
    result = {"ok": False, "status": "unsupported", "path": "", "message": "", "ts": ts, "tool": tool_key}

    path = detect_tool(tool_key)
    if path:
        result.update({"ok": True, "status": "already_present", "path": path,
                       "message": f"{tool_key} 已就绪: {path}"})
        _record(result, workspace)
        return result

    src = _KNOWN_TOOL_SOURCES.get(tool_key, {})
    dl = src.get("download")
    if dl:
        url, kind, dest_sub = dl
        ok = _download_extract(url, kind, dest_sub)
        path = detect_tool(tool_key)
        if ok and path:
            result.update({"ok": True, "status": "filled", "path": path,
                           "message": f"{tool_key} 已自动下载安装: {path}"})
        else:
            result.update({"status": "download_failed",
                           "message": f"{tool_key} 下载失败；手动方式: {src.get('manual', '')}"})
    elif src.get("manual"):
        result.update({"status": "manual_required", "message": src["manual"]})
    else:
        result.update({"message": f"未知工具 {tool_key}，无法自动补缺"})

    _record(result, workspace)
    return result


def _record(result: dict, workspace: str) -> None:
    """追加补缺记录到 workspace state/logs/gap_fill_log.jsonl（失败不阻断）。"""
    try:
        from ..api_log import workspace_root_from_pointer

        root = workspace_root_from_pointer()
        if not root:
            return
        log_dir = os.path.join(root, "state", "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "gap_fill_log.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("[GAP_FILLER] record failed: %s", exc)
