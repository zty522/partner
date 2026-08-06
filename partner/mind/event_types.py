"""Mind Event Types — Partner 自主系统的念头类型定义。

每个念头是系统内部产生的"我想做这件事"的冲动。
不同于传统任务队列，念头可以自发产生、自我修改、自我销毁。

念头优先级（数值越低越紧急）：
  1  = 立即（用户指令）
  3  = 高（汇报）
  5  = 中（项目）
  7  = 低
  10 = 最低（心跳）

保留的事件类型：PROJECT, REPORT, REFLECTION,
CROSS_PROJECT, MEMORY_CONSOLIDATE, CONTENT_DIGEST, CONTENT_PATROL, DIRECT_REPLY。

动作级事件：BATCH_PLAN, DIRECT_TASK, LITERATURE_REVIEW, DATA_FETCH, DATA_ANALYSIS, VISUALIZATION,
EVIDENCE_AUDIT, ARTIFACT_BUILD, PDF_REPORT, EMAIL_DELIVERY, WEB_SEARCH, WEB_CAPTURE, PROJECT_THINK,
OBJECTIVE_REVIEW, CURIOSITY_EXPLORE, HABIT_UPDATE, OLLAMA_STATUS。项目只是容器，具体动作由这些
小事件承载，避免所有用户请求都进入重型 PROJECT 管道。
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4
from typing import Optional, Dict, Any
from enum import Enum


class EventType(str, Enum):
    """所有念头类型（精简版）。"""
    USER_MESSAGE = "user_message"      # 外部入口收到用户消息：先交给 InteractionOrchestrator/selector 决定路由
    DIRECT_REPLY = "direct_reply"      # 直接 LLM 回复：不需要多步执行，直接生成回复
    PROJECT = "project"                # 长期项目：多步推进的研究意图
    BATCH_PLAN = "batch_plan"          # 顶层批量规划：一次生成 Harness MicroPlan 并执行
    DIRECT_TASK = "direct_task"        # 一次性直接交付：改文件、生成文件等
    LITERATURE_REVIEW = "literature_review"  # 查资料/找文献/整理方法，不自动进入实验
    DATA_FETCH = "data_fetch"          # 数据获取：只获取/下载/保存一个真实数据源
    DATA_ANALYSIS = "data_analysis"    # 数据读取、统计、质量检查或最小分析
    VISUALIZATION = "visualization"    # 可视化：只基于已有数据/结果绘制图表
    EVIDENCE_AUDIT = "evidence_audit"  # 证据审计、泄露/过拟合/可靠性检查
    ARTIFACT_BUILD = "artifact_build"  # 图表、表格、代码、PPT 等用户可见产物构建
    PDF_REPORT = "pdf_report"          # PDF 报告生成：把已有结果/摘要整理成 PDF 并交付
    EMAIL_DELIVERY = "email_delivery"  # 邮件交付：把已有/本轮生成文件通过 SMTP 发送
    WEB_SEARCH = "web_search"          # 公开网页/平台搜索：网页、小红书、B站等
    WEB_CAPTURE = "web_capture"        # 网页/图片捕获：下载公开图片或对公开网页截图
    FILE_INSPECTION = "file_inspection"  # 附件识别：魔数、hex dump、格式边界说明
    PROJECT_THINK = "project_think"    # 项目拆解、难点识别、下一步选择
    OBJECTIVE_REVIEW = "objective_review"  # 目标/交付物对齐：回看根目标、已完成、缺口和下一 event
    CHECK = "check"                    # Harness 迭代检查：本地规则检查产物是否满足
    REFLECT = "reflect"                # Harness 迭代反思：LLM 分析缺口和补充方向
    CURIOSITY = "curiosity"            # Harness 迭代好奇补充：根据缺口生成小计划
    CURIOSITY_EXPLORE = "curiosity_explore"  # 好奇探索：从可执行下一步中选择最小探索动作
    HABIT_UPDATE = "habit_update"      # 经验/习惯/成长记录和抽象化
    OLLAMA_STATUS = "ollama_status"    # Ollama 状态探测：检查轻量模型是否可用
    TASK_FAILED = "task_failed"           # 任务失败总结：通知用户并提供诊断信息
    STOP_PROJECT = "stop_project"      # 显式停止当前执行链
    EXECUTE_CODE = "execute_code"      # 执行 Python 代码：写脚本、运行、产出结果：由 LLM selector 选择后才等待/清 active
    REPORT = "report"                  # 汇报：向用户推送进展或结果
    REFLECTION = "reflection"          # 独立长反思：跨轮次整理失败/边界/策略
    SELF_REVIEW = "self_review"        # 自我审视：生成能力清单、识别差距、搜索解决方案
    CROSS_PROJECT = "cross_project"    # 默认网络：跨
    CRON_TICK = "cron_tick"            # 定时触发：cron 周期性健康检查和任务推进
    WAKE_UP = "wake_up"                # 唤醒：从等待室恢复执行项目迁移
    # (duplicates removed)
    MEMORY_CONSOLIDATE = "memory_consolidate"  # 记忆压缩：保持 prompt 轻量
    CONTENT_DIGEST = "content_digest"  # 外部内容消化：用户分享/自巡游素材 → 假设/灵感
    CONTENT_PATROL = "content_patrol"  # 受控巡游：公开内容入口 → content_feed
    APPLY_ARCHITECTURE_IMPROVEMENT = "apply_architecture_improvement"  # 应用架构改进方案


@dataclass
class MindEvent:
    """一个念头 = 系统内部一次自主冲动的完整描述。"""
    id: str = field(default_factory=lambda: f"ev_{uuid4().hex[:8]}")
    type: EventType = EventType.PROJECT
    priority: int = 5                  # 1=最急, 10=最缓
    payload: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source: str = ""                   # 产生来源："cron_tick" | "executor" | "user" | "self_check"
    parent_id: Optional[str] = None    # 由哪个念头产生的？
    wake_after: Optional[float] = None # 时间戳（time.time()），在此之前不出队

    def __lt__(self, other: 'MindEvent') -> bool:
        """PriorityQueue 使用 < 比较确定顺序。数值越低优先级越高。"""
        if not isinstance(other, MindEvent):
            return NotImplemented
        if self.priority != other.priority:
            return self.priority < other.priority
        if self.created_at != other.created_at:
            return self.created_at < other.created_at
        return self.id < other.id

    def __le__(self, other: 'MindEvent') -> bool:
        if not isinstance(other, MindEvent):
            return NotImplemented
        return self.priority <= other.priority

    def __gt__(self, other: 'MindEvent') -> bool:
        if not isinstance(other, MindEvent):
            return NotImplemented
        return self.priority > other.priority

    def __ge__(self, other: 'MindEvent') -> bool:
        if not isinstance(other, MindEvent):
            return NotImplemented
        return self.priority >= other.priority


# ── Convenience factory functions ──────────────────────────────────


def report(content: str, priority: int = 3, source: str = "",
           parent_id: str = None) -> MindEvent:
    """创建一个 Report 念头，payload 含 content。"""
    return MindEvent(
        type=EventType.REPORT,
        priority=priority,
        payload={"content": content},
        source=source,
        parent_id=parent_id,
    )


def direct_reply(text: str, reply: str = "", source: str = "desktop") -> MindEvent:
    """创建一个直接回复念头。"""
    return MindEvent(
        type=EventType.DIRECT_REPLY,
        priority=1,
        payload={"text": text, "reply": reply, "source": source},
        source=source,
    )


def reflection(source: str = "self_pulse") -> MindEvent:
    return MindEvent(
        type=EventType.REFLECTION,
        priority=7,
        payload={},
        source=source,
    )


def cross_project(source: str = "self_pulse") -> MindEvent:
    return MindEvent(
        type=EventType.CROSS_PROJECT,
        priority=8,
        payload={},
        source=source,
    )


def memory_consolidate(source: str = "self_pulse") -> MindEvent:
    return MindEvent(
        type=EventType.MEMORY_CONSOLIDATE,
        priority=9,
        payload={},
        source=source,
    )
