"""Goal Parser — transforms user messages into verifiable subgoals 
and provides Loop Engineering infrastructure for Event pipelines."""

from __future__ import annotations
import logging, json, re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

@dataclass
class SubGoal:
    id: str
    description: str
    type: str  # 'artifact', 'format', 'content', 'action', 'quantity'
    target: Any = None
    achieved: bool = False
    evidence: str = ""

@dataclass  
class Hypothesis:
    gap: str
    suggestion: str
    action_type: str  # 'search', 'generate', 'compute', 'verify'

@dataclass
class SubGoalStatus:
    subgoal: SubGoal
    status: str  # 'achieved', 'partial', 'unachieved'
    reason: str = ""

# ── Goal Parsing ────────────────────────────────────────────────

COMMON_GOAL_PATTERNS = [
    (r"(生成|创建|编写).*(报告|文档|文章)", "artifact", "生成文档"),
    (r"(对比|比较|差异分析)", "action", "对比分析"),
    (r"(搜索|查找|检索|查).*(文献|论文|资料)", "action", "文献检索"),
    (r"(统计|计算|测量|分析)", "action", "数据分析"),
    (r"(可视化|绘图|图表|图)", "artifact", "可视化"),
    (r"(表格|excel|csv)", "format", "表格输出"),
    (r"(pdf|PDF)", "format", "PDF输出"),
    (r"至少.*(\d+).*(篇|个|条|种)", "quantity", "数量要求"),
]

def parse_goal(user_message: str) -> list[SubGoal]:
    """Parse user message into structured subgoals."""
    if not user_message:
        return [SubGoal(id="g0", description="完成用户请求", type="action")]
    goals = []
    for pattern, gtype, desc in COMMON_GOAL_PATTERNS:
        m = re.search(pattern, user_message)
        if m:
            gid = f"g{len(goals)+1}"
            target = int(m.group(1)) if m.lastindex and m.group(1).isdigit() else None
            goals.append(SubGoal(id=gid, description=desc, type=gtype, target=target))
    if not goals:
        goals.append(SubGoal(id="g1", description="完成用户请求", type="action"))
    return goals

def validate_subgoals(artifacts: dict, subgoals: list[SubGoal]) -> list[SubGoalStatus]:
    """Check each subgoal's achievement status against current artifacts."""
    statuses = []
    for sg in subgoals:
        content = str(artifacts.get("content", "") or artifacts.get("result", "") or "")
        files = str(artifacts.get("files", "") or artifacts.get("artifacts", "") or "")
        all_text = content + files
        
        if sg.type == "artifact":
            has_file = bool(artifacts.get("files")) or bool(artifacts.get("artifacts"))
            status = "achieved" if has_file else "unachieved"
            reason = "文件已生成" if has_file else "未生成文件"
        elif sg.type == "format":
            has_fmt = sg.description.lower() in all_text.lower()
            status = "achieved" if has_fmt else "unachieved"
            reason = f"格式匹配: {sg.description}" if has_fmt else f"格式不匹配: {sg.description}"
        elif sg.type == "quantity":
            if sg.target and len(re.findall(r'[\w]+', all_text)) >= sg.target:
                status, reason = "achieved", f"数量达到 {sg.target}"
            else:
                status, reason = "unachieved", f"数量不足 {sg.target}"
        elif sg.type == "action":
            status = "achieved" if len(all_text) > 100 else "partial"
            reason = f"内容长度: {len(all_text)}"
        else:
            status, reason = "partial", "无法确定"
        
        statuses.append(SubGoalStatus(subgoal=sg, status=status, reason=reason))
    return statuses

def analyze_gap(statuses: list[SubGoalStatus]) -> list[Hypothesis]:
    """Analyze gaps between subgoal targets and current state, generate hypotheses."""
    hypotheses = []
    for st in statuses:
        if st.status == "unachieved":
            hypotheses.append(Hypothesis(
                gap=f"未达成: {st.subgoal.description}",
                suggestion=f"尝试扩展搜索范围或增加执行步骤以达成: {st.subgoal.description}",
                action_type="search" if "搜索" in st.subgoal.type else "generate"
            ))
        elif st.status == "partial":
            hypotheses.append(Hypothesis(
                gap=f"部分达成: {st.subgoal.description} - {st.reason}",
                suggestion=f"继续完善: {st.subgoal.description}",
                action_type="generate"
            ))
    return hypotheses

def generate_new_plan(hypotheses: list[Hypothesis], current_events: list[dict] | None = None) -> list[dict]:
    """Generate new Event plan steps based on hypotheses."""
    new_events = list(current_events or [])
    for h in hypotheses:
        if h.action_type == "search":
            new_events.append({"event_type": "web_search", "parameters": {"query": h.suggestion}})
        elif h.action_type == "generate":
            new_events.append({"event_type": "smart_llm_structured_action", "parameters": {"prompt": h.suggestion}})
    return new_events


def format_subgoal_report(statuses: list[SubGoalStatus], hypotheses: list[Hypothesis]) -> str:
    """Format subgoal validation report for user display."""
    lines = ["## Goal 完成度检查", ""]
    for st in statuses:
        icon = "✅" if st.status == "achieved" else "🔄" if st.status == "partial" else "❌"
        lines.append(f"{icon} {st.subgoal.description}: {st.reason}")
    if hypotheses:
        lines.extend(["", "### 改进假设"])
        for h in hypotheses:
            lines.append(f"- {h.gap} → {h.suggestion}")
    return "\n".join(lines)
