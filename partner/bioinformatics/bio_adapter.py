"""BioAdapter — 统一调度层。

根据 tool 参数路由到具体的操作模块 (MoleculeOps / SequenceOps / AlignmentOps)。
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BioResult:
    """标准化的生信工具结果。"""
    ok: bool = False
    status: str = "error"
    text: str = ""
    data: Any = None
    error: str = ""
    method: str = ""  # python | cli_tool
    extra: dict = field(default_factory=dict)

    def summary(self) -> str:
        if self.text:
            return self.text
        return str(self.data)[:500] if self.data else self.error


class BioAdapter:
    """统一生物信息学适配器。

    根据 tool 类型分发到分子/序列/比对子模块。
    CLI 工具自动检测是否已安装，未安装时给出安装指引。
    """

    def __init__(self):
        self._molecule: Any = None  # lazy init
        self._sequence: Any = None
        self._alignment: Any = None

    def run(self, tool: str, task: str = "", **kwargs: Any) -> BioResult:
        handlers = {
            "molecule": self._run_molecule,
            "sequence": self._run_sequence,
            "alignment": self._run_alignment,
            "blast": self._run_blast,
            "cli": self._run_cli,
        }
        handler = handlers.get(tool)
        if not handler:
            return BioResult(ok=False, status="error", error=f"未知工具: {tool}，可用: {list(handlers.keys())}")
        return handler(task=task, **kwargs)

    def _run_molecule(self, task: str = "", **kwargs: Any) -> BioResult:
        if self._molecule is None:
            from .molecule_ops import MoleculeOps
            self._molecule = MoleculeOps()
        return self._molecule.execute(task=task, **kwargs)

    def _run_sequence(self, task: str = "", **kwargs: Any) -> BioResult:
        if self._sequence is None:
            from .sequence_ops import SequenceOps
            self._sequence = SequenceOps()
        return self._sequence.execute(task=task, **kwargs)

    def _run_alignment(self, task: str = "", **kwargs: Any) -> BioResult:
        if self._alignment is None:
            from .alignment_ops import AlignmentOps
            self._alignment = AlignmentOps()
        return self._alignment.execute(task=task, **kwargs)

    def _run_blast(self, task: str = "", **kwargs: Any) -> BioResult:
        if not shutil.which("blastp") and not shutil.which("blastn"):
            return BioResult(ok=False, status="error", error="BLAST 未安装。安装: conda install -c bioconda blast")
        from .alignment_ops import AlignmentOps
        if self._alignment is None:
            from .alignment_ops import AlignmentOps
            self._alignment = AlignmentOps()
        return self._alignment.run_blast(task=task, **kwargs)

    def _run_cli(self, task: str = "", **kwargs: Any) -> BioResult:
        """通用 CLI 工具调用（seqkit, samtools, bwa 等）。"""
        import subprocess
        import shlex

        cmd_line = kwargs.get("command") or kwargs.get("cmd") or task
        if not cmd_line:
            return BioResult(ok=False, status="error", error="未指定命令。需要 command 参数或自然语言描述任务")

        # 提取工具名检查是否安装
        tool_name = shlex.split(cmd_line)[0]
        if not shutil.which(tool_name):
            return BioResult(
                ok=False,
                status="error",
                error=f"'{tool_name}' 未找到。安装: conda install -c bioconda {tool_name}",
            )

        try:
            r = subprocess.run(
                shlex.split(cmd_line),
                capture_output=True,
                text=True,
                timeout=kwargs.get("timeout", 120),
            )
            output = (r.stdout or "") + (r.stderr or "")
            truncated = output[:5000]
            return BioResult(
                ok=r.returncode == 0,
                status="success" if r.returncode == 0 else "error",
                text=truncated,
                error=r.stderr[:500] if r.returncode != 0 else "",
                method="cli_tool",
            )
        except FileNotFoundError:
            return BioResult(ok=False, status="error", error=f"命令不存在: {tool_name}。请先安装")
        except subprocess.TimeoutExpired:
            return BioResult(ok=False, status="error", error=f"命令超时 (>{kwargs.get('timeout', 120)}s)")
        except Exception as exc:
            return BioResult(ok=False, status="error", error=f"执行失败: {exc}")

    def list_tools(self) -> list[dict]:
        """列出所有可用工具及其安装状态。"""
        tools = [
            {"name": "molecule", "description": "分子操作 — RDKit: 分子指纹、相似度、SMILES 校验", "type": "python"},
            {"name": "sequence", "description": "序列分析 — BioPython: 翻译、转录、文件格式转换", "type": "python"},
            {"name": "alignment", "description": "多序列比对 — MUSCLE, MAFFT", "type": "cli"},
            {"name": "blast", "description": "序列搜索 — BLAST (blastp/blastn)", "type": "cli"},
            {"name": "cli", "description": "通用 CLI 工具: seqkit, samtools, bwa, bowtie2 等", "type": "cli"},
        ]
        for t in tools:
            if t["type"] == "cli":
                cmds = {
                    "alignment": ["muscle", "mafft"],
                    "blast": ["blastp", "blastn"],
                    "cli": ["seqkit", "samtools", "bwa", "bowtie2"],
                }
                t["installed"] = [c for c in cmds.get(t["name"], []) if shutil.which(c)]
            else:
                t["installed"] = True
        return tools
