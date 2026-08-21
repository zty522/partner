"""Capability & design events — 能力盘点 + 软件项目式总设计文档。

新增两个 Harness 原子事件：

1. atomic_capability_inventory — 盘点 Partner 能力（会什么 / 不会什么 / 需学什么），
   持续更新到共享的 partner_data/capabilities.md（5 个实例读同一份）。

2. atomic_write_design — 每个任务执行前用 LLM 生成软件项目式总设计文档，
   写入 shared_projects/<project>/design.md。

两者关系：接任务 → 先盘点能力 → 写设计（参考能力清单）→ 照设计执行 → 执行完回填能力清单。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


# ── 路径解析 ────────────────────────────────────────────────────────

def _workspace_root(ctx: Any) -> str:
    """从实例工作区解析 workspace 根目录。"""
    ws = getattr(ctx, "workspace", "") or ""
    if not ws:
        return ""
    try:
        from partner.workspace.workspace_layout import workspace_root_from_instance
        return workspace_root_from_instance(ws)
    except Exception:
        return ws


def _capabilities_path(ctx: Any) -> str:
    """能力清单共享路径：{workspace_root}/partner_data/capabilities.md"""
    root = _workspace_root(ctx)
    try:
        from partner.utils.workspace import get_partner_data_dir
        return os.path.join(get_partner_data_dir(root), "capabilities.md")
    except Exception:
        return os.path.join(root, "partner_data", "capabilities.md")


def _read_capabilities_snippet(ctx: Any, max_chars: int = 2500) -> str:
    """读取能力清单片段，供设计文档参考。"""
    path = _capabilities_path(ctx)
    try:
        if os.path.isfile(path):
            return open(path, encoding="utf-8").read()[:max_chars]
    except Exception:
        pass
    return ""


# ── 事件 1：能力盘点 ───────────────────────────────────────────────

async def atomic_capability_inventory(ctx, params: JsonDict) -> JsonDict:
    """盘点 Partner 能力，持续更新共享 capabilities.md。

    参数:
        save_path (str, 可选): 覆盖默认保存路径
        include_experience (bool, 可选): 是否包含历史经验统计（默认 True）
    """
    save_path = str(params.get("save_path") or params.get("path") or "").strip()
    if not save_path:
        save_path = _capabilities_path(ctx)

    previous = ""
    if os.path.isfile(save_path):
        try:
            previous = open(save_path, encoding="utf-8").read()
        except Exception:
            previous = ""

    inventory = None
    gaps: list = []
    try:
        def _run():
            from partner.evolution.self_review import SelfReview
            reviewer = SelfReview(workspace=getattr(ctx, "workspace", "") or None)
            inv = reviewer.generate_capability_inventory()
            gs = reviewer.identify_gaps(inv)
            return inv, gs
        inventory, gaps = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.warning("[CAPABILITY] inventory failed (non-fatal): %s", exc)

    md = _render_capability_md(inventory, gaps, previous)

    parent = os.path.dirname(save_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(md)
        if not md.endswith("\n"):
            f.write("\n")

    stats = {
        "agents": len(inventory.agents) if inventory else 0,
        "skills": inventory.skill_count if inventory else 0,
        "events": len(inventory.event_types) if inventory else 0,
        "gaps": len(gaps),
    }
    logger.info("[CAPABILITY] inventory written to %s (agents=%s skills=%s events=%s gaps=%s)",
                save_path, stats["agents"], stats["skills"], stats["events"], stats["gaps"])
    return {"ok": True, "path": save_path, "files": [save_path],
            "content": md[:2000], "stats": stats}


def _render_capability_md(inventory, gaps, previous: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []
    lines.append("# Partner 能力清单")
    lines.append("")
    lines.append(f"> 最后盘点：{now}")
    lines.append("> 说明：本清单由 Partner 自我盘点生成并持续维护。接新任务前先查阅，判断缺什么、要不要先学。")
    lines.append("")

    # ── 一、会什么 ──
    lines.append("## 一、会什么（已具备）")
    lines.append("")
    if inventory is not None:
        agents = inventory.agents or []
        lines.append(f"### 已接入 Agent（{len(agents)} 个）")
        if agents:
            for a in agents:
                caps = ", ".join(a.get("capabilities") or []) or "（未标注）"
                status = a.get("health_status", "unknown")
                label = {"ok": "健康", "unknown": "未知", "unavailable": "未安装",
                         "timeout": "超时", "error": "异常"}.get(status, status)
                lines.append(f"- **{a.get('name', '?')}** — {caps} | 状态: {label}")
        else:
            lines.append("- （暂无已接入 Agent）")
        lines.append("")
        lines.append(f"### 技能 / 事件")
        lines.append(f"- 已注册技能：{inventory.skill_count} 个")
        lines.append(f"- 已注册 Harness 事件：{len(inventory.event_types or [])} 个")
        et = inventory.event_types or []
        if et:
            lines.append(f"- 事件列表：{', '.join(sorted(str(e) for e in et)[:60])}")
        lines.append("")
        stats = inventory.experience_stats or {}
        total = stats.get("total", 0)
        sr = stats.get("success_rate", 0.0)
        if total:
            lines.append(f"### 历史经验")
            lines.append(f"- 累计任务：{total} 次，成功率 {sr:.0%}")
        lines.append("")
    else:
        lines.append("（盘点失败，暂无数据）")
        lines.append("")

    # ── 二、不会什么 ──
    lines.append("## 二、不会什么（能力缺口）")
    lines.append("")
    if gaps:
        for g in gaps:
            prio = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}.get(g.priority, g.priority)
            lines.append(f"- [{prio}] **{g.name}** — {g.description}")
    else:
        lines.append("- 暂未检测到明显缺口。")
    lines.append("")

    # ── 三、需要先学什么 ──
    lines.append("## 三、需要先学什么（学习计划）")
    lines.append("")
    learn_items = _derive_learn_plan(gaps)
    if learn_items:
        for i, item in enumerate(learn_items, 1):
            lines.append(f"{i}. {item}")
    else:
        lines.append("- 当前无需紧急学习项；保持现状，遇到缺口再补。")
    lines.append("")

    # ── 历史学习计划留存（持续维护：对比上次）──
    if previous:
        prev_plan = _extract_section(previous, "需要先学什么")
        if prev_plan:
            lines.append("## 附：上次学习计划（对照用）")
            lines.append("")
            lines.append(prev_plan.strip())
            lines.append("")

    return "\n".join(lines)


# 补缺动作库：缺口关键词 → 具体可执行的补缺动作。
# 优先级：已覆盖的说明覆盖关系；未覆盖的给出工具、安装方式与来源。
_GAP_REMEDIATION: dict[str, str] = {
    "基因组注释": "建议集成 prokka（apt install prokka 或 conda -c bioconda prokka；依赖 perl/bioperl 生态，需 sudo）"
                  "或 bakta（conda -c conda-forge -c bioconda bakta）",
    "GATK": "变体调用已由 bcftools agent 覆盖（variant_calling/snpcalling），无需再集成重型 GATK（Java+~10GB 依赖）",
    "DESeq2": "差异表达已由 diffexp agent 覆盖（scanpy Wilcoxon 秩和检验）；若需 DESeq2 特定流程（负二项模型）再集成",
    "差异表达分析": "已由 diffexp agent 覆盖（h5ad + groupby 列 → Wilcoxon 差异基因表）",
    "变体调用": "已由 bcftools agent 覆盖（VCF 统计 + QUAL/DP 过滤）",
    "通路富集分析": "已由 enrichment agent 覆盖（gseapy ORA，Enrichr 基因集库）",
    "系统发育分析": "已由 iqtree agent 覆盖（IQ-TREE 2 最大似然 + ModelFinder）",
    "AlphaFold": "蛋白结构预测已由 bionemo agent 部分覆盖（protein_structure）；如需 AlphaFold 级精度需 GPU + 权重",
    "DiffDock": "分子对接：pocketflow（structure_based_drug_design）覆盖分子生成；若需显式对接可集成 DiffDock（GitHub 源码，需 GPU）",
    "Scanpy": "单细胞分析已由 cytobridge agent 覆盖（预处理/UMAP/PAGA/DPT/RNA velocity）",
    "BLAST": "序列搜索已由 bioinformatics agent 覆盖（blast_search）",
    "Rosetta": "蛋白设计：bionemo（protein_structure）部分覆盖；如需 Rosetta 全套需 conda（bioconda）安装",
    "CellChat": "细胞通讯：暂未覆盖；可选 R 生态 CellChat 或 Python 替代（cell2cell）",
    "Seurat": "单细胞分析已由 cytobridge agent 覆盖（scanpy 生态，功能等价）",
    "GROMACS": "分子动力学：external/amber 已集成但无 agent 声明；可补 wrapper 或集成 OpenMM（pip）",
    "PLINK": "GWAS 已由 plink agent 覆盖（PLINK 1.9，--assoc --adjust）",
    "IQ-TREE": "系统发育已由 iqtree agent 覆盖（IQ-TREE 2.4.0）",
}


def _find_remediation(gap) -> str:
    """按缺口名/描述匹配补缺动作；未命中返回空串。"""
    haystack = f"{gap.name} {gap.description}"
    for kw, action in _GAP_REMEDIATION.items():
        if kw.lower() in haystack.lower():
            return action
    return ""


def _derive_learn_plan(gaps) -> list[str]:
    """从缺口推导学习计划（优先级排序），附具体补缺动作。"""
    items: list[str] = []
    for g in gaps:
        prio = g.priority
        action = _find_remediation(g)
        suffix = f" → {action}。" if action else " → 建议优先接入或学习对应工具/Agent。"
        if prio == "high":
            items.append(f"【高优先级】{g.name}：{g.description}{suffix}")
        elif prio == "medium":
            items.append(f"【中优先级】{g.name}：{g.description}{suffix}")
        else:
            items.append(f"【低优先级】{g.name}：{g.description}{suffix}")
    return items


def _extract_section(md: str, title: str) -> str:
    """从 markdown 里抽取某个 ## 标题段落到下一个 ## 之前。"""
    pattern = rf"##[^\n]*{re.escape(title)}[^\n]*\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, md, re.S)
    if not m:
        return ""
    return m.group(1).strip()


# ── 事件 2：写总设计 ───────────────────────────────────────────────

async def atomic_write_design(ctx, params: JsonDict) -> JsonDict:
    """用 LLM 生成软件项目式总设计文档，写入 shared_projects/<project>/design.md。

    参数:
        goal (str): 任务目标（默认取 ctx.user_goal / ctx.title）
        save_path (str, 可选): 覆盖默认保存路径（默认 {project_dir}/design.md）
        force_regenerate (bool, 可选): 已存在 design.md 时是否覆盖（默认 True）
    """
    goal = str(params.get("goal") or params.get("task") or
               getattr(ctx, "user_goal", "") or getattr(ctx, "title", "") or "").strip()
    save_path = str(params.get("save_path") or "").strip()
    if not save_path:
        project_dir = getattr(ctx, "project_dir", "") or getattr(ctx, "working_dir", "") or ""
        save_path = os.path.join(project_dir, "design.md")

    force = bool(params.get("force_regenerate", True))
    if not force and os.path.isfile(save_path):
        return {"ok": True, "path": save_path, "files": [save_path],
                "content": open(save_path, encoding="utf-8").read()[:2000], "reused": True}

    caps_snippet = _read_capabilities_snippet(ctx)
    prompt = _build_design_prompt(goal, caps_snippet)

    design_md = ""
    adapter = getattr(ctx, "adapter", None)
    if adapter is not None:
        try:
            chat = getattr(adapter, "chat", None)
            if chat is None:
                raise RuntimeError("adapter 缺少 chat 方法")
            if asyncio.iscoroutinefunction(chat):
                design_md = await chat(prompt, purpose="action")
            else:
                design_md = await asyncio.to_thread(chat, prompt, purpose="action")
        except Exception as exc:
            logger.warning("[DESIGN] adapter.chat failed (non-fatal): %s", exc)

    design_md = str(design_md or "").strip()
    if len(design_md) < 120 or "```" in design_md[:80] and design_md.count("```") % 2 == 1:
        # LLM 输出过短或畸形 → 兜底骨架
        design_md = _design_skeleton(goal, caps_snippet)

    # 去掉可能的 markdown 代码围栏
    design_md = _strip_code_fence(design_md)

    parent = os.path.dirname(save_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(design_md)
        if not design_md.endswith("\n"):
            f.write("\n")

    logger.info("[DESIGN] design written to %s (%d chars)", save_path, len(design_md))
    return {"ok": True, "path": save_path, "files": [save_path],
            "content": design_md[:2000], "goal": goal[:200]}


def _build_design_prompt(goal: str, caps_snippet: str) -> str:
    cap_block = ""
    if caps_snippet:
        cap_block = (
            "\n\n【Partner 当前能力清单（供参考，据此判断缺口）】\n"
            + caps_snippet
            + "\n"
        )
    return (
        "你是一名资深软件架构师。请为下面的任务编写一份《总设计》文档，"
        "就像软件项目开工前的设计文档。用中文、Markdown 格式输出。\n\n"
        f"任务目标：\n{goal}\n"
        f"{cap_block}\n"
        "请严格按以下结构输出（直接输出 Markdown，不要任何多余说明）：\n\n"
        "# 总设计\n\n"
        "## 1. 目标\n（明确要做什么、为什么做、交付什么）\n\n"
        "## 2. 现状与能力\n（基于能力清单，说明已具备能力和缺口）\n\n"
        "## 3. 方案设计\n（核心思路、技术路线、关键决策）\n\n"
        "## 4. 模块划分\n（拆分为哪些模块/步骤，各自职责）\n\n"
        "## 5. 实现步骤\n（按顺序列出具体可执行步骤）\n\n"
        "## 6. 接口与依赖\n（依赖哪些工具/Agent/外部资源，数据如何流转）\n\n"
        "## 7. 验收标准\n（如何判断完成、交付物是什么）\n"
    )


def _design_skeleton(goal: str, caps_snippet: str = "") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cap_note = ""
    if caps_snippet:
        first = caps_snippet.strip().split("\n")[0] if caps_snippet.strip() else ""
        cap_note = f"（能力清单已有记录：{first}）"
    return (
        "# 总设计\n\n"
        f"> 生成时间：{now}（兜底骨架，LLM 未生成完整设计）\n\n"
        f"## 1. 目标\n{goal}\n\n"
        f"## 2. 现状与能力\n待补充 {cap_note}\n\n"
        "## 3. 方案设计\n待补充\n\n"
        "## 4. 模块划分\n待补充\n\n"
        "## 5. 实现步骤\n待补充\n\n"
        "## 6. 接口与依赖\n待补充\n\n"
        "## 7. 验收标准\n待补充\n"
    )


def _strip_code_fence(md: str) -> str:
    """去掉 LLM 输出首尾的 ```markdown ... ``` 围栏。"""
    text = md.strip()
    if text.startswith("```"):
        # 去掉第一行的 ```lang
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    return text
