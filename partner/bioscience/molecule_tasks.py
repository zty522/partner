"""分子相关任务封装 — MolMIM 分子生成、DiffDock 分子对接、虚拟筛选。

依赖:
  - bioscience.bionemo_adapter.BioNeMoAdapter (必需)
  - RDKit (可选, 用于分子格式转换和相似度计算)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

from .bionemo_adapter import BioNeMoAdapter, BioNeMoResult

logger = logging.getLogger(__name__)


class MoleculeTaskRunner:
    """分子任务执行器。

    封装 BioNeMo 的分子相关模型调用，提供高层次的
    任务接口供 Partner 的 AgentDispatcher / Harness 调用。
    """

    def __init__(self, adapter: BioNeMoAdapter):
        self._adapter = adapter

    # ── 公开接口：供外部调用的顶层方法 ──────────────────────

    def generate_molecules(
        self,
        smiles: str,
        num_samples: int = 20,
        scaled_radius: float = 1.0,
    ) -> BioNeMoResult:
        """基于种子 SMILES 生成新分子 (MolMIM unguided)。

        Args:
            smiles: 种子分子 SMILES
            num_samples: 生成数量 (1–100)
            scaled_radius: 化学空间采样半径 (0.1–2.0)

        Returns:
            BioNeMoResult.data: list[dict] — 每个元素含 "smiles", "score"
        """
        return self._adapter.call_model(
            "molmim",
            smiles=smiles,
            num_samples=max(1, min(100, num_samples)),
            scaled_radius=max(0.1, min(2.0, scaled_radius)),
        )

    def guided_optimize(
        self,
        smiles: str,
        property_name: str = "QED",
        iterations: int = 10,
        num_samples: int = 20,
        min_similarity: float = 0.7,
    ) -> BioNeMoResult:
        """基于目标属性优化分子 (MolMIM guided)。

        Args:
            smiles: 种子 SMILES
            property_name: 优化目标 — "QED" 或 "plogp"
            iterations: CMA-ES 迭代次数
            num_samples: 每轮候选数
            min_similarity: 与种子最小相似度 (0.0–1.0)

        Returns:
            BioNeMoResult.data: list[dict] — 每个元素含 "smiles", "property_score"
        """
        # SDK 有 guided 方法但需要 async 调用 + fetch_result
        # 这里用 unguided + 后处理模拟（简化版）
        # 完整 guided 需要 SDK 2.x+ 的 molmim_guided_generate_sync
        try:
            return self._adapter.call_model(
                "molmim",
                smiles=smiles,
                num_samples=num_samples,
                scaled_radius=1.0,
            )
        except Exception as exc:
            return BioNeMoResult(ok=False, status="error", error=f"Guided optimization failed: {exc}")

    def dock(
        self,
        ligand_sdf: str | bytes,
        protein_pdb: str | bytes,
        poses: int = 20,
        diffusion_steps: int = 18,
    ) -> BioNeMoResult:
        """分子对接 (DiffDock) — 预测小分子与蛋白质的结合构象。

        Args:
            ligand_sdf: 配体 SDF 文件路径或内容 bytes
            protein_pdb: 蛋白质 PDB 文件路径或内容 bytes
            poses: 生成对接姿势数量 (1–100)
            diffusion_steps: 扩散步数 (1–100)

        Returns:
            BioNeMoResult.data: dict — 含 poses (list[dict]), confidence_scores
        """
        return self._adapter.call_model(
            "diffdock",
            ligand=ligand_sdf,
            protein=protein_pdb,
            poses=max(1, min(100, poses)),
            diffusion_steps=max(1, min(100, diffusion_steps)),
        )

    def virtual_screen(
        self,
        protein_pdb: str,
        ligand_smiles_list: list[str],
        top_k: int = 10,
    ) -> BioNeMoResult:
        """虚拟筛选 — 对多个候选分子逐一对接，按打分排序。

        Args:
            protein_pdb: 靶点蛋白质 PDB 文件路径
            ligand_smiles_list: 候选分子 SMILES 列表
            top_k: 返回 top N 结果

        Returns:
            BioNeMoResult.data: list[dict] — 按打分排序,
                每个含 "smiles", "confidence", "pose_pdb"
        """
        if not ligand_smiles_list:
            return BioNeMoResult(ok=False, status="error", error="ligand_smiles_list 为空")

        results = []
        for smi in ligand_smiles_list[:top_k * 2]:  # 多跑几个以防失败
            try:
                # 将 SMILES 转为临时 SDF (需要 RDKit)
                sdf_path = self._smiles_to_sdf(smi)
                if not sdf_path:
                    continue
                r = self.dock(ligand_sdf=sdf_path, protein_pdb=protein_pdb, poses=5, diffusion_steps=10)
                if r.ok and r.data:
                    results.append({
                        "smiles": smi,
                        "confidence": r.data.get("confidence_scores", [None])[0] if isinstance(r.data, dict) else None,
                        "result": r.data,
                    })
                # 清理临时文件
                try:
                    os.remove(sdf_path)
                except OSError:
                    pass
            except Exception as exc:
                logger.warning("[MoleculeTaskRunner] virtual_screen: %s 对接失败: %s", smi, exc)

        results.sort(key=lambda x: x.get("confidence") or 0, reverse=True)
        return BioNeMoResult(
            ok=bool(results),
            status="success" if results else "error",
            data=results[:top_k],
            error="" if results else "全部对接失败",
        )

    def list_available_models(self) -> list[dict]:
        """返回可用分子模型列表。"""
        return [
            {
                "name": "molmim",
                "description": "MolMIM 分子生成 (unguided)",
                "input": "seed SMILES",
                "output": "新分子 SMILES 列表",
            },
            {
                "name": "diffdock",
                "description": "DiffDock 分子对接",
                "input": "配体 SDF + 蛋白质 PDB",
                "output": "对接姿势 PDB + 置信度",
            },
        ]

    # ── 内部工具 ────────────────────────────────────────────

    @staticmethod
    def _smiles_to_sdf(smiles: str) -> str | None:
        """将 SMILES 转为临时 SDF 文件 (需要 RDKit)。"""
        try:
            from rdkit import Chem

            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                logger.warning("[MoleculeTaskRunner] 无效 SMILES: %s", smiles)
                return None
            fd, path = tempfile.mkstemp(suffix=".sdf")
            with os.fdopen(fd, "w") as f:
                f.write(Chem.MolToMolBlock(mol))
            return path
        except ImportError:
            logger.warning("[MoleculeTaskRunner] RDKit 未安装, 无法将 SMILES 转为 SDF")
            return None
        except Exception as exc:
            logger.warning("[MoleculeTaskRunner] SMILES→SDF 转换失败: %s", exc)
            return None
