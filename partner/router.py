"""Conversation Router - routes user queries to appropriate handlers.

This module implements the Conversation Router component of Research Entity.
It classifies user intent and routes to specialized handlers.

Supported intents:
  - status:     "你在干什么？", "最近做了什么？", "进展如何？"
  - knowledge:  "关于 X 你知道什么？", "扩散模型是什么？"
  - direction:  "暂停 X，集中做 Y", "优先研究 X"
  - detail:     "详细说说 X", "展开讲讲"
  - task_mgmt:  "添加任务：研究 X", "取消任务 Y"
  - help:       "帮助", "你能做什么？"
  - general:    anything else
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict, Callable
from enum import Enum


class Intent(Enum):
    """User intent classification."""
    STATUS = "status"
    PROGRESS = "progress"
    KNOWLEDGE = "knowledge"
    DIRECTION = "direction"
    DETAIL = "detail"
    TASK_ADD = "task_add"
    TASK_CANCEL = "task_cancel"
    WORKSPACE = "workspace"  # organize workspace
    HELP = "help"
    GENERAL = "general"


@dataclass
class ParsedQuery:
    """Result of intent parsing."""
    intent: Intent
    confidence: float  # 0.0 - 1.0
    query: str  # original query
    topic: Optional[str] = None  # extracted topic/subject
    params: Optional[Dict] = None  # additional extracted parameters


# Intent classification rules: (pattern, intent, confidence, topic_group)
# topic_group: regex group index for topic extraction, or None
INTENT_RULES: List[Tuple[str, Intent, float, Optional[int]]] = [
    # Status queries - expanded for QQ chat
    (r"(最近|刚才|今天)(在?干|在?做|研究|搞|忙)(了?什么|啥|什么活)", Intent.STATUS, 0.95, None),
    (r"(你在?干什么|你在?做什么|在忙什么|状态|进展如何|进展怎么样|最近在研究什么)", Intent.STATUS, 0.9, None),
    (r"(干嘛呢|忙啥|干啥|在干嘛|最近在干嘛|在忙啥)", Intent.STATUS, 0.95, None),
    (r"(what (have you|did you|are you)|recent|status|progress)", Intent.STATUS, 0.9, None),
    (r"(汇报|总结一下|最近的?进展)", Intent.STATUS, 0.85, None),
    
    # Progress / task queue queries
    (r"(任务队列|待办|还有多少任务|pending|任务列表)", Intent.PROGRESS, 0.9, None),
    (r"(还有什么(要|需要)做|下一步做什么|接下来做什么)", Intent.PROGRESS, 0.85, None),
    
    # Knowledge queries (with topic extraction)
    (r"关于[「『]?(.+?)[」』]?(你)?(知道|了解|学到|发现)了?什么", Intent.KNOWLEDGE, 0.95, 1),
    (r"(什么是|怎么理解|解释一?下?|说说|聊聊)[「『]?(.+?)[」』]?$", Intent.KNOWLEDGE, 0.85, 2),
    (r"^(知道|了解)(?!最近|一下)\s*(.+?)$", Intent.KNOWLEDGE, 0.7, 2),
    (r"(知道|了解).+?[关于]?(扩散|VAE|scGPT|年龄|衰老|AMP|抗菌肽|鲍曼|因果|批次校正|XGBoost)", Intent.KNOWLEDGE, 0.9, None),
    (r"(know about|what is|tell me about|explain)\s+(.+)", Intent.KNOWLEDGE, 0.9, 2),
    (r"(区别|对比|比较).+?(和|与|vs)", Intent.KNOWLEDGE, 0.8, None),
    
    # Direction change
    (r"(暂停|停止|先不做|搁置)[「『]?(.+?)[」』]?(，|,|。|然后|集中|重点|转|去做)", Intent.DIRECTION, 0.95, 2),
    (r"(集中|重点|优先|focus|switch).+?(做|研究|探索)[「『]?(.+?)[」』]?$", Intent.DIRECTION, 0.9, 3),
    (r"(切换到?|转到?|去做)[「『]?(.+?)[」』]?$", Intent.DIRECTION, 0.85, 2),
    (r"(pause|stop|focus on|switch to|prioritize)\s+(.+)", Intent.DIRECTION, 0.9, 2),
    
    # Detail queries
    (r"(详细说说|具体讲讲|展开讲讲|深入了解|详细说|具体说|展开说|再说说|多说说)[「『]?(.+?)[」』?？]?$", Intent.DETAIL, 0.95, 2),
    (r"(详细|具体|深入|展开|再多说说|说详细点)[地说一讲聊]?\s*[「『]?(.+?)[」』?？]?$", Intent.DETAIL, 0.9, 2),
    (r"(more about|elaborate|details? on|deep dive)\s+(.+)", Intent.DETAIL, 0.9, 2),

    # Workspace organization (non-destructive)
    (r"(整理|重组|重构|重新组织|清理|归档)[一二下]?(workspace|工作区|文件|项目|目录|文件夹)", Intent.WORKSPACE, 0.95, None),
    (r"(organize|restructure|clean up|tidy)\s+(workspace|files|projects)", Intent.WORKSPACE, 0.9, None),
    
    # Task management
    (r"(添加|新建|增加|add)\s*(一个)?\s*任务[：:]?\s*(.+)", Intent.TASK_ADD, 0.95, 3),
    (r"(去研究|去搜索|去查|帮我查|帮我研究)[一下]?\s*(.+)", Intent.TASK_ADD, 0.85, 2),
    (r"(取消|删除|不要了)\s*(任务)?\s*[「『]?(.+?)[」』]?$", Intent.TASK_CANCEL, 0.9, 3),
    
    # Help
    (r"^(帮助|help|你(能|会|可以)做什么|\?|？)$", Intent.HELP, 0.99, None),
]


class ConversationRouter:
    """Routes user queries to appropriate handlers using intent classification.
    
    Usage:
        router = ConversationRouter(journal, knowledge, task_queue, state)
        response = router.route("最近在研究什么？")
    """
    
    def __init__(self, journal, knowledge, task_queue, state):
        self.journal = journal
        self.knowledge = knowledge
        self.task_queue = task_queue
        self.state = state
        self._handlers: Dict[Intent, Callable] = {
            Intent.STATUS: self._handle_status,
            Intent.PROGRESS: self._handle_progress,
            Intent.KNOWLEDGE: self._handle_knowledge,
            Intent.DIRECTION: self._handle_direction,
            Intent.DETAIL: self._handle_detail,
            Intent.TASK_ADD: self._handle_task_add,
            Intent.TASK_CANCEL: self._handle_task_cancel,
            Intent.WORKSPACE: self._handle_workspace,
            Intent.HELP: self._handle_help,
            Intent.GENERAL: self._handle_general,
        }
    
    def route(self, query: str) -> str:
        """Main entry point: classify intent and route to handler."""
        parsed = self.parse_intent(query)
        handler = self._handlers.get(parsed.intent, self._handle_general)
        return handler(parsed)
    
    def parse_intent(self, query: str) -> ParsedQuery:
        """Parse user query into structured intent."""
        query_stripped = query.strip()
        
        best_match: Optional[ParsedQuery] = None
        best_confidence = 0.0
        
        for pattern, intent, confidence, topic_group in INTENT_RULES:
            match = re.search(pattern, query_stripped, re.IGNORECASE)
            if match and confidence > best_confidence:
                topic = match.group(topic_group) if topic_group and topic_group <= len(match.groups()) else None
                best_match = ParsedQuery(
                    intent=intent,
                    confidence=confidence,
                    query=query_stripped,
                    topic=topic.strip() if topic else None,
                )
                best_confidence = confidence
        
        if best_match:
            return best_match
        
        # Fallback: check for knowledge-related keywords
        knowledge_keywords = ["知道", "了解", "知识", "发现", "什么是", "怎么", "区别", "对比",
                              "learned", "know about", "what is"]
        if any(k in query_stripped.lower() for k in knowledge_keywords):
            return ParsedQuery(intent=Intent.KNOWLEDGE, confidence=0.6, query=query_stripped)
        
        return ParsedQuery(intent=Intent.GENERAL, confidence=0.5, query=query_stripped)
    
    # ==================== Handlers ====================
    
    def _handle_status(self, parsed: ParsedQuery) -> str:
        """Generate status report: recent activities, knowledge, stats."""
        lines = ["📊 我最近的研究进展：\n"]
        
        # Stats
        stats = self.state.load_stats()
        cycles = stats.get("total_cycles", 0)
        completed = stats.get("total_tasks_completed", 0)
        kb_stats = self.knowledge.stats()
        
        lines.append(f"  ⏱  完成了 {cycles} 个研究周期")
        lines.append(f"  📋 完成了 {completed} 个任务")
        lines.append(f"  📚 积累了 {kb_stats['total']} 条知识")
        
        if kb_stats.get("by_category"):
            cats = ", ".join(f"{k}:{v}" for k, v in kb_stats["by_category"].items())
            lines.append(f"     分类: {cats}")
        lines.append("")
        
        # Recent journal entries
        recent = self.journal.get_recent(5)
        if recent:
            lines.append("📖 最近活动：")
            for i, e in enumerate(recent, 1):
                ts = e.timestamp[:16].replace("T", " ")
                lines.append(f"  {i}. [{ts}] {e.task_title}")
                if e.result_summary:
                    summary = e.result_summary[:150] + "..." if len(e.result_summary) > 150 else e.result_summary
                    lines.append(f"     → {summary}")
            lines.append("")
        
        # Key recent knowledge
        recent_kb = self.knowledge.get_recent(3)
        if recent_kb:
            lines.append("🔑 最近重要发现：")
            for e in recent_kb:
                lines.append(f"  • [{e.confidence}] {e.title}")
            lines.append("")
        
        # Pending tasks count
        pending = [t for t in self.task_queue.tasks if t.status == "pending"]
        if pending:
            lines.append(f"🎯 还有 {len(pending)} 个任务待探索")
        
        return "\n".join(lines)
    
    def _handle_progress(self, parsed: ParsedQuery) -> str:
        """Show task queue status with top pending tasks."""
        stats = self.task_queue.stats()
        lines = [f"📋 任务队列：共 {stats['total']} 个任务\n"]
        for status, count in stats.get("by_status", {}).items():
            emoji = {"pending": "⏳", "completed": "✅", "failed": "❌", "in_progress": "🔄"}.get(status, "•")
            lines.append(f"  {emoji} {status}: {count}")
        
        # Show top 5 pending by priority
        pending = [t for t in self.task_queue.tasks if t.status == "pending"]
        pending.sort(key=lambda t: -t.priority)
        if pending:
            lines.append(f"\n⏳ 待执行（按优先级）：")
            for t in pending[:5]:
                lines.append(f"  [{t.priority:>2}] {t.title}")
                lines.append(f"       类型: {t.type} | 标签: {', '.join(t.tags[:3])}")
        
        return "\n".join(lines)
    
    def _handle_knowledge(self, parsed: ParsedQuery) -> str:
        """Search knowledge base and present findings."""
        query = parsed.topic or parsed.query
        results = self.knowledge.search(query, top_k=5)
        
        if not results:
            return (f"🔍 关于「{query}」我在知识库中没有找到相关内容。\n\n"
                    f"你可以让我去研究这个方向：\n"
                    f"  「去研究 {query}」")
        
        lines = [f"🔍 关于「{query}」我了解到：\n"]
        for i, e in enumerate(results, 1):
            lines.append(f"  {i}. 【{e.category}】{e.title}")
            lines.append(f"     来源: {e.source}")
            lines.append(f"     置信度: {e.confidence}")
            content_preview = e.content[:300] + "..." if len(e.content) > 300 else e.content
            # Indent content for readability
            for content_line in content_preview.split("\n"):
                lines.append(f"     {content_line}")
            if e.related_projects:
                lines.append(f"     相关项目: {', '.join(e.related_projects)}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _handle_direction(self, parsed: ParsedQuery) -> str:
        """Handle research direction changes: pause, prioritize, switch."""
        topic = parsed.topic or ""
        
        # Count affected tasks
        pending = [t for t in self.task_queue.tasks if t.status == "pending"]
        related = [t for t in pending if topic and any(
            topic.lower() in tag.lower() or topic.lower() in t.title.lower()
            for tag in t.tags
        )]
        
        lines = [f"🧭 收到方向调整指令！\n"]
        
        if topic and related:
            lines.append(f"找到 {len(related)} 个与「{topic}」相关的待执行任务：")
            for t in related:
                lines.append(f"  • [{t.priority}] {t.title}")
            lines.append(f"\n我会调整这些任务的优先级。")
        elif topic:
            lines.append(f"当前队列中没有直接匹配「{topic}」的任务。")
            lines.append(f"你想要：")
            lines.append(f"  1. 添加一个关于「{topic}」的新任务？")
            lines.append(f"  2. 提升某些现有任务的优先级？")
        else:
            lines.append(f"当前有 {len(pending)} 个待执行任务。")
            lines.append(f"请告诉我你想重点研究什么方向。")
        
        return "\n".join(lines)
    
    def _handle_detail(self, parsed: ParsedQuery) -> str:
        """Provide detailed information about a topic."""
        topic = parsed.topic or ""
        
        if not topic:
            return "请告诉我你想详细了解什么。比如：「详细说说扩散模型」"
        
        # Search knowledge for the topic
        results = self.knowledge.search(topic, top_k=3)
        
        if not results:
            return (f"关于「{topic}」我目前没有详细的知识条目。\n\n"
                    f"你可以让我深入研究：\n"
                    f"  「去研究 {topic}」")
        
        # Find the most relevant entry
        best = results[0]
        lines = [f"📖 关于「{topic}」的详细信息：\n"]
        lines.append(f"  标题: {best.title}")
        lines.append(f"  分类: {best.category}")
        lines.append(f"  来源: {best.source}")
        lines.append(f"  置信度: {best.confidence}")
        lines.append(f"  创建时间: {best.created_at[:10]}")
        lines.append("")
        lines.append("  内容：")
        for content_line in best.content.split("\n"):
            lines.append(f"  {content_line}")
        
        if best.related_projects:
            lines.append(f"\n  相关项目: {', '.join(best.related_projects)}")
        if best.tags:
            lines.append(f"  标签: {', '.join(best.tags)}")
        
        # Show related entries
        if len(results) > 1:
            lines.append(f"\n  相关知识条目：")
            for e in results[1:]:
                lines.append(f"    • [{e.confidence}] {e.title}")
        
        return "\n".join(lines)
    
    def _handle_task_add(self, parsed: ParsedQuery) -> str:
        """Add a new research task based on user request."""
        topic = parsed.topic or parsed.query
        
        # Create a task
        from .task_queue import Task
        task = Task(
            type="deep_dive",
            title=f"用户请求：研究 {topic}",
            description=f"用户通过对话请求研究此方向。原始查询：{parsed.query}",
            priority=8,  # User-requested tasks get high priority
            tags=["user_request"],
        )
        task_id = self.task_queue.add_task(task)
        
        return (f"✅ 已添加新任务！\n\n"
                f"  任务ID: {task_id}\n"
                f"  标题: {task.title}\n"
                f"  优先级: {task.priority}\n\n"
                f"我会在下一个研究周期开始处理这个任务。")
    
    def _handle_task_cancel(self, parsed: ParsedQuery) -> str:
        """Cancel a task based on user request."""
        topic = parsed.topic or ""
        
        if not topic:
            return "请告诉我要取消哪个任务。比如：「取消任务 关于因果推断的研究」"
        
        # Find matching pending tasks
        pending = [t for t in self.task_queue.tasks if t.status == "pending"]
        matching = [t for t in pending if topic.lower() in t.title.lower()]
        
        if not matching:
            return f"没有找到匹配「{topic}」的待执行任务。"
        
        # Cancel the first match
        target = matching[0]
        target.status = "cancelled"
        self.task_queue.save()
        
        return (f"❌ 已取消任务：\n\n"
                f"  {target.title}\n"
                f"  (原优先级: {target.priority})")
    
    def _handle_help(self, parsed: ParsedQuery) -> str:
        """Show help information."""
        return ("我是 Partner，你的 AI 科研伙伴。\n\n"
                "你可以和我说：\n"
                "- 「最近在研究什么」-> 看进展\n"
                "- 「关于 X 你知道什么」-> 搜知识\n"
                "- 「去研究 X」-> 加新任务\n"
                "- 「继续推进 X」-> 直接执行\n"
                "- 「帮助」-> 看这个\n\n"
                "我在后台自己跑，不用一直盯着。有结果了会通知你。")
    
    def _handle_general(self, parsed: ParsedQuery) -> str:
        """Handle general/unrecognized queries."""
        return (f"收到。我会把「{parsed.query[:50]}」纳入考虑。\n\n"
                f"你可以问我：\n"
                f"  • 「最近在研究什么？」 — 查看进展\n"
                f"  • 「关于 X 你知道什么？」 — 搜索知识\n"
                f"  • 「去研究 X」 — 添加新任务\n"
                f"  • 「整理 workspace」 — 整理工作区文件\n"
                f"  • 「帮助」 — 查看所有命令")

    def _handle_workspace(self, parsed: ParsedQuery) -> str:
        """Handle workspace organization request."""
        try:
            from .workspace_manager import migrate_workspace, detect_projects
            ws = self.state.workspace if hasattr(self.state, 'workspace') else None
            if not ws:
                return "无法找到工作区路径"
            projects = detect_projects(ws)
            actions = migrate_workspace(ws)
            lines = [f"📁 workspace 整理完成！不删除任何内容。\n"]
            lines.append(f"发现 {len(projects)} 个项目:")
            for k in sorted(projects):
                lines.append(f"  • {k}")
            if actions:
                lines.append(f"\n本次操作 ({len(actions)} 项):")
                for a in actions[:8]:
                    lines.append(f"  {a}")
                if len(actions) > 8:
                    lines.append(f"  ...还有 {len(actions)-8} 项")
            else:
                lines.append("\n无需调整，结构已是最新。")
            return "\n".join(lines)
        except Exception as e:
            return f"整理时出错: {e}"
