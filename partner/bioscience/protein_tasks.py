"""蛋白质相关任务封装 — ESMFold 结构预测、OpenFold 折叠、
ESM-2 嵌入提取。

依赖:
  - bioscience.bionemo_adapter.BioNeMoAdapter
"""

from __future__ import annotations

import logging
from typing import Any

from .bionemo_adapter import BioNeMoAdapter, BioNeMoResult

logger = logging.getLogger(__name__)


class ProteinTaskRunner:
    """蛋白质任务执行器。"""

    def __init__(self, adapter: BioNeMoAdapter):
        self._adapter = adapter

    # ── 公开接口 ────────────────────────────────────────────

    def predict_structure_esmfold(
        self,
        sequence: str,
    ) -> BioNeMoResult:
        """ESMFold 快速蛋白质结构预测。

        适合短序列 (<1024 aa)，快速 (5–10 秒)。

        Args:
            sequence: 氨基酸序列 (1–1024 字符)

        Returns:
            BioNeMoResult.data: dict — 含 pdb_string (str), plddt (list[float])
        """
        if len(sequence) > 1024:
            return BioNeMoResult(
                ok=False, status="error",
                error=f"ESMFold 最大支持 1024 aa, 输入 {len(sequence)} aa",
            )
        return self._adapter.call_model("esmfold", sequence=sequence)

    def predict_structure_openfold(
        self,
        sequence: str,
        msas: list[str] | None = None,
        use_msa: bool = True,
        relax: bool = True,
    ) -> BioNeMoResult:
        """OpenFold 高精度蛋白质结构预测。

        更准确但慢 (2–10 分钟)，支持多序列比对输入。

        Args:
            sequence: 氨基酸序列 (1–2000 字符)
            msas: MSA 文件路径列表 (.a3m 格式)
            use_msa: 是否使用 MSA
            relax: 是否执行结构弛豫

        Returns:
            BioNeMoResult.data: dict — 含 pdb_string (str), plddt (list[float])
        """
        if len(sequence) > 2000:
            return BioNeMoResult(
                ok=False, status="error",
                error=f"OpenFold 最大支持 2000 aa, 输入 {len(sequence)} aa",
            )
        return self._adapter.call_model(
            "openfold",
            sequence=sequence,
            msas=msas,
            use_msa=use_msa,
            relax=relax,
        )

    def get_embeddings_esm2(
        self,
        sequences: list[str],
        model_size: str = "650m",
    ) -> BioNeMoResult:
        """ESM-2 蛋白质嵌入提取。

        将蛋白质序列转为高维向量表示，用于下游聚类/分类/相似度搜索。

        Args:
            sequences: 蛋白质序列列表 (每条 ≤1024 aa)
            model_size: 模型大小 — "650m", "3b", "15b"

        Returns:
            BioNeMoResult.data: list[dict] — 每个元素含
                "representations" (np.array), "tokens", "logits"
        """
        return self._adapter.call_model(
            "esm2",
            sequences=sequences,
            model_size=model_size,
        )

    def list_available_models(self) -> list[dict]:
        """返回可用蛋白质模型列表。"""
        return [
            {
                "name": "esmfold",
                "description": "ESMFold 快速蛋白质结构预测 (5–10s)",
                "input": "氨基酸序列",
                "output": "PDB 结构 + pLDDT",
            },
            {
                "name": "openfold",
                "description": "OpenFold 高精度蛋白质折叠 (2–10min)",
                "input": "氨基酸序列 + 可选 MSA",
                "output": "PDB 结构 + pLDDT",
            },
            {
                "name": "esm2",
                "description": "ESM-2 蛋白质嵌入 (650M/3B/15B)",
                "input": "蛋白质序列列表",
                "output": "嵌入向量矩阵",
            },
        ]
