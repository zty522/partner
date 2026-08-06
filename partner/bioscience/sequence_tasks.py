"""序列分析任务封装 — ESM-1nv 嵌入、基因组注释、
多序列比对 (MSA)。

依赖:
  - bioscience.bionemo_adapter.BioNeMoAdapter
"""

from __future__ import annotations

import logging
from typing import Any

from .bionemo_adapter import BioNeMoAdapter, BioNeMoResult

logger = logging.getLogger(__name__)


class SequenceTaskRunner:
    """序列任务执行器。"""

    def __init__(self, adapter: BioNeMoAdapter):
        self._adapter = adapter

    # ── 公开接口 ────────────────────────────────────────────

    def get_embeddings_esm1nv(
        self,
        sequences: list[str],
    ) -> BioNeMoResult:
        """ESM-1nv 序列嵌入 — 快速序列到向量转换。

        Args:
            sequences: 蛋白质序列列表 (每条 ≤1024 aa)

        Returns:
            BioNeMoResult.data: list[dict] — 每个元素含
                "representations" (np.array), "tokens"
        """
        return self._adapter.call_model("esm1nv", sequences=sequences)

    def fetch_uniprot_sequence(
        self,
        uniprot_id: str,
    ) -> BioNeMoResult:
        """通过 UniProt ID 获取氨基酸序列。

        使用 BioNeMo 内置的 get_uniprot() 方法。

        Args:
            uniprot_id: UniProt 蛋白质 ID (如 "P68871")

        Returns:
            BioNeMoResult.data: dict — 含 "sequence", "id", "description"
        """
        try:
            if not self._adapter._check_sdk():
                # 无 SDK 时 fallback 到 REST
                import urllib.request
                import json

                url = f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.json"
                with urllib.request.urlopen(url, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                seq = data.get("sequence", {}).get("value", "")
                return BioNeMoResult(
                    ok=True,
                    status="success",
                    data={"id": uniprot_id, "sequence": seq, "description": data.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {}).get("value", "")},
                )

            from bionemo.api import BionemoClient
            c = self._adapter._client or BionemoClient(api_key=self._adapter.api_key or "dummy")
            result = c.get_uniprot(uniprot_id)
            return BioNeMoResult(ok=True, status="success", data=result)
        except Exception as exc:
            return BioNeMoResult(ok=False, status="error", error=f"UniProt 查询失败: {exc}")

    def fetch_smiles_from_pubchem(
        self,
        pubchem_cid: str,
    ) -> BioNeMoResult:
        """通过 PubChem CID 获取 SMILES。

        Args:
            pubchem_cid: PubChem 化合物 ID (如 "2244")

        Returns:
            BioNeMoResult.data: str — SMILES
        """
        try:
            if not self._adapter._check_sdk():
                import urllib.request
                url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{pubchem_cid}/property/CanonicalSMILES/JSON"
                with urllib.request.urlopen(url, timeout=30) as resp:
                    import json
                    data = json.loads(resp.read().decode())
                smi = data["PropertyTable"]["Properties"][0]["CanonicalSMILES"]
                return BioNeMoResult(ok=True, status="success", data={"pubchem_cid": pubchem_cid, "smiles": smi})

            from bionemo.api import BionemoClient
            c = self._adapter._client or BionemoClient(api_key=self._adapter.api_key or "dummy")
            result = c.get_smiles(pubchem_cid)
            return BioNeMoResult(ok=True, status="success", data=result)
        except Exception as exc:
            return BioNeMoResult(ok=False, status="error", error=f"PubChem 查询失败: {exc}")

    def list_available_models(self) -> list[dict]:
        """返回可用序列模型列表。"""
        return [
            {
                "name": "esm1nv",
                "description": "ESM-1nv 快速序列嵌入",
                "input": "蛋白质序列列表",
                "output": "嵌入向量",
            },
            {
                "name": "uniprot",
                "description": "UniProt 蛋白质序列查询",
                "input": "UniProt ID",
                "output": "氨基酸序列",
            },
            {
                "name": "pubchem",
                "description": "PubChem CID → SMILES",
                "input": "PubChem CID",
                "output": "SMILES",
            },
        ]
