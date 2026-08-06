"""分子操作模块 — 基于 RDKit（已安装）。

功能：
  - SMILES 校验与标准化
  - 分子指纹计算 (Morgan / MACCS / RDKit)
  - Tanimoto 相似度计算
  - 分子性质预测 (MW, LogP, HBA, HBD, TPSA, Rotatable Bonds)
  - SMILES ↔ SDF 格式转换
"""

from __future__ import annotations

import logging
from typing import Any

from .bio_adapter import BioResult

logger = logging.getLogger(__name__)


class MoleculeOps:
    """RDKit 分子操作封装。"""

    def __init__(self):
        self._rdkit = None

    @property
    def rdkit(self):
        if self._rdkit is None:
            from rdkit import Chem
            from rdkit.Chem import AllChem, Descriptors, MACCSkeys, rdMolDescriptors
            self._Chem = Chem
            self._AllChem = AllChem
            self._Descriptors = Descriptors
            self._MACCSkeys = MACCSkeys
            self._rdMolDescriptors = rdMolDescriptors
            self._rdkit = True
        return self._rdkit

    def execute(self, task: str = "", **kwargs: Any) -> BioResult:
        """统一入口，根据 task/params 自动选择操作。"""
        op = kwargs.get("op") or _infer_op_from_task(task)
        if not op:
            return BioResult(ok=False, status="error", error="未指定操作。可用: validate, fingerprint, similarity, properties, convert")

        try:
            self.rdkit  # 确保 RDKit 可导入
        except ImportError:
            return BioResult(ok=False, status="error", error="RDKit 未安装。安装: conda install -c conda-forge rdkit")

        handlers = {
            "validate": self._validate_smiles,
            "fingerprint": self._fingerprint,
            "similarity": self._similarity,
            "properties": self._properties,
            "convert": self._convert,
        }
        handler = handlers.get(op)
        if not handler:
            return BioResult(ok=False, status="error", error=f"未知操作: {op}")

        return handler(**kwargs)

    def _validate_smiles(self, **kwargs: Any) -> BioResult:
        """验证 SMILES 是否有效。"""
        smiles = kwargs.get("smiles") or kwargs.get("input") or ""
        if not smiles:
            return BioResult(ok=False, status="error", error="需要 SMILES 字符串")
        mol = self._Chem.MolFromSmiles(smiles)
        if mol is None:
            return BioResult(ok=False, status="error", text=f"无效 SMILES: {smiles}", data={"valid": False})
        canon = self._Chem.MolToSmiles(mol, canonical=True)
        return BioResult(ok=True, status="success", text=f"✅ 有效 SMILES\n  原始: {smiles}\n  规范: {canon}", data={"valid": True, "canonical": canon})

    def _fingerprint(self, **kwargs: Any) -> BioResult:
        """计算分子指纹。"""
        smiles = kwargs.get("smiles") or kwargs.get("input") or ""
        fp_type = kwargs.get("type", "morgan")  # morgan | maccs | rdkit
        mol = self._Chem.MolFromSmiles(smiles)
        if mol is None:
            return BioResult(ok=False, status="error", error=f"无效 SMILES: {smiles}")

        if fp_type == "morgan":
            fp = self._AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048)
        elif fp_type == "maccs":
            fp = self._MACCSkeys.GenMACCSKeys(mol)
        else:
            fp = self._Chem.RDKFingerprint(mol)

        bits = list(fp.GetOnBits())
        return BioResult(ok=True, status="success", text=f"🔬 {fp_type.upper()} 指纹\n  位向量长度: {fp.GetNumBits()}\n  非零位: {len(bits)}\n  前20位: {bits[:20]}", data={"type": fp_type, "nbits": fp.GetNumBits(), "on_bits": bits})

    def _similarity(self, **kwargs: Any) -> BioResult:
        """计算两个分子的 Tanimoto 相似度。"""
        smi_a = kwargs.get("smiles_a") or kwargs.get("a") or ""
        smi_b = kwargs.get("smiles_b") or kwargs.get("b") or ""
        mol_a = self._Chem.MolFromSmiles(smi_a)
        mol_b = self._Chem.MolFromSmiles(smi_b)
        if mol_a is None or mol_b is None:
            return BioResult(ok=False, status="error", error="一个或多个 SMILES 无效")
        fp_a = self._AllChem.GetMorganFingerprintAsBitVect(mol_a, 2, nBits=2048)
        fp_b = self._AllChem.GetMorganFingerprintAsBitVect(mol_b, 2, nBits=2048)
        from rdkit import DataStructs
        sim = DataStructs.TanimotoSimilarity(fp_a, fp_b)
        return BioResult(ok=True, status="success", text=f"📊 Tanimoto 相似度: {sim:.4f}\n  A: {smi_a}\n  B: {smi_b}", data={"tanimoto": round(sim, 4)})

    def _properties(self, **kwargs: Any) -> BioResult:
        """计算分子性质描述符。"""
        smiles = kwargs.get("smiles") or kwargs.get("input") or ""
        mol = self._Chem.MolFromSmiles(smiles)
        if mol is None:
            return BioResult(ok=False, status="error", error=f"无效 SMILES: {smiles}")

        from rdkit.Chem import Descriptors, rdMolDescriptors
        props = {
            "MW": round(Descriptors.MolWt(mol), 2),
            "LogP": round(Descriptors.MolLogP(mol), 2),
            "HBA": Descriptors.NumHAcceptors(mol),
            "HBD": Descriptors.NumHDonors(mol),
            "TPSA": round(Descriptors.TPSA(mol), 2),
            "RotBonds": Descriptors.NumRotatableBonds(mol),
            "HeavyAtoms": mol.GetNumHeavyAtoms(),
            "RingCount": Descriptors.RingCount(mol),
            "Formula": rdMolDescriptors.CalcMolFormula(mol),
        }
        lines = [f"📦 分子性质 — {smiles}", ""]
        for k, v in props.items():
            lines.append(f"  {k}: {v}")
        return BioResult(ok=True, status="success", text="\n".join(lines), data=props)

    def _convert(self, **kwargs: Any) -> BioResult:
        """SMILES ↔ SDF 转换。"""
        smiles = kwargs.get("smiles") or kwargs.get("input") or ""
        mol = self._Chem.MolFromSmiles(smiles)
        if mol is None:
            return BioResult(ok=False, status="error", error=f"无效 SMILES: {smiles}")
        mol = self._Chem.AddHs(mol)
        self._AllChem.EmbedMolecule(mol, randomSeed=42)
        self._AllChem.UFFOptimizeMolecule(mol)
        mol = self._Chem.RemoveHs(mol)
        sdf_block = self._Chem.MolToMolBlock(mol)
        return BioResult(ok=True, status="success", text=f"🔄 SMILES → SDF\n{smiles}\n\nSDF 块 ({len(sdf_block)} chars):\n{sdf_block[:2000]}", data={"sdf": sdf_block})


def _infer_op_from_task(task: str) -> str:
    tl = task.lower()
    if any(k in tl for k in ("验证", "校验", "validate", "有效")):
        return "validate"
    if any(k in tl for k in ("指纹", "fingerprint", "morgan", "maccs")):
        return "fingerprint"
    if any(k in tl for k in ("相似度", "similarity", "tanimoto", "对比")):
        return "similarity"
    if any(k in tl for k in ("性质", "properties", "描述符", "分子量", "logp")):
        return "properties"
    if any(k in tl for k in ("转换", "convert", "sdf")):
        return "convert"
    return ""
