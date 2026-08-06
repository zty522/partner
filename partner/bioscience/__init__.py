"""BioNeMo 科学能力平台接入层。

封装 NVIDIA BioNeMo Service SDK 和 REST API，提供分子生成/对接、
蛋白质结构预测、序列分析等科学计算能力。

架构层：
  bionemo_adapter.py    — BioNeMo SDK/HTTP 适配器核心
  molecule_tasks.py     — 分子生成、分子对接、虚拟筛选
  protein_tasks.py      — 蛋白质结构预测、折叠分析
  sequence_tasks.py     — 序列分析、基因组注释
"""

import logging
from typing import Any

from .bionemo_adapter import BioNeMoAdapter, BioNeMoResult
from .molecule_tasks import MoleculeTaskRunner
from .protein_tasks import ProteinTaskRunner
from .sequence_tasks import SequenceTaskRunner

logger = logging.getLogger(__name__)

__all__ = [
    "BioNeMoAdapter",
    "BioNeMoResult",
    "MoleculeTaskRunner",
    "ProteinTaskRunner",
    "SequenceTaskRunner",
    "dispatch",
]


def dispatch(
    task: str = "",
    model: str = "",
    input: str | None = None,
    params: dict | None = None,
    **kwargs: Any,
) -> dict:
    """供 AgentDispatcher 调用的统一入口函数 (python_api endpoint)。

    由 bionemo.json manifest 的 endpoint_config 指向此函数。
    AgentDispatcher._dispatch_python() 会调用此函数并返回 AgentResult。

    Args:
        task: 自然语言任务描述
        model: 模型名 (molmim/diffdock/esmfold/openfold/esm2/esm1nv/...)
        input: 输入数据 (序列/SMILES/文件路径)
        params: 模型特定参数 dict
        **kwargs: 额外参数 (与 params 合并)

    Returns:
        dict: 符合 AgentResult.output 格式的字典
    """
    try:
        adapter = BioNeMoAdapter()
        if not adapter.is_available():
            return {
                "status": "error",
                "output": {
                    "type": "text",
                    "text": (
                        "BioNeMo 当前不可用。\n"
                        "请设置 NGC_API_KEY 或配置本地 NIM 端点:\n"
                        "  export NGC_API_KEY=your_ngc_api_key\n"
                        "  pip install bionemo\n"
                        "或使用本地 NIM:\n"
                        "  export BIONEMO_NIM_BASE=http://localhost:8000"
                    ),
                },
                "error": "BioNeMo not available — no SDK and no NIM endpoint configured",
            }

        merged_params = dict(params or {})
        merged_params.update(kwargs)
        if input:
            merged_params.setdefault("input", input)

        # 根据 task 关键词自动选择模型
        resolved_model = model
        if not resolved_model and task:
            resolved_model = _resolve_model_from_task(task)

        if not resolved_model:
            return {
                "status": "error",
                "output": {"type": "text", "text": "未指定模型。可用: molmim, diffdock, esmfold, openfold, esm2, esm1nv"},
                "error": "model parameter required",
            }

        result = adapter.call_model(resolved_model, **merged_params)

        if result.ok:
            return {
                "status": "success",
                "output": {
                    "type": "text",
                    "text": _format_result_text(resolved_model, result),
                },
                "metadata": {
                    "model": resolved_model,
                    "method": result.metadata.get("method", "unknown"),
                    "duration_s": result.duration_s,
                },
            }
        else:
            return {
                "status": "error" if result.status != "auth_error" else "auth_error",
                "output": {"type": "text", "text": result.error},
                "error": result.error,
            }

    except Exception as exc:
        logger.exception("[BioNeMo.dispatch] 未预期错误")
        return {
            "status": "error",
            "output": {"type": "text", "text": f"BioNeMo 调用异常: {exc}"},
            "error": str(exc),
        }


def _resolve_model_from_task(task: str) -> str:
    """从任务描述自动推断模型名。"""
    task_lower = task.lower()
    if any(k in task_lower for k in ("分子生成", "molmim", "generate molecule", "新分子")):
        return "molmim"
    if any(k in task_lower for k in ("分子对接", "diffdock", "docking", "对接", "虚拟筛选", "virtual screen")):
        return "diffdock"
    if any(k in task_lower for k in ("esmfold", "快速折叠", "fast fold")):
        return "esmfold"
    if any(k in task_lower for k in ("openfold", "高精度折叠", "蛋白质结构")):
        return "openfold"
    if any(k in task_lower for k in ("esm2", "蛋白质嵌入", "embedding", "蛋白质表示")):
        return "esm2"
    if any(k in task_lower for k in ("esm1nv", "序列嵌入", "序列表示")):
        return "esm1nv"
    return ""


def _format_result_text(model: str, result: BioNeMoResult) -> str:
    """将模型输出格式化为用户可读文本。"""
    lines = [f"[BioNeMo] {model} — {result.duration_s:.1f}s", ""]
    data = result.data

    if model == "molmim" and isinstance(data, list):
        lines.append(f"生成了 {len(data)} 个分子:")
        for i, mol in enumerate(data[:10], 1):
            smi = mol.get("smiles", mol.get("SMILES", ""))
            lines.append(f"  {i}. {smi}")
        if len(data) > 10:
            lines.append(f"  ... 还有 {len(data) - 10} 个")
    elif model in ("esmfold", "openfold") and isinstance(data, dict):
        pdb = data.get("pdb_string", "")
        plddt = data.get("plddt", [])
        lines.append(f"PDB 结构长度: {len(pdb)} 字符")
        lines.append(f"pLDDT 置信度: {len(plddt)} 个残基")
        if plddt:
            avg = sum(plddt) / len(plddt)
            lines.append(f"平均 pLDDT: {avg:.1f}")
    elif model == "diffdock" and isinstance(data, dict):
        poses = data.get("poses", [])
        confs = data.get("confidence_scores", [])
        lines.append(f"生成 {len(poses)} 个对接姿势")
        if confs:
            lines.append(f"置信度范围: {min(confs):.3f} – {max(confs):.3f}")
    elif model in ("esm2", "esm1nv") and isinstance(data, list):
        lines.append(f"处理了 {len(data)} 条序列")
        for i, entry in enumerate(data[:5]):
            tokens = entry.get("tokens", [])
            lines.append(f"  {i+1}. 嵌入维度: {len(tokens) if hasattr(tokens, '__len__') else 'N/A'}")
    else:
        lines.append(str(data)[:1000])

    return "\n".join(lines)


# ── AgentDispatcher 兼容别名 ──
# manifest 中 function="_dispatch" 指向此名
_dispatch = dispatch
