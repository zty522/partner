"""开源生物信息学工具集成层。

集成已安装在系统上的免费开源生信工具，通过统一 Python API 调用：
  - RDKit: 分子操作、分子指纹、相似度计算
  - BioPython: 序列分析、文件格式转换、NCBI 查询
  - CLI 工具: BLAST, seqkit, samtools, MUSCLE, MAFFT, HMMER, BWA, bowtie2

所有功能免费、开源、无 API key 需求。
"""

import logging
from typing import Any

from .bio_adapter import BioAdapter, BioResult
from .molecule_ops import MoleculeOps
from .sequence_ops import SequenceOps
from .alignment_ops import AlignmentOps

logger = logging.getLogger(__name__)

__all__ = [
    "BioAdapter",
    "BioResult",
    "MoleculeOps",
    "SequenceOps",
    "AlignmentOps",
    "dispatch",
]


def dispatch(
    task: str = "",
    tool: str = "",
    input: str | None = None,
    params: dict | None = None,
    **kwargs: Any,
) -> dict:
    """供 AgentDispatcher 调用的统一入口。

    Args:
        task: 自然语言任务描述
        tool: 子工具名 (molecule/sequence/alignment/blast/cli)
        input: 输入数据
        params: 工具特定参数
        **kwargs: 额外参数

    Returns:
        dict: 符合 AgentResult.output 格式的字典
    """
    try:
        merged = dict(params or {})
        merged.update(kwargs)
        if input:
            merged.setdefault("input", input)

        resolved_tool = tool or _resolve_tool_from_task(task)

        if not resolved_tool:
            return {
                "status": "error",
                "output": {"type": "text", "text": "未指定工具。可用: molecule, sequence, alignment, blast"},
                "error": "tool parameter required",
            }

        adapter = BioAdapter()
        result = adapter.run(resolved_tool, task=task, **merged)

        if result.ok:
            return {
                "status": "success",
                "output": {"type": "text", "text": result.text or result.summary()},
                "metadata": {
                    "tool": resolved_tool,
                    "method": result.method,
                    "extra": result.extra,
                },
            }
        else:
            return {
                "status": "error",
                "output": {"type": "text", "text": result.text or result.error},
                "error": result.error,
            }

    except Exception as exc:
        logger.exception("[Bioinformatics.dispatch] 未预期错误")
        return {
            "status": "error",
            "output": {"type": "text", "text": f"调用异常: {exc}"},
            "error": str(exc),
        }


def _resolve_tool_from_task(task: str) -> str:
    tl = task.lower()
    if any(k in tl for k in ("分子", "smiles", "mol", "rdkit", "指纹", "相似度")):
        return "molecule"
    if any(k in tl for k in ("序列", "fasta", "biopython", "翻译", "转录", "核苷酸", "氨基酸", "genbank")):
        return "sequence"
    if any(k in tl for k in ("比对", "alignment", "多序列", "muscle", "mafft", "blast", "同源")):
        return "alignment"
    if any(k in tl for k in ("sam", "bam", "samtools", "bwa", "比对到")):
        return "cli"
    return ""


# ── AgentDispatcher 兼容别名 ──
_dispatch = dispatch
