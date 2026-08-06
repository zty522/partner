"""CLI 生物信息学工具操作 — seqkit, samtools, bwa, fastqc 等。

提供统一的 Python API 封装系统上已安装的生信 CLI 工具。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import shlex
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 工具检测 ──────────────────────────────────────────────────────

CLI_TOOLS: dict[str, dict] = {
    "samtools": {
        "description": "SAM/BAM/CRAM 文件处理",
        "install_cmd": "conda install -c bioconda samtools",
        "version_cmd": "samtools --version | head -1",
        "tasks": ["bam统计", "bam排序", "bam索引", "bam标记重复", "bam合并"],
    },
    "seqkit": {
        "description": "FASTA/Q 序列文件处理",
        "install_cmd": "conda install -c bioconda seqkit",
        "version_cmd": "seqkit version",
        "tasks": ["序列统计", "格式转换", "序列筛选", "序列翻译"],
    },
    "bwa": {
        "description": "短序列比对工具",
        "install_cmd": "conda install -c bioconda bwa",
        "version_cmd": "bwa 2>&1 | head -1",
        "tasks": ["构建索引", "序列比对", "paired-end比对"],
    },
    "bowtie2": {
        "description": "短序列比对工具（快速）",
        "install_cmd": "conda install -c bioconda bowtie2",
        "version_cmd": "bowtie2 --version | head -1",
        "tasks": ["构建索引", "序列比对"],
    },
    "fastqc": {
        "description": "测序数据质量控制",
        "install_cmd": "conda install -c bioconda fastqc",
        "version_cmd": "fastqc --version",
        "tasks": ["fastq质量检查", "生成质控报告"],
    },
    "muscle": {
        "description": "多序列比对",
        "install_cmd": "conda install -c bioconda muscle",
        "version_cmd": "muscle -version 2>&1 | head -1",
        "tasks": ["多序列比对"],
    },
    "mafft": {
        "description": "快速多序列比对",
        "install_cmd": "conda install -c bioconda mafft",
        "version_cmd": "mafft --version 2>&1 | head -1",
        "tasks": ["多序列比对", "add比对"],
    },
    "blastp": {
        "description": "蛋白质序列搜索",
        "install_cmd": "conda install -c bioconda blast",
        "version_cmd": "blastp -version",
        "tasks": ["蛋白质同源搜索"],
    },
    "trimmomatic": {
        "description": "测序数据修剪",
        "install_cmd": "conda install -c bioconda trimmomatic",
        "version_cmd": "trimmomatic -version",
        "tasks": ["adaptor切除", "质量修剪"],
    },
    "bedtools": {
        "description": "基因组区间操作",
        "install_cmd": "conda install -c bioconda bedtools",
        "version_cmd": "bedtools --version",
        "tasks": ["区间交集", "区间合并", "bed格式转换"],
    },
}


def scan_installed_tools() -> dict[str, dict]:
    """扫描系统 PATH 中的生信工具，返回已安装和未安装的列表。"""
    installed = {}
    unavailable = {}
    for name, meta in CLI_TOOLS.items():
        found = shutil.which(name)
        if found:
            version = _get_tool_version(name, meta.get("version_cmd", ""))
            installed[name] = {
                "path": found,
                "version": version,
                "description": meta["description"],
            }
        else:
            unavailable[name] = {
                "install_cmd": meta["install_cmd"],
                "description": meta["description"],
            }
    return {"installed": installed, "unavailable": unavailable}


def _get_tool_version(name: str, version_cmd: str) -> str:
    """获取工具版本号。"""
    if not version_cmd:
        return "unknown"
    try:
        r = subprocess.run(shlex.split(version_cmd), capture_output=True, text=True, timeout=10)
        output = (r.stdout or r.stderr or "").strip()
        # Extract first meaningful line
        lines = [l.strip() for l in output.split("\n") if l.strip()]
        return lines[0][:80] if lines else "unknown"
    except Exception:
        return "unknown"


def _run_cli_tool(command: str, timeout: int = 120, workdir: str | None = None) -> dict:
    """运行 CLI 工具并返回结构化结果。"""
    parts = shlex.split(command)
    if not parts:
        return {"ok": False, "error": "空命令"}
    
    tool_name = parts[0]
    if not shutil.which(tool_name):
        meta = CLI_TOOLS.get(tool_name, {})
        install_hint = meta.get("install_cmd", f"conda install -c bioconda {tool_name}")
        return {"ok": False, "error": f"'{tool_name}' 未安装。安装: {install_hint}"}
    
    try:
        r = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir or os.getcwd(),
        )
        stdout = r.stdout.strip() if r.stdout else ""
        stderr = r.stderr.strip() if r.stderr else ""
        output = stdout
        if r.returncode != 0:
            output = stderr or stdout
        
        return {
            "ok": r.returncode == 0,
            "stdout": stdout[:10000],
            "stderr": stderr[:2000],
            "output": output[:10000],
            "returncode": r.returncode,
            "tool": tool_name,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"命令超时 (>={timeout}s): {command[:100]}", "tool": tool_name}
    except Exception as exc:
        return {"ok": False, "error": f"执行失败: {exc}", "tool": tool_name}


# ── 工具特定操作 ────────────────────────────────────────────────────

def samtools_stats(bam_path: str, timeout: int = 120) -> dict:
    """运行 samtools stats 获取 BAM 统计信息。"""
    return _run_cli_tool(f"samtools stats {shlex.quote(bam_path)}", timeout=timeout)


def samtools_flagstat(bam_path: str, timeout: int = 120) -> dict:
    """运行 samtools flagstat 获取比对统计。"""
    return _run_cli_tool(f"samtools flagstat {shlex.quote(bam_path)}", timeout=timeout)


def samtools_sort(input_bam: str, output_bam: str = "", timeout: int = 300) -> dict:
    """排序 BAM 文件。"""
    if not output_bam:
        base, ext = os.path.splitext(input_bam)
        output_bam = f"{base}.sorted{ext}"
    return _run_cli_tool(f"samtools sort -o {shlex.quote(output_bam)} {shlex.quote(input_bam)}", timeout=timeout)


def samtools_index(bam_path: str, timeout: int = 120) -> dict:
    """索引 BAM 文件。"""
    return _run_cli_tool(f"samtools index {shlex.quote(bam_path)}", timeout=timeout)


def seqkit_stats(fasta_path: str, timeout: int = 120) -> dict:
    """运行 seqkit stats 获取序列统计信息。"""
    return _run_cli_tool(f"seqkit stats {shlex.quote(fasta_path)}", timeout=timeout)


def seqkit_gc(fasta_path: str, timeout: int = 120) -> dict:
    """计算序列 GC 含量。"""
    return _run_cli_tool(f"seqkit fx2tab -n -l -g -G {shlex.quote(fasta_path)}", timeout=timeout)


def fastqc_report(fastq_path: str, output_dir: str = ".", timeout: int = 300) -> dict:
    """运行 FastQC 质控。"""
    return _run_cli_tool(f"fastqc -o {shlex.quote(output_dir)} {shlex.quote(fastq_path)}", timeout=timeout)


def bwa_index(reference: str, timeout: int = 600) -> dict:
    """构建 BWA 索引。"""
    return _run_cli_tool(f"bwa index {shlex.quote(reference)}", timeout=timeout)


def bwa_mem(reference: str, fastq1: str, fastq2: str = "", output_sam: str = "", threads: int = 4, timeout: int = 600) -> dict:
    """BWA-MEM 比对。"""
    if not output_sam:
        base = os.path.splitext(os.path.basename(fastq1))[0]
        output_sam = f"{base}.sam"
    cmd = f"bwa mem -t {threads} {shlex.quote(reference)} {shlex.quote(fastq1)}"
    if fastq2:
        cmd += f" {shlex.quote(fastq2)}"
    cmd += f" -o {shlex.quote(output_sam)}"
    return _run_cli_tool(cmd, timeout=timeout)


# ── 统一调度入口 ─────────────────────────────────────────────────────

def execute(task: str = "", **kwargs: Any) -> dict:
    """统一 CLI 工具执行入口。
    
    Args:
        task: 自然语言任务描述
        **kwargs: command=str (完整命令), tool=str (工具名), args=list
    
    Returns:
        dict with keys: ok, output, error, tool, returncode
    """
    command = kwargs.get("command") or task
    if command:
        return _run_cli_tool(command, timeout=kwargs.get("timeout", 120), workdir=kwargs.get("workdir"))
    
    tool = kwargs.get("tool", "")
    if tool == "samtools":
        subtask = kwargs.get("subtask", "stats")
        input_file = kwargs.get("input", "")
        if subtask == "stats":
            return samtools_stats(input_file)
        elif subtask == "flagstat":
            return samtools_flagstat(input_file)
        elif subtask == "sort":
            return samtools_sort(input_file, kwargs.get("output", ""))
        elif subtask == "index":
            return samtools_index(input_file)
    elif tool == "seqkit":
        subtask = kwargs.get("subtask", "stats")
        input_file = kwargs.get("input", "")
        if subtask == "stats":
            return seqkit_stats(input_file)
        elif subtask == "gc":
            return seqkit_gc(input_file)
    elif tool == "fastqc":
        return fastqc_report(kwargs.get("input", ""), kwargs.get("output_dir", "."))
    elif tool == "bwa":
        if kwargs.get("subtask") == "index":
            return bwa_index(kwargs.get("reference", ""))
        elif kwargs.get("subtask") == "mem":
            return bwa_mem(kwargs.get("reference", ""), kwargs.get("fastq1", ""), kwargs.get("fastq2", ""))
    
    # Auto-detect tool from task description
    tl = task.lower()
    for tool_name, meta in CLI_TOOLS.items():
        if any(kw in tl for kw in meta.get("tasks", [])):
            return _run_cli_tool(f"{tool_name} --help", timeout=30)
    
    return {"ok": False, "error": f"无法识别工具或命令。可用工具: {list(CLI_TOOLS.keys())}"}
