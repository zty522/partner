"""C3 增强：工具保障事件 —— 检测/自动补缺外部工具。

ensure_tool: 确保指定工具可用（检测 → 自动下载 → 验证），
            用于任务执行前保障依赖就绪。对应 partner/evolution/gap_filler.py。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


async def atomic_ensure_tool(ctx, params: JsonDict) -> JsonDict:
    """确保工具可用：检测 external/tools/ 与 PATH，缺失时尝试自动补缺。

    参数:
        tool (str, 必填): 工具名（plink / iqtree / bcftools / prokka / samtools / mafft / muscle / seqkit）
    返回: {ok, status, path, message}
    """
    tool = str(params.get("tool") or "").strip()
    workspace = str(getattr(ctx, "workspace", "") or "")
    if not tool:
        return {"ok": False, "status": "missing_param", "message": "缺少参数 tool", "path": ""}

    from ..evolution.gap_filler import detect_tool, fill_gap

    path = detect_tool(tool)
    if path:
        return {"ok": True, "status": "already_present", "path": path,
                "message": f"{tool} 已就绪: {path}"}

    result = fill_gap(workspace, tool)
    return {
        "ok": result.get("ok", False),
        "status": result.get("status", "unsupported"),
        "path": result.get("path", ""),
        "message": result.get("message", ""),
    }
